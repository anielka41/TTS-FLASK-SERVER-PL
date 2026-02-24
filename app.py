#!/usr/bin/env python3
"""
app_flask.py – Flask backend for Chatterbox TTS Server.
Serves the Polish interface and implements all /api/* endpoints.
Uses existing engine.py, config.py, utils.py without modifications.
Persistence via SQLite (database.py).
"""

import gc
import os
import io
import re
import json
import uuid
import time
import base64
import shutil
import logging
import threading
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

import numpy as np
import torch
import soundfile as sf

from flask import (
    Flask, request, jsonify, send_file, send_from_directory, abort
)

# --- Internal Project Imports ---
from config import (
    config_manager,
    get_host,
    get_port,
    get_output_path,
    get_reference_audio_path,
    get_gen_default_temperature,
    get_gen_default_exaggeration,
    get_gen_default_cfg_weight,
    get_gen_default_seed,
    get_gen_default_language,
    get_audio_sample_rate,
    get_full_config_for_template,
)
import engine
import utils
import database as db

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("app_flask")

# --- Flask App ---
BASE_DIR = Path(__file__).parent
FLASK_APP_DIR = BASE_DIR / "flask_app"

app = Flask(
    __name__,
    static_folder=str(FLASK_APP_DIR / "static"),
    static_url_path="/static",
    template_folder=str(FLASK_APP_DIR / "templates"),
)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

# --- Persistent Data ---
VOICE_METADATA_FILE = BASE_DIR / "voice_metadata.json"
JOBS_DIR = get_output_path(ensure_absolute=True)

# --- Job runtime flags (thread control, not persisted) ---
job_flags: Dict[str, Dict[str, bool]] = {}
job_flags_lock = threading.Lock()


# ============================================================
# Helper: Voice metadata persistence
# ============================================================
def _load_voice_metadata() -> Dict[str, Dict[str, Any]]:
    if VOICE_METADATA_FILE.exists():
        try:
            return json.loads(VOICE_METADATA_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_voice_metadata(meta: Dict[str, Dict[str, Any]]):
    VOICE_METADATA_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


def _get_audio_duration(filepath: Path) -> float:
    try:
        info = sf.info(str(filepath))
        return round(info.duration, 1)
    except Exception:
        return 0.0


def _list_voices() -> List[Dict[str, Any]]:
    ref_path = get_reference_audio_path(ensure_absolute=True)
    ref_path.mkdir(parents=True, exist_ok=True)
    meta = _load_voice_metadata()
    voices = []
    for f in sorted(ref_path.iterdir()):
        if f.suffix.lower() in (".wav", ".mp3"):
            vm = meta.get(f.name, {})
            dur = _get_audio_duration(f)
            voices.append({
                "id": f.stem,
                "file_name": f.name,
                "name": vm.get("name", f.stem.replace("_", " ").replace("-", " ").title()),
                "gender": vm.get("gender", "unknown"),
                "language": vm.get("language", "pl"),
                "description": vm.get("description", ""),
                "duration_seconds": dur,
                "is_valid_prompt": dur >= 5.0,
            })
    return voices


# ============================================================
# Helper: Text analysis
# ============================================================
SPEAKER_TAG_RE = re.compile(r"\[(\w[\w-]*)\](.*?)\[/\1\]", re.DOTALL)


def _analyze_text(text: str, custom_heading: Optional[str] = None,
                  chunk_size: int = 450) -> Dict[str, Any]:
    matches = SPEAKER_TAG_RE.findall(text)
    speakers = list(dict.fromkeys(m[0] for m in matches)) if matches else []
    clean = re.sub(r"\[\/?\w[\w-]*\]", "", text).strip()
    words = len(clean.split()) if clean else 0
    chapter_pattern = custom_heading if custom_heading else r"Rozdział|Chapter|Odcinek|Tom"
    chapters = len(re.findall(
        rf"^(?:{chapter_pattern})\s+\S+", text, re.MULTILINE | re.IGNORECASE
    ))
    total_chars = len(clean)
    total_chunks = max(1, (total_chars + chunk_size - 1) // chunk_size)
    estimated_duration = (words / 150) * 60 if words > 0 else 0
    return {
        "success": True,
        "speakers": speakers,
        "speaker_count": max(len(speakers), 1),
        "total_chunks": total_chunks,
        "word_count": words,
        "estimated_duration": round(estimated_duration, 1),
        "chapter_count": chapters,
    }


# ============================================================
# Helper: Audio encoding
# ============================================================
def _encode_audio_to_format(audio_np: np.ndarray, sr: int,
                            fmt: str = "mp3", bitrate_kbps: int = 128) -> bytes:
    buf = io.BytesIO()
    if fmt == "wav":
        sf.write(buf, audio_np, sr, format="WAV", subtype="PCM_16")
    elif fmt == "ogg":
        sf.write(buf, audio_np, sr, format="OGG", subtype="VORBIS")
    elif fmt == "mp3":
        try:
            from pydub import AudioSegment
            wav_buf = io.BytesIO()
            sf.write(wav_buf, audio_np, sr, format="WAV", subtype="PCM_16")
            wav_buf.seek(0)
            seg = AudioSegment.from_wav(wav_buf)
            seg.export(buf, format="mp3", bitrate=f"{bitrate_kbps}k")
        except ImportError:
            logger.warning("pydub not available, saving as WAV instead of MP3")
            sf.write(buf, audio_np, sr, format="WAV", subtype="PCM_16")
    else:
        sf.write(buf, audio_np, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


# ============================================================
# Job Processing Worker
# ============================================================
def _process_job(job_id: str):
    """Worker thread for processing a TTS generation job."""
    job = db.db_get_job(job_id)
    if not job:
        return

    try:
        db.db_update_job(job_id, status="processing", started_at=datetime.utcnow().isoformat())

        text = job["text"]
        voice_assignments = job.get("voice_assignments", {})
        output_format = job.get("output_format", "mp3")
        output_bitrate = job.get("output_bitrate_kbps", 128)
        chapters = job.get("chapters", [])
        total_chapters = len(chapters) if chapters else 1

        # Apply dictionary
        text = db.db_apply_dictionary(text)
        if chapters:
            chapters = [db.db_apply_dictionary(ch) for ch in chapters]

        # If no explicit chapters, treat the whole text as one chapter
        if not chapters:
            chapters = [text]

        db.db_update_job(job_id, total_chapters=total_chapters)

        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        all_output_files = []

        for ch_idx, chapter_text in enumerate(chapters):
            # Check cancellation
            with job_flags_lock:
                flags = job_flags.get(job_id, {})
            if flags.get("_cancel"):
                db.db_update_job(job_id, status="cancelled")
                return

            db.db_update_job(job_id, current_chapter=ch_idx + 1, status="processing")

            # Parse speaker segments
            segments = []
            pos = 0
            for m in SPEAKER_TAG_RE.finditer(chapter_text):
                if m.start() > pos:
                    before = chapter_text[pos:m.start()].strip()
                    if before:
                        segments.append(("default", before))
                segments.append((m.group(1), m.group(2).strip()))
                pos = m.end()
            remaining = chapter_text[pos:].strip()
            if remaining:
                segments.append(("default", remaining))
            if not segments:
                segments = [("default", re.sub(r"\[\/?\w[\w-]*\]", "", chapter_text).strip())]

            # Chunk each segment
            chunk_size = config_manager.get_int("generation_defaults.chunk_size", 450)
            all_chunks = []
            for speaker, seg_text in segments:
                for chunk in utils.chunk_text_by_sentences(seg_text, chunk_size):
                    all_chunks.append((speaker, chunk))

            total_chunks = len(all_chunks)
            if total_chunks == 0:
                continue

            db.db_update_job(job_id, total_chunks=total_chunks, current_chunk=0)

            audio_parts = []
            sr = get_audio_sample_rate()

            for i, (speaker, chunk_text) in enumerate(all_chunks):
                # Check cancellation
                with job_flags_lock:
                    flags = job_flags.get(job_id, {})
                if flags.get("_cancel"):
                    db.db_update_job(job_id, status="cancelled")
                    return

                # Check pause
                while True:
                    with job_flags_lock:
                        flags = job_flags.get(job_id, {})
                    if not flags.get("_paused"):
                        break
                    db.db_update_job(job_id, status="paused")
                    time.sleep(0.5)

                db.db_update_job(job_id, status="processing", current_chunk=i + 1)

                va = voice_assignments.get(speaker, {})
                audio_prompt = va.get("audio_prompt_path", va.get("voice", None))
                lang_code = va.get("lang_code", get_gen_default_language())

                prompt_path = None
                if audio_prompt:
                    ref_dir = get_reference_audio_path(ensure_absolute=True)
                    candidate = ref_dir / audio_prompt
                    if candidate.exists():
                        prompt_path = str(candidate)

                wav_tensor, sample_rate = engine.synthesize(
                    text=chunk_text,
                    audio_prompt_path=prompt_path,
                    temperature=get_gen_default_temperature(),
                    exaggeration=get_gen_default_exaggeration(),
                    cfg_weight=get_gen_default_cfg_weight(),
                    seed=get_gen_default_seed(),
                    language=lang_code,
                )

                if wav_tensor is not None:
                    audio_np = wav_tensor.squeeze().cpu().numpy()
                    sr = sample_rate or sr
                    audio_parts.append(audio_np)

                # Update progress
                overall = int(((ch_idx * total_chunks + i + 1) / (total_chapters * max(total_chunks, 1))) * 100)
                db.db_update_job(job_id, progress=min(overall, 99))

            if audio_parts:
                full_audio = np.concatenate(audio_parts)
                ext = output_format if output_format in ("mp3", "wav", "ogg") else "wav"
                # File name: chapter_number.format
                output_filename = f"{ch_idx + 1}.{ext}"
                output_path = job_dir / output_filename
                audio_bytes = _encode_audio_to_format(full_audio, sr, output_format, output_bitrate)
                output_path.write_bytes(audio_bytes)
                all_output_files.append(f"/outputs/{job_id}/{output_filename}")

        if not all_output_files:
            db.db_update_job(job_id, status="failed", error="Synteza nie wygenerowała żadnego audio")
            return

        db.db_update_job(
            job_id,
            output_files=all_output_files,
            status="completed",
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
        )

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        db.db_update_job(job_id, status="failed", error=str(e))
    finally:
        # VRAM cleanup
        if config_manager.get_bool("audio_output.cleanup_vram_after_job", False):
            logger.info(f"Job {job_id}: cleaning up VRAM...")
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                try:
                    torch.mps.empty_cache()
                except AttributeError:
                    pass
        # Clean up flags
        with job_flags_lock:
            job_flags.pop(job_id, None)


# ============================================================
# Routes: UI
# ============================================================
@app.route("/")
def index():
    return send_file(str(FLASK_APP_DIR / "templates" / "index.html"))


# ============================================================
# Routes: API - Model Info & Restart
# ============================================================
@app.route("/api/model-info", methods=["GET"])
def api_model_info():
    info = engine.get_model_info()
    return jsonify({"success": True, **info})


@app.route("/api/restart-server", methods=["POST"])
def api_restart_server():
    logger.info("Request received for /api/restart-server (Model Hot-Swap).")
    try:
        success = engine.reload_model()
        if success:
            info = engine.get_model_info()
            return jsonify({
                "success": True,
                "message": f"Model hot-swap OK: {info.get('class_name', '?')} ({info.get('type', '?')})",
                "model_info": info,
            })
        else:
            return jsonify({"success": False, "error": "Nie udało się przeładować modelu."}), 500
    except Exception as e:
        logger.error(f"Model hot-swap failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Routes: API - Text Analysis
# ============================================================
@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"success": False, "error": "Tekst jest pusty"}), 400
    custom_heading = data.get("custom_heading")
    result = _analyze_text(text, custom_heading)
    return jsonify(result)


# ============================================================
# Routes: API - Generate
# ============================================================
@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    chapters = data.get("chapters", [])

    # If chapters provided, use them; otherwise use text
    if not chapters and not text:
        return jsonify({"success": False, "error": "Tekst jest pusty"}), 400

    job_id = str(uuid.uuid4())
    first_line = (text or (chapters[0] if chapters else "")).split("\n")[0][:50].strip()
    title = re.sub(r"\[\/?\w[\w-]*\]", "", first_line).strip() or "Bez tytułu"

    job = db.db_create_job(
        job_id=job_id,
        title=title,
        text=text,
        output_format=data.get("output_format", "mp3"),
        output_bitrate_kbps=data.get("output_bitrate_kbps", 128),
        voice_assignments=data.get("voice_assignments", {}),
        tts_engine=data.get("tts_engine", "chatterbox_mtl_local"),
        split_by_chapter=bool(chapters),
        chapters=chapters if chapters else [],
        total_chapters=len(chapters) if chapters else 1,
    )

    # Set runtime flags
    with job_flags_lock:
        job_flags[job_id] = {"_cancel": False, "_paused": False}

    # Start worker
    t = threading.Thread(target=_process_job, args=(job_id,), daemon=True)
    t.start()

    active = db.db_get_active_job_count()
    return jsonify({"success": True, "job_id": job_id, "queue_position": active})


# ============================================================
# Routes: API - Jobs
# ============================================================
@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    all_jobs = db.db_get_jobs()
    job_list = []
    for j in all_jobs:
        job_list.append({
            "job_id": j["job_id"],
            "title": j["title"],
            "status": j["status"],
            "progress": j["progress"],
            "current_chunk": j.get("current_chunk", 0),
            "total_chunks": j.get("total_chunks", 0),
            "current_chapter": j.get("current_chapter", 0),
            "total_chapters": j.get("total_chapters", 0),
            "created_at": j["created_at"],
            "completed_at": j.get("completed_at"),
            "error": j.get("error"),
            "output_files": j.get("output_files", []),
        })
    active = db.db_get_active_job_count()
    return jsonify({"success": True, "jobs": job_list, "active_count": active})


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
def api_pause_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    with job_flags_lock:
        flags = job_flags.get(job_id, {})
        flags["_paused"] = True
        job_flags[job_id] = flags
    return jsonify({"success": True})


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
def api_resume_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    with job_flags_lock:
        flags = job_flags.get(job_id, {})
        flags["_paused"] = False
        job_flags[job_id] = flags
    return jsonify({"success": True})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    with job_flags_lock:
        flags = job_flags.get(job_id, {})
        flags["_cancel"] = True
        flags["_paused"] = False
        job_flags[job_id] = flags
    return jsonify({"success": True})


@app.route("/api/jobs/<job_id>/delete", methods=["DELETE"])
def api_delete_job(job_id: str):
    deleted = db.db_delete_job(job_id)
    if not deleted:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    # Delete output files
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    with job_flags_lock:
        job_flags.pop(job_id, None)
    return jsonify({"success": True})


# ============================================================
# Routes: API - Library
# ============================================================
@app.route("/api/library", methods=["GET"])
def api_library():
    completed = db.db_get_jobs(status_filter="completed")
    lib = [{
        "job_id": j["job_id"],
        "title": j["title"],
        "created_at": j["created_at"],
        "completed_at": j.get("completed_at"),
        "output_files": j.get("output_files", []),
    } for j in completed]
    return jsonify({"success": True, "library": lib})


@app.route("/api/library/<job_id>/download", methods=["GET"])
def api_library_download(job_id: str):
    job = db.db_get_job(job_id)
    if not job or job["status"] != "completed":
        abort(404)
    output_files = job.get("output_files", [])
    if not output_files:
        abort(404)

    if len(output_files) == 1:
        # Single file — serve directly
        rel = output_files[0].lstrip("/")
        filepath = BASE_DIR / rel
        if filepath.exists():
            return send_file(str(filepath), as_attachment=True)
        abort(404)

    # Multiple files — create ZIP
    title = utils.sanitize_filename(job.get("title", "audiobook")) or "audiobook"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f_url in output_files:
            rel = f_url.lstrip("/")
            filepath = BASE_DIR / rel
            if filepath.exists():
                zf.write(str(filepath), filepath.name)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{title}.zip",
    )


@app.route("/api/library/<job_id>/title", methods=["PUT"])
def api_library_update_title(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    data = request.get_json(force=True)
    db.db_update_job(job_id, title=data.get("title", job["title"]))
    return jsonify({"success": True})


# ============================================================
# Routes: API - Chatterbox Voices
# ============================================================
@app.route("/api/chatterbox-voices", methods=["GET"])
def api_voices_list():
    voices = _list_voices()
    return jsonify({"success": True, "voices": voices})


@app.route("/api/chatterbox-voices", methods=["POST"])
def api_voices_upload():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "Brak pliku"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "Brak nazwy pliku"}), 400

    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "Nazwa głosu jest wymagana"}), 400

    gender = request.form.get("gender", "unknown")
    language = request.form.get("language", "pl")
    description = request.form.get("description", "")

    ref_path = get_reference_audio_path(ensure_absolute=True)
    ref_path.mkdir(parents=True, exist_ok=True)

    safe_name = utils.sanitize_filename(file.filename)
    dest = ref_path / safe_name
    file.save(str(dest))

    dur = _get_audio_duration(dest)
    if dur < 5.0:
        dest.unlink(missing_ok=True)
        return jsonify({"success": False, "error": f"Plik za krótki ({dur}s). Minimum to 5 sekund."}), 400

    meta = _load_voice_metadata()
    meta[safe_name] = {"name": name, "gender": gender, "language": language, "description": description}
    _save_voice_metadata(meta)

    return jsonify({"success": True, "voice_id": Path(safe_name).stem})


@app.route("/api/chatterbox-voices/<voice_id>", methods=["PUT"])
def api_voices_edit(voice_id: str):
    data = request.get_json(force=True)
    meta = _load_voice_metadata()

    ref_path = get_reference_audio_path(ensure_absolute=True)
    target_file = None
    for f in ref_path.iterdir():
        if f.stem == voice_id and f.suffix.lower() in (".wav", ".mp3"):
            target_file = f.name
            break

    if not target_file:
        return jsonify({"success": False, "error": "Głos nie znaleziony"}), 404

    entry = meta.get(target_file, {})
    for key in ("name", "gender", "language", "description"):
        if key in data:
            entry[key] = data[key]
    meta[target_file] = entry
    _save_voice_metadata(meta)

    return jsonify({"success": True})


@app.route("/api/chatterbox-voices/<voice_id>", methods=["DELETE"])
def api_voices_delete(voice_id: str):
    ref_path = get_reference_audio_path(ensure_absolute=True)
    target_file = None
    for f in ref_path.iterdir():
        if f.stem == voice_id and f.suffix.lower() in (".wav", ".mp3"):
            target_file = f
            break

    if not target_file:
        return jsonify({"success": False, "error": "Głos nie znaleziony"}), 404

    target_file.unlink(missing_ok=True)
    meta = _load_voice_metadata()
    meta.pop(target_file.name, None)
    _save_voice_metadata(meta)
    return jsonify({"success": True})


@app.route("/api/chatterbox-voices/<voice_id>/preview", methods=["GET"])
def api_voices_preview(voice_id: str):
    ref_path = get_reference_audio_path(ensure_absolute=True)
    target_file = None
    for f in ref_path.iterdir():
        if f.stem == voice_id and f.suffix.lower() in (".wav", ".mp3"):
            target_file = f
            break

    if not target_file:
        return jsonify({"success": False, "error": "Głos nie znaleziony"}), 404

    wav_tensor, sr = engine.synthesize(
        text="Witaj, to jest próbka głosu.",
        audio_prompt_path=str(target_file),
        temperature=get_gen_default_temperature(),
        exaggeration=get_gen_default_exaggeration(),
        cfg_weight=get_gen_default_cfg_weight(),
        language=get_gen_default_language(),
    )

    if wav_tensor is None:
        return jsonify({"success": False, "error": "Synteza nie powiodła się"}), 500

    audio_np = wav_tensor.squeeze().cpu().numpy()
    buf = io.BytesIO()
    sf.write(buf, audio_np, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    audio_b64 = base64.b64encode(buf.read()).decode("utf-8")
    dur = round(len(audio_np) / sr, 1)
    return jsonify({"success": True, "audio_base64": audio_b64, "duration": dur})


# ============================================================
# Routes: API - Preview (from Generate tab)
# ============================================================
@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(force=True)
    voice = data.get("voice", "")
    text = data.get("text", "Witaj, to jest próbka głosu.")
    lang_code = data.get("lang_code", get_gen_default_language())

    prompt_path = None
    if voice:
        ref_dir = get_reference_audio_path(ensure_absolute=True)
        candidate = ref_dir / voice
        if candidate.exists():
            prompt_path = str(candidate)

    wav_tensor, sr = engine.synthesize(
        text=text,
        audio_prompt_path=prompt_path,
        temperature=get_gen_default_temperature(),
        exaggeration=get_gen_default_exaggeration(),
        cfg_weight=get_gen_default_cfg_weight(),
        language=lang_code,
    )

    if wav_tensor is None:
        return jsonify({"success": False, "error": "Synteza nie powiodła się"}), 500

    audio_np = wav_tensor.squeeze().cpu().numpy()
    buf = io.BytesIO()
    sf.write(buf, audio_np, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    audio_b64 = base64.b64encode(buf.read()).decode("utf-8")
    dur = round(len(audio_np) / sr, 1)
    return jsonify({"success": True, "audio_base64": audio_b64, "duration": dur})


# ============================================================
# Routes: API - Dictionary (SQLite)
# ============================================================
@app.route("/api/dictionary", methods=["GET"])
def api_dictionary_get():
    d = db.db_get_dictionary()
    entries = [{"word": k, "replacement": v} for k, v in d.items()]
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "entries": entries, "count": count})


@app.route("/api/dictionary", methods=["POST"])
def api_dictionary_add():
    data = request.get_json(force=True)
    word = data.get("word", "").strip()
    replacement = data.get("replacement", "").strip()
    if not word:
        return jsonify({"success": False, "error": "Słowo jest wymagane"}), 400
    db.db_add_word(word, replacement)
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "count": count})


@app.route("/api/dictionary/<path:word>", methods=["DELETE"])
def api_dictionary_delete(word: str):
    db.db_delete_word(word)
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "count": count})


@app.route("/api/dictionary", methods=["DELETE"])
def api_dictionary_clear():
    db.db_clear_dictionary()
    return jsonify({"success": True, "count": 0})


@app.route("/api/dictionary/import", methods=["POST"])
def api_dictionary_import():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Oczekiwano obiektu JSON"}), 400
    db.db_import_dictionary(data)
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "count": count})


# ============================================================
# Routes: API - Convert (Text through Dictionary)
# ============================================================
@app.route("/api/convert", methods=["POST"])
def api_convert():
    data = request.get_json(force=True)
    text = data.get("text", "")
    if not text:
        return jsonify({"success": True, "text": ""})
    converted = db.db_apply_dictionary(text)
    return jsonify({"success": True, "text": converted})


# ============================================================
# Routes: API - Settings
# ============================================================
@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    cfg = get_full_config_for_template()
    settings = {
        "output_format": cfg.get("audio_output", {}).get("format", "wav"),
        "output_bitrate_kbps": 128,
        "crossfade_duration": cfg.get("audio_output", {}).get("crossfade_duration", 0.1),
        "intro_silence_ms": cfg.get("audio_output", {}).get("intro_silence_ms", 0),
        "inter_chunk_silence_ms": cfg.get("audio_output", {}).get("inter_chunk_silence_ms", 0),
        "group_chunks_by_speaker": cfg.get("audio_output", {}).get("group_chunks_by_speaker", False),
        "cleanup_vram_after_job": cfg.get("audio_output", {}).get("cleanup_vram_after_job", False),
        "chatterbox_mtl_local_default_language": cfg.get("generation_defaults", {}).get("language", "pl"),
        "chatterbox_mtl_local_device": cfg.get("tts_engine", {}).get("device", "auto"),
        "chatterbox_mtl_local_default_prompt": cfg.get("tts_engine", {}).get("default_voice_id", ""),
        "chatterbox_mtl_local_chunk_size": cfg.get("generation_defaults", {}).get("chunk_size", 450),
        "chatterbox_mtl_local_temperature": cfg.get("generation_defaults", {}).get("temperature", 0.8),
        "chatterbox_mtl_local_exaggeration": cfg.get("generation_defaults", {}).get("exaggeration", 0.5),
        "chatterbox_mtl_local_cfg_weight": cfg.get("generation_defaults", {}).get("cfg_weight", 0.5),
        "chatterbox_mtl_local_seed": cfg.get("generation_defaults", {}).get("seed", 0),
        "model_repo_id": cfg.get("model", {}).get("repo_id", "chatterbox-multilingual"),
    }
    return jsonify({"success": True, "settings": settings})


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    data = request.get_json(force=True)
    update: Dict[str, Any] = {}

    # Audio output
    audio = {}
    if "output_format" in data:
        audio["format"] = data["output_format"]
    if "crossfade_duration" in data:
        audio["crossfade_duration"] = float(data["crossfade_duration"])
    if "intro_silence_ms" in data:
        audio["intro_silence_ms"] = int(data["intro_silence_ms"])
    if "inter_chunk_silence_ms" in data:
        audio["inter_chunk_silence_ms"] = int(data["inter_chunk_silence_ms"])
    if "group_chunks_by_speaker" in data:
        audio["group_chunks_by_speaker"] = data["group_chunks_by_speaker"]
    if "cleanup_vram_after_job" in data:
        audio["cleanup_vram_after_job"] = data["cleanup_vram_after_job"]
    if audio:
        update["audio_output"] = audio

    # Generation defaults
    gen = {}
    if "chatterbox_mtl_local_default_language" in data:
        gen["language"] = data["chatterbox_mtl_local_default_language"]
    if "chatterbox_mtl_local_chunk_size" in data:
        gen["chunk_size"] = int(data["chatterbox_mtl_local_chunk_size"])
    if "chatterbox_mtl_local_temperature" in data:
        gen["temperature"] = float(data["chatterbox_mtl_local_temperature"])
    if "chatterbox_mtl_local_exaggeration" in data:
        gen["exaggeration"] = float(data["chatterbox_mtl_local_exaggeration"])
    if "chatterbox_mtl_local_cfg_weight" in data:
        gen["cfg_weight"] = float(data["chatterbox_mtl_local_cfg_weight"])
    if "chatterbox_mtl_local_seed" in data:
        gen["seed"] = int(data["chatterbox_mtl_local_seed"])
    if gen:
        update["generation_defaults"] = gen

    # TTS Engine
    eng = {}
    if "chatterbox_mtl_local_device" in data:
        eng["device"] = data["chatterbox_mtl_local_device"]
    if "chatterbox_mtl_local_default_prompt" in data:
        eng["default_voice_id"] = data["chatterbox_mtl_local_default_prompt"]
    if eng:
        update["tts_engine"] = eng

    # Model
    if "model_repo_id" in data:
        update["model"] = {"repo_id": data["model_repo_id"]}

    if update:
        success = config_manager.update_and_save(update)
        if success:
            return jsonify({"success": True, "message": "Ustawienia zapisane"})
        else:
            return jsonify({"success": False, "error": "Nie udało się zapisać ustawień"}), 500

    return jsonify({"success": True, "message": "Brak zmian"})


# ============================================================
# Routes: API - Upload Document
# ============================================================
@app.route("/api/upload-document", methods=["POST"])
def api_upload_document():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "Brak pliku"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "Brak nazwy pliku"}), 400

    filename = file.filename.lower()
    text = ""

    try:
        if filename.endswith(".txt") or filename.endswith(".md"):
            text = file.read().decode("utf-8", errors="replace")
        elif filename.endswith(".html") or filename.endswith(".htm"):
            raw = file.read().decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", "", raw)
            text = re.sub(r"\s+", " ", text).strip()
        elif filename.endswith(".pdf"):
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(file.stream)
                pages = [page.extract_text() or "" for page in reader.pages]
                text = "\n\n".join(pages)
            except ImportError:
                return jsonify({"success": False, "error": "PyPDF2 nie jest zainstalowany"}), 500
        elif filename.endswith(".docx"):
            try:
                import docx
                doc = docx.Document(file.stream)
                text = "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return jsonify({"success": False, "error": "python-docx nie jest zainstalowany"}), 500
        elif filename.endswith(".epub"):
            try:
                import ebooklib
                from ebooklib import epub
                from bs4 import BeautifulSoup
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
                    file.save(tmp.name)
                    book = epub.read_epub(tmp.name)
                parts = []
                for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    soup = BeautifulSoup(item.get_content(), "html.parser")
                    parts.append(soup.get_text(separator="\n"))
                text = "\n\n".join(parts)
                os.unlink(tmp.name)
            except ImportError:
                return jsonify({"success": False, "error": "ebooklib i beautifulsoup4 nie są zainstalowane"}), 500
        else:
            text = file.read().decode("utf-8", errors="replace")

        return jsonify({"success": True, "text": text.strip(), "filename": file.filename})
    except Exception as e:
        return jsonify({"success": False, "error": f"Błąd parsowania: {str(e)}"}), 500


# ============================================================
# Routes: Serve output files
# ============================================================
@app.route("/outputs/<path:filepath>")
def serve_output(filepath: str):
    return send_from_directory(str(JOBS_DIR), filepath)


# ============================================================
# Main Entry Point
# ============================================================
def _load_engine():
    logger.info("Loading TTS engine...")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    get_reference_audio_path(ensure_absolute=True).mkdir(parents=True, exist_ok=True)
    db.init_db()
    logger.info("SQLite database initialized.")

    if not engine.load_model():
        logger.error("CRITICAL: TTS Model failed to load!")
    else:
        logger.info("TTS Model loaded successfully.")


if __name__ == "__main__":
    _load_engine()

    host = get_host()
    port = get_port()
    logger.info(f"Starting Chatterbox Flask PL on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)

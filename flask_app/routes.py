import os
import io
import json
import uuid
import base64
import shutil
import zipfile
import threading
import logging
from pathlib import Path
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file, send_from_directory, abort

from config import config_manager, get_output_path, get_audio_sample_rate
import re
import math
import utils
from typing import Dict, Any, Optional

from config import get_reference_audio_path, get_gen_default_temperature, get_gen_default_exaggeration, get_gen_default_cfg_weight, get_gen_default_seed, get_gen_default_language, get_full_config_for_template
import soundfile as sf
import gc
import torch

import engine
import database as db

from flask_app.helpers import _load_voice_metadata, _save_voice_metadata, _list_voices, _analyze_text, _get_audio_duration
from flask_app.worker import _process_chapter, JOBS_DIR

logger = logging.getLogger("flask_app.routes")

main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__, url_prefix='/api')

FLASK_APP_DIR = Path(__file__).parent

# ============================================================
# Routes: UI
# ============================================================
@main_bp.route("/")
def index():
    return send_file(str(FLASK_APP_DIR / "templates" / "index.html"))


# ============================================================
# Routes: API - Model Info & Restart
# ============================================================
@api_bp.route("/model-info", methods=["GET"])
def api_model_info():
    info = engine.get_model_info()
    return jsonify({"success": True, **info})


@api_bp.route("/restart-server", methods=["POST"])
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
@api_bp.route("/analyze", methods=["POST"])
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
@api_bp.route("/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    chapters = data.get("chapters", [])

    # If chapters provided, use them; otherwise use text
    if not chapters and not text:
        return jsonify({"success": False, "error": "Tekst jest pusty"}), 400

    job_id = str(uuid.uuid4())
    title = data.get("title", "").strip()
    if not title:
        first_line = (text or (chapters[0] if chapters else "")).split("\n")[0][:50].strip()
        title = re.sub(r"\[\/?\w[\w-]*\]", "", first_line).strip() or "Brak nazwy projektu"

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

    from redis import Redis
    from rq import Queue
    import os
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    q = Queue('chapters', connection=Redis.from_url(redis_url))

    # Send to RQ per chapter
    for ch_idx in range(len(chapters) if chapters else 1):
        q.enqueue("flask_app.worker._process_chapter", job_id, ch_idx, job_timeout=3600, result_ttl=86400)

    active = db.db_get_active_job_count()
    return jsonify({"success": True, "job_id": job_id, "queue_position": active})


# ============================================================
# Routes: API - Jobs
# ============================================================
@api_bp.route("/jobs", methods=["GET"])
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
            "completed_chapters": j.get("completed_chapters", 0),
            "created_at": j["created_at"],
            "completed_at": j.get("completed_at"),
            "error": j.get("error"),
            "output_files": j.get("output_files", []),
            "worker_name": j.get("worker_name"),
            "chapter_states": db.db_get_chapter_states(j["job_id"]),
        })
    active = db.db_get_active_job_count()
    return jsonify({"success": True, "jobs": job_list, "active_count": active})


@api_bp.route("/jobs/<job_id>/pause", methods=["POST"])
def api_pause_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    db.db_update_job(job_id, status="paused")
    return jsonify({"success": True})


@api_bp.route("/jobs/<job_id>/resume", methods=["POST"])
def api_resume_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    db.db_update_job(job_id, status="processing")
    return jsonify({"success": True})


@api_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id: str):
    job = db.db_get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    db.db_update_job(job_id, status="cancelled")
    return jsonify({"success": True})


@api_bp.route("/jobs/<job_id>/delete", methods=["DELETE"])
def api_delete_job(job_id: str):
    deleted = db.db_delete_job(job_id)
    if not deleted:
        return jsonify({"success": False, "error": "Job nie znaleziony"}), 404
    # Delete output files
    from flask_app.worker import JOBS_DIR
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    return jsonify({"success": True})


# ============================================================
# Routes: API - Library
# ============================================================
@api_bp.route("/library", methods=["GET"])
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


@api_bp.route("/library/<job_id>/download", methods=["GET"])
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
        filepath = FLASK_APP_DIR.parent / rel
        if filepath.exists():
            return send_file(str(filepath), as_attachment=True)
        abort(404)

    # Multiple files — create ZIP
    title = utils.sanitize_filename(job.get("title", "audiobook")) or "audiobook"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f_url in output_files:
            rel = f_url.lstrip("/")
            filepath = FLASK_APP_DIR.parent / rel
            if filepath.exists():
                zf.write(str(filepath), filepath.name)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{title}.zip",
    )


@api_bp.route("/library/<job_id>/title", methods=["PUT"])
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
@api_bp.route("/chatterbox-voices", methods=["GET"])
def api_voices_list():
    voices = _list_voices()
    return jsonify({"success": True, "voices": voices})


@api_bp.route("/chatterbox-voices", methods=["POST"])
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


@api_bp.route("/chatterbox-voices/<voice_id>", methods=["PUT"])
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


@api_bp.route("/chatterbox-voices/<voice_id>", methods=["DELETE"])
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


@api_bp.route("/chatterbox-voices/<voice_id>/preview", methods=["GET"])
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
@api_bp.route("/preview", methods=["POST"])
def api_preview():
    data = request.get_json(force=True)
    voice = data.get("voice", "")
    text = data.get("text", "Witaj, to jest próbka głosu.")
    lang_code = data.get("lang_code", get_gen_default_language())

    if not voice:
        from config import get_default_voice_id
        voice = get_default_voice_id()

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
@api_bp.route("/dictionary", methods=["GET"])
def api_dictionary_get():
    d = db.db_get_dictionary()
    entries = [{"word": k, "replacement": v} for k, v in d.items()]
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "entries": entries, "count": count})


@api_bp.route("/dictionary", methods=["POST"])
def api_dictionary_add():
    data = request.get_json(force=True)
    word = data.get("word", "").strip()
    replacement = data.get("replacement", "").strip()
    if not word:
        return jsonify({"success": False, "error": "Słowo jest wymagane"}), 400
    db.db_add_word(word, replacement)
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "count": count})


@api_bp.route("/dictionary/<path:word>", methods=["DELETE"])
def api_dictionary_delete(word: str):
    db.db_delete_word(word)
    count = db.db_get_dictionary_count()
    return jsonify({"success": True, "count": count})


@api_bp.route("/dictionary", methods=["DELETE"])
def api_dictionary_clear():
    db.db_clear_dictionary()
    return jsonify({"success": True, "count": 0})


@api_bp.route("/dictionary/import", methods=["POST"])
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
@api_bp.route("/convert", methods=["POST"])
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
@api_bp.route("/settings", methods=["GET"])
def api_settings_get():
    cfg = get_full_config_for_template()
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
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
        "chatterbox_mtl_local_speed_factor": cfg.get("generation_defaults", {}).get("speed_factor", 1.0),
        "chatterbox_mtl_local_sentence_pause_ms": cfg.get("generation_defaults", {}).get("sentence_pause_ms", 500),
        "model_repo_id": cfg.get("model", {}).get("repo_id", "chatterbox-multilingual"),
        "num_workers": int(os.environ.get("NUM_WORKERS", 1))
    }
    return jsonify({"success": True, "settings": settings})


@api_bp.route("/settings", methods=["POST"])
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
    if "chatterbox_mtl_local_speed_factor" in data:
        gen["speed_factor"] = float(data["chatterbox_mtl_local_speed_factor"])
    if "chatterbox_mtl_local_sentence_pause_ms" in data:
        gen["sentence_pause_ms"] = int(data["chatterbox_mtl_local_sentence_pause_ms"])
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

    # Update env num_workers
    worker_success_msg = ""
    if "num_workers" in data:
        new_workers = int(data["num_workers"])
        import dotenv
        from dotenv import find_dotenv
        env_file = find_dotenv()
        if not env_file:
            env_file = os.path.join(FLASK_APP_DIR.parent, ".env")
        dotenv.set_key(env_file, "NUM_WORKERS", str(new_workers))
        os.environ["NUM_WORKERS"] = str(new_workers)
        
        # update supervisor if possible
        import subprocess
        try:
            # First, update the number of processes in the supervisor target conf if it exists
            conf_path = "/etc/supervisor/conf.d/chatterbox_workers.conf"
            if os.path.exists(conf_path):
                try:
                    with open(conf_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    with open(conf_path, "w", encoding="utf-8") as f:
                        for line in lines:
                            if line.startswith("numprocs="):
                                f.write(f"numprocs={new_workers}\n")
                            else:
                                f.write(line)
                except PermissionError:
                    worker_success_msg = " (nie udało się zapisać conf. Supervisora - brak zapisu)"
                
                # reread and update
                subprocess.run(["sudo", "-n", "supervisorctl", "reread"], check=False)
                subprocess.run(["sudo", "-n", "supervisorctl", "update", "chatterbox_workers"], check=False)
        except Exception as e:
            logger.warning(f"Could not update supervisor automatically: {e}")
        worker_success_msg = f" (Workers = {new_workers})"

    if update:
        success = config_manager.update_and_save(update)
        if success:
            return jsonify({"success": True, "message": f"Ustawienia zapisane{worker_success_msg}"})
        else:
            return jsonify({"success": False, "error": "Nie udało się zapisać ustawień"}), 500

    return jsonify({"success": True, "message": f"Brak zmian config.yaml{worker_success_msg}"})


# ============================================================
# Routes: API - Upload Document
# ============================================================
@api_bp.route("/upload-document", methods=["POST"])
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
@main_bp.route("/outputs/<path:filepath>")
def serve_output(filepath: str):
    return send_from_directory(str(JOBS_DIR), filepath)


# ============================================================
# Routes: System Status
# ============================================================
@api_bp.route("/system-status", methods=["GET"])
def api_system_status():
    status = {"redis": False, "supervisor": False, "workers": 0}
    try:
        from redis import Redis
        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        status["redis"] = r.ping()
    except Exception:
        pass

    try:
        import subprocess
        res = subprocess.run(["sudo", "-n", "supervisorctl", "status", "chatterbox_workers:"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 or res.returncode == 3: # 3 can mean not all are running or just status output
            status["supervisor"] = True
            lines = res.stdout.strip().split('\n')
            running_workers = sum(1 for line in lines if "RUNNING" in line or "STARTING" in line)
            status["workers"] = running_workers
    except Exception:
        pass

    return jsonify({"success": True, "status": status})


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



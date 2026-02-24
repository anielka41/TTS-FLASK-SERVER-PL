import time
import gc
import re
import math
import uuid
import threading
import logging
from datetime import datetime
from typing import Dict, Any
import numpy as np
import torch

from config import config_manager, get_output_path, get_reference_audio_path, get_gen_default_temperature, get_gen_default_exaggeration, get_gen_default_cfg_weight, get_gen_default_seed, get_gen_default_language, get_audio_sample_rate
import engine
import utils
import database as db

from flask_app.helpers import _encode_audio_to_format, SPEAKER_TAG_RE

logger = logging.getLogger("flask_app.worker")
JOBS_DIR = get_output_path(ensure_absolute=True)

job_flags: Dict[str, Dict[str, bool]] = {}
job_flags_lock = threading.Lock()

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

                # Fallback to default voice if none provided
                if not audio_prompt:
                    from config import get_default_voice_id
                    audio_prompt = get_default_voice_id()

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



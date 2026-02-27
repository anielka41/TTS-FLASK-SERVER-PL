import os
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
import librosa

from config import config_manager, get_output_path, get_reference_audio_path, get_gen_default_temperature, get_gen_default_exaggeration, get_gen_default_cfg_weight, get_gen_default_seed, get_gen_default_language, get_audio_sample_rate, get_gen_default_speed_factor, get_gen_default_sentence_pause_ms
import engine
import utils
import database as db

from flask_app.helpers import _encode_audio_to_format, SPEAKER_TAG_RE

logger = logging.getLogger("flask_app.worker")
JOBS_DIR = get_output_path(ensure_absolute=True)

# ============================================================
# Job Processing Worker
# ============================================================
def _process_chapter(job_id: str, ch_idx: int):
    """Worker thread for processing a SINGLE chapter of a TTS job."""
    config_manager.load_config()
    job = db.db_get_job(job_id)
    if not job:
        return

    try:
        worker_name = os.environ.get("SUPERVISOR_PROCESS_NAME", "Lokalny Worker")
        db.db_update_job(job_id, status="processing", started_at=datetime.utcnow().isoformat(), worker_name=worker_name, current_chapter=ch_idx + 1)

        text = job["text"]
        voice_assignments = job.get("voice_assignments", {})
        output_format = job.get("output_format", "mp3")
        output_bitrate = job.get("output_bitrate_kbps", 128)
        chapters = job.get("chapters", [])
        total_chapters = len(chapters) if chapters else 1

        # Apply dictionary
        if chapters:
            chapter_text = db.db_apply_dictionary(chapters[ch_idx])
        else:
            chapter_text = db.db_apply_dictionary(text)

        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        # Check cancellation
        current_job_state = db.db_get_job(job_id)
        if not current_job_state or current_job_state.get("status") == "cancelled":
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
            return

        db.db_update_job(job_id, total_chunks=total_chunks, current_chunk=0)
        db.db_update_chapter_state(job_id, ch_idx, worker_name, 0, total_chunks, "processing")

        audio_parts = []
        sr = get_audio_sample_rate()

        for i, (speaker, chunk_text) in enumerate(all_chunks):
            # Check cancellation
            current_job_state_chunk = db.db_get_job(job_id)
            if not current_job_state_chunk or current_job_state_chunk.get("status") == "cancelled":
                return

            # Check pause
            while True:
                current_job_state_pause = db.db_get_job(job_id)
                if not current_job_state_pause:
                    return
                if current_job_state_pause.get("status") != "paused":
                    break
                time.sleep(1.0)

            db.db_update_job(job_id, status="processing", current_chunk=i + 1)
            db.db_update_chapter_state(job_id, ch_idx, worker_name, i + 1, total_chunks, "processing")

            va = voice_assignments.get(speaker, {})
            audio_prompt = va.get("audio_prompt_path", va.get("voice", None))
            lang_code = va.get("lang_code", get_gen_default_language())

            # Jeżeli brak wybranego u bieżącego speakera audio_prompt to:
            #   1) upewniamy się, czy "default" z JSON'a przypadkiem go nie nadpisuje
            #   2) jeśli nie, bierzemy ostatecznie głos z konfigu globalnego.
            if not audio_prompt:
                default_va = voice_assignments.get("default", {})
                audio_prompt = default_va.get("audio_prompt_path", default_va.get("voice", None))
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

                # Apply speed factor (time-stretching) using librosa if != 1.0
                speed_factor = get_gen_default_speed_factor()
                if speed_factor and speed_factor != 1.0 and speed_factor > 0:
                    audio_np = librosa.effects.time_stretch(audio_np, rate=speed_factor)
                    
                # ----- NEW: Apply Artifact Reduction Pipeline based on mode -----
                pipeline_mode = job.get("pipeline_mode", "baseline")
                if pipeline_mode in ("test_pipeline", "tuning"):
                    try:
                        from flask_app.artifacts import apply_artifacts_pipeline
                        is_test = (pipeline_mode == "test_pipeline")
                        audio_np = apply_artifacts_pipeline(audio_np, sr, expected_text=chunk_text, is_test_mode=is_test)
                    except Exception as e:
                        logger.error(f"Failed to apply artifacts pipeline: {e}")
                # --------------------------------------------------

                audio_parts.append(audio_np)
                
                # Append sentence pause padding if there is a gap requirement
                pause_ms = get_gen_default_sentence_pause_ms()
                if pause_ms > 0:
                    pause_samples = int(sr * (pause_ms / 1000.0))
                    padding_np = np.zeros(pause_samples, dtype=audio_np.dtype)
                    audio_parts.append(padding_np)

        if audio_parts:
            full_audio = np.concatenate(audio_parts)
            ext = output_format if output_format in ("mp3", "wav", "ogg") else "wav"
            
            pipeline_mode = job.get("pipeline_mode", "baseline")
            if pipeline_mode == "test_pipeline":
                # Save to test_outputs
                from config import get_output_path
                test_dir = get_output_path(ensure_absolute=True).parent / "test_outputs"
                test_dir.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                output_filename = f"test_{timestamp}_{job_id[:8]}.{ext}"
                output_path = test_dir / output_filename
                
                audio_bytes = _encode_audio_to_format(full_audio, sr, output_format, output_bitrate)
                output_path.write_bytes(audio_bytes)
                
                db.db_update_chapter_state(job_id, ch_idx, worker_name, total_chunks, total_chunks, "completed")
                db.db_update_job(
                    job_id,
                    output_files=[f"/test_outputs/{output_filename}"],
                    status="completed",
                    progress=100,
                    completed_at=datetime.utcnow().isoformat(),
                )
                return
            
            # File name: chapter_number.format
            output_filename = f"{ch_idx + 1}.{ext}"
            output_path = job_dir / output_filename
            audio_bytes = _encode_audio_to_format(full_audio, sr, output_format, output_bitrate)
            output_path.write_bytes(audio_bytes)
            
            db.db_update_chapter_state(job_id, ch_idx, worker_name, total_chunks, total_chunks, "completed")
            completed_count = db.db_increment_completed_chapters(job_id)
            
            if completed_count >= total_chapters:
                # Ostatni z workerów łączy wszystko
                all_output_files = []
                for i in range(total_chapters):
                    all_output_files.append(f"/outputs/{job_id}/{i + 1}.{ext}")
                
                db.db_update_job(
                    job_id,
                    output_files=all_output_files,
                    status="completed",
                    progress=100,
                    completed_at=datetime.utcnow().isoformat(),
                )

    except Exception as e:
        logger.error(f"Job {job_id} Chapter {ch_idx} failed: {e}", exc_info=True)
        db.db_update_job(job_id, status="failed", error=str(e))
        db.db_update_chapter_state(job_id, ch_idx, worker_name if 'worker_name' in locals() else "unknown", 0, 0, "failed")
    finally:
        # VRAM cleanup
        if config_manager.get_bool("audio_output.cleanup_vram_after_job", False):
            logger.info(f"Job {job_id} ch {ch_idx}: cleaning up VRAM...")
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                try:
                    torch.mps.empty_cache()
                except AttributeError:
                    pass
                    pass

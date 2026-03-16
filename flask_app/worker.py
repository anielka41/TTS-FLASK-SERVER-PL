import os
import time
import gc
import re
import math
import uuid
import threading
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any
import numpy as np
import torch
import librosa

from config import config_manager, get_output_path, get_reference_audio_path, get_gen_default_temperature, get_gen_default_exaggeration, get_gen_default_cfg_weight, get_gen_default_seed, get_gen_default_language, get_audio_sample_rate, get_gen_default_speed_factor, get_gen_default_sentence_pause_ms, get_artifacts_enabled, get_artifacts_text_preprocessing_enabled, get_artifacts_min_sentence_words, get_artifacts_glitch_detection_enabled, get_artifacts_glitch_threshold, get_artifacts_retry_on_glitch
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
        print(f"[Worker {worker_name}] Rozpoczynam iterację nad rozdziałami zadania {job_id}...")
        db.db_update_job(job_id, status="processing", started_at=datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S"), worker_name=worker_name, current_chapter=ch_idx + 1)

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
        
        # Check cancellation (lightweight)
        job_status = db.db_get_job_status(job_id)
        if not job_status or job_status == "cancelled":
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
            # Check cancellation (lightweight — status only, no JSON parsing)
            job_status = db.db_get_job_status(job_id)
            if not job_status or job_status == "cancelled":
                return

            # Check pause (lightweight — status only)
            while True:
                job_status = db.db_get_job_status(job_id)
                if not job_status:
                    return
                if job_status != "paused":
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

                # ----- Apply Text Pre-Processing -----
                pipeline_mode = job.get("pipeline_mode", "baseline")
                if pipeline_mode in ("test_pipeline", "tuning") and get_artifacts_text_preprocessing_enabled():
                    try:
                        from flask_app.text_preprocessing import normalize_text_for_tts
                        min_words = get_artifacts_min_sentence_words()
                        chunk_text = normalize_text_for_tts(chunk_text, min_sentence_words=min_words)
                    except Exception as e:
                        logger.error(f"Text pre-processing failed: {e}")

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
                    
                # ----- Apply Artifact Reduction Pipeline based on mode -----
                if pipeline_mode in ("test_pipeline", "tuning"):
                    try:
                        from flask_app.artifacts import apply_artifacts_pipeline
                        is_test = (pipeline_mode == "test_pipeline")
                        audio_np, glitch_score = apply_artifacts_pipeline(audio_np, sr, expected_text=chunk_text, is_test_mode=is_test)
                        
                        # Retry logic: if glitch detected and retry enabled
                        if (glitch_score is not None 
                            and get_artifacts_glitch_detection_enabled() 
                            and get_artifacts_retry_on_glitch() 
                            and glitch_score > get_artifacts_glitch_threshold()):
                            
                            logger.warning(f"Glitch detected (score={glitch_score:.3f}), retrying with modified seed...")
                            original_seed = get_gen_default_seed()
                            retry_seed = original_seed + 1 if original_seed > 0 else 42
                            
                            retry_tensor, retry_sr = engine.synthesize(
                                text=chunk_text,
                                audio_prompt_path=prompt_path,
                                temperature=get_gen_default_temperature(),
                                exaggeration=get_gen_default_exaggeration(),
                                cfg_weight=get_gen_default_cfg_weight(),
                                seed=retry_seed,
                                language=lang_code,
                            )
                            
                            if retry_tensor is not None:
                                retry_np = retry_tensor.squeeze().cpu().numpy()
                                if speed_factor and speed_factor != 1.0 and speed_factor > 0:
                                    retry_np = librosa.effects.time_stretch(retry_np, rate=speed_factor)
                                retry_np, retry_glitch = apply_artifacts_pipeline(retry_np, sr, expected_text=chunk_text, is_test_mode=is_test)
                                
                                retry_score = retry_glitch if retry_glitch is not None else float('inf')
                                if retry_score < glitch_score:
                                    logger.info(f"Retry improved: {glitch_score:.3f} -> {retry_score:.3f}. Using retried version.")
                                    audio_np = retry_np
                                    glitch_score = retry_score
                                else:
                                    logger.info(f"Retry did not improve: {glitch_score:.3f} vs {retry_score:.3f}. Keeping original.")
                            
                            if glitch_score > get_artifacts_glitch_threshold():
                                logger.warning(f"Problematic chunk logged: text='{chunk_text[:80]}...', glitch={glitch_score:.3f}")
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

            # --- Aggressively release transient variables after each chunk ---
            try:
                del wav_tensor
            except NameError:
                pass
            try:
                del audio_np
            except NameError:
                pass
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        if audio_parts:
            full_audio = np.concatenate(audio_parts)
            ext = output_format if output_format in ("mp3", "wav", "ogg") else "wav"
            
            pipeline_mode = job.get("pipeline_mode", "baseline")
            if pipeline_mode == "test_pipeline":
                # Save to test_outputs
                from config import get_output_path
                test_dir = get_output_path(ensure_absolute=True).parent / "test_outputs"
                test_dir.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d_%H%M%S")
                output_filename = f"test_{timestamp}_{job_id[:8]}.{ext}"
                output_path = test_dir / output_filename
                
                audio_bytes = _encode_audio_to_format(full_audio, sr, output_format, output_bitrate)
                output_path.write_bytes(audio_bytes)
                
                timestamps = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")
                # Immediately update output files array
                current_job = db.db_get_job(job_id)
                new_files = list(current_job.get("output_files", []))
                new_file_url = f"/test_outputs/{output_filename}"
                if new_file_url not in new_files:
                    new_files.append(new_file_url)
                
                db.db_update_chapter_state(job_id, ch_idx, worker_name, total_chunks, total_chunks, "completed")
                db.db_update_job(
                    job_id,
                    output_files=new_files,
                    status="completed",
                    progress=100,
                    completed_at=timestamps,
                )
                
                del full_audio, audio_parts, audio_bytes
                return
            
            # File name: chapter_number.format
            output_filename = f"{ch_idx + 1}.{ext}"
            output_path = job_dir / output_filename
            audio_bytes = _encode_audio_to_format(full_audio, sr, output_format, output_bitrate)
            output_path.write_bytes(audio_bytes)
            
            db.db_update_chapter_state(job_id, ch_idx, worker_name, total_chunks, total_chunks, "completed")
            completed_count = db.db_increment_completed_chapters(job_id)
            
            # Immediately append this chapter's file to the job's `output_files` in the database!
            current_job = db.db_get_job(job_id)
            current_files = list(current_job.get("output_files", []))
            new_file_url = f"/outputs/{job_id}/{ch_idx + 1}.{ext}"
            if new_file_url not in current_files:
                current_files.append(new_file_url)
                
            db.db_update_job(job_id, output_files=current_files)
            
            if completed_count >= total_chapters:
                # Ostatni z workerów łączy wszystko
                db.db_update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    completed_at=datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S"),
                )
                
            del full_audio, audio_parts, audio_bytes

    except Exception as e:
        logger.error(f"Job {job_id} Chapter {ch_idx} failed: {e}", exc_info=True)
        db.db_update_job(job_id, status="failed", error=str(e))
        db.db_update_chapter_state(job_id, ch_idx, worker_name if 'worker_name' in locals() else "unknown", 0, 0, "failed")
    finally:
        # Aggressively release all large variables.
        # NOTE: the old exec(f"del {_varname}") approach does NOT work —
        # exec() runs in its own scope and cannot delete variables from
        # the enclosing function.  We use direct deletion instead.
        try:
            del audio_parts
        except (NameError, UnboundLocalError):
            pass
        try:
            del all_chunks
        except (NameError, UnboundLocalError):
            pass
        try:
            del full_audio
        except (NameError, UnboundLocalError):
            pass
        try:
            del audio_bytes
        except (NameError, UnboundLocalError):
            pass
        try:
            del segments
        except (NameError, UnboundLocalError):
            pass
        try:
            del job
        except (NameError, UnboundLocalError):
            pass
        try:
            del chapter_text
        except (NameError, UnboundLocalError):
            pass

        # Always run GC + VRAM cleanup after every chapter, unconditionally
        gc.collect()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if torch.backends.mps.is_available():
            try:
                torch.mps.empty_cache()
            except AttributeError:
                pass


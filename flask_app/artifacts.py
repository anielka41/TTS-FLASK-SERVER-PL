import os
import tempfile
import numpy as np
import soundfile as sf
import subprocess
import logging
import torch

from config import (
    get_artifacts_enabled,
    get_artifacts_denoise_enabled,
    get_artifacts_denoise_strength,
    get_artifacts_autoeditor_enabled,
    get_artifacts_autoeditor_threshold,
    get_artifacts_autoeditor_margin,
    get_whisper_enabled,
    get_whisper_backend,
    get_whisper_model_name,
    get_whisper_language
)

logger = logging.getLogger("flask_app.artifacts")

# Whisper Models caching
_WHISPER_OPENAI_MODEL = None
_WHISPER_FASTER_MODEL = None


def apply_artifacts_pipeline(audio_np: np.ndarray, sample_rate: int, expected_text: str = "", is_test_mode: bool = False) -> np.ndarray:
    """
    Applies the configured artifact reduction pipeline on a single chunk or full audio.
    Order: Denoise -> Auto-editor -> FFmpeg normalize -> Whisper validation (optional warning)
    Returns: Processed audio numpy array.
    """
    if not is_test_mode and not get_artifacts_enabled():
        return audio_np

    if len(audio_np) == 0:
        return audio_np

    current_audio = audio_np

    # 1. Denoise (pyrnnoise)
    if get_artifacts_denoise_enabled():
        try:
            from pyrnnoise import RNNoise
            denoiser = RNNoise(sample_rate=sample_rate)
            
            # RNNoise works strictly on 480Hz frames
            # Ensure float32 [-1, 1]
            if current_audio.dtype != np.float32:
                current_audio = current_audio.astype(np.float32)
            
            # Convert to int16 for pyrnnoise processing
            audio_int16 = (current_audio * 32767).astype(np.int16)
            
            # Quick & dirty rnnoise pass
            out_frames = [f for p, f in denoiser.denoise_chunk(audio_int16, partial=True)]
            if out_frames:
                denoised_audio_int16 = np.concatenate(out_frames, axis=1).squeeze()
                denoised_audio_int16 = denoised_audio_int16[:len(current_audio)]
                denoised_audio = denoised_audio_int16.astype(np.float32) / 32767.0
            else:
                denoised_audio = current_audio.copy()
            # Mix based on strength
            strength = get_artifacts_denoise_strength()
            strength_clamped = max(0.0, min(1.0, float(strength)))
            if strength_clamped > 0.0:
                current_audio = current_audio * (1.0 - strength_clamped) + denoised_audio * strength_clamped
                logger.info(f"pyrnnoise denoising applied with strength {strength_clamped}.")
        except ImportError:
            logger.warning("pyrnnoise not installed. Skipping denoising.")
        except Exception as e:
            logger.error(f"pyrnnoise error: {e}")

    # 2. Native Silence Trimming (replacing external auto-editor)
    if is_test_mode or get_artifacts_autoeditor_enabled():
        try:
            import librosa
            threshold = 4.0 if is_test_mode else get_artifacts_autoeditor_threshold()
            margin = 0.2 if is_test_mode else get_artifacts_autoeditor_margin()
            
            # Convert percentage (0.1 to 10.0) to top_db (dB below reference maximum)
            top_db = -20 * np.log10(max(threshold, 0.01) / 100.0)
            
            intervals = librosa.effects.split(current_audio, top_db=top_db, frame_length=2048, hop_length=512)
            
            if len(intervals) > 0:
                margin_samples = int(margin * sample_rate)
                merged_intervals = []
                
                for start, end in intervals:
                    start = max(0, start - margin_samples)
                    end = min(len(current_audio), end + margin_samples)
                    
                    if not merged_intervals:
                        merged_intervals.append([start, end])
                    else:
                        prev_start, prev_end = merged_intervals[-1]
                        if start <= prev_end:
                            merged_intervals[-1][1] = max(prev_end, end)
                        else:
                            merged_intervals.append([start, end])
                            
                active_audio_parts = [current_audio[s:e] for s, e in merged_intervals]
                if active_audio_parts:
                    current_audio = np.concatenate(active_audio_parts)
                logger.info(f"Silence trimmed (threshold: {threshold}%, margin: {margin}s). Interval cuts: {len(merged_intervals)}")
            else:
                logger.info(f"Silence trimmer: Audio entirely below threshold. Returning unmodified.")
        except Exception as e:
            logger.error(f"Silence trimming process error: {e}")

    # 3. Whisper Validation
    if get_whisper_enabled() and expected_text and not is_test_mode:
        try:
            whisper_text = run_whisper_transcription(current_audio, sample_rate)
            # Dalsza logika, np. logowanie błędu jeśli difflib.SequenceMatcher ratio < 0.5
            # Obecnie: tylko log. Mógłby throwować jeśli strict validation jest włączone.
            logger.info(f"Whisper Validation: Expected: '{expected_text}' || Got: '{whisper_text}'")
        except Exception as e:
            logger.error(f"Whisper validation error: {e}")

    return current_audio


def run_whisper_transcription(audio_np: np.ndarray, sample_rate: int) -> str:
    """Uses Whisper to transcribe the audio for validation."""
    backend = get_whisper_backend()
    model_name = get_whisper_model_name()
    language = get_whisper_language() or "pl"
    
    # Resample to 16k for whisper if needed
    if sample_rate != 16000:
        import librosa
        audio_16k = librosa.resample(audio_np, orig_sr=sample_rate, target_sr=16000)
    else:
        audio_16k = audio_np

    # FP32 -> FP32 (Whisper expects floats between -1 and 1)
    if audio_16k.dtype != np.float32:
        audio_16k = audio_16k.astype(np.float32)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    text = ""
    if backend == "faster-whisper":
        global _WHISPER_FASTER_MODEL
        try:
            from faster_whisper import WhisperModel
            if _WHISPER_FASTER_MODEL is None or not hasattr(_WHISPER_FASTER_MODEL, '_model_name') or _WHISPER_FASTER_MODEL._model_name != model_name:
                num_workers = int(os.environ.get("NUM_WORKERS", 1))
                # optimize device_index/compute_type for concurrency
                _WHISPER_FASTER_MODEL = WhisperModel(model_name, device=device, compute_type="float16" if device == "cuda" else "int8")
                _WHISPER_FASTER_MODEL._model_name = model_name
            
            segments, info = _WHISPER_FASTER_MODEL.transcribe(audio_16k, beam_size=5, language=language)
            text = " ".join([segment.text for segment in segments]).strip()
        except ImportError:
            logger.error("faster-whisper not installed!")

    else: # openai-whisper
        global _WHISPER_OPENAI_MODEL
        try:
            import whisper
            if _WHISPER_OPENAI_MODEL is None or getattr(_WHISPER_OPENAI_MODEL, 'name', '') != model_name:
                _WHISPER_OPENAI_MODEL = whisper.load_model(model_name, device=device)
            
            # openai whisper accepts torch tensor or numpy
            result = _WHISPER_OPENAI_MODEL.transcribe(audio_16k, language=language)
            text = result["text"].strip()
        except ImportError:
            logger.error("openai-whisper not installed!")

    return text

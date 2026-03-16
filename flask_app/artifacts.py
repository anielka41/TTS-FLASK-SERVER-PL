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
    get_artifacts_loudnorm_enabled,
    get_artifacts_loudnorm_target_lufs,
    get_artifacts_microfade_enabled,
    get_artifacts_microfade_duration_ms,
    get_artifacts_glitch_detection_enabled,
    get_artifacts_glitch_threshold,
    get_artifacts_trim_silence_enabled,
    get_artifacts_trim_silence_threshold_db,
    get_artifacts_remove_unvoiced_enabled,
    get_artifacts_remove_unvoiced_min_duration_ms,
    get_artifacts_tail_guard_enabled,
    get_artifacts_tail_guard_max_tail_ms,
    get_artifacts_tail_guard_energy_threshold,
    get_artifacts_fingerprint_enabled,
    get_artifacts_fingerprint_similarity_threshold,
    get_whisper_enabled,
    get_whisper_backend,
    get_whisper_model_name,
    get_whisper_language
)

from utils import trim_lead_trail_silence, remove_long_unvoiced_segments

logger = logging.getLogger("flask_app.artifacts")

# Whisper Models caching
_WHISPER_OPENAI_MODEL = None
_WHISPER_FASTER_MODEL = None


def compute_glitch_score(audio_np: np.ndarray, sample_rate: int, window_ms: int = 10) -> float:
    """
    Computes a glitch score based on short-window RMS analysis.
    Detects sudden anomalous spikes in energy that indicate clicks,
    pops, or generation artifacts.

    Higher score = more anomalous spikes detected.

    Args:
        audio_np: Float32 numpy audio array.
        sample_rate: Sample rate of audio.
        window_ms: Window size in ms for RMS computation.

    Returns:
        Glitch score (0.0 = clean, higher = more glitches).
    """
    if len(audio_np) == 0:
        return 0.0

    window_samples = max(1, int(sample_rate * window_ms / 1000))
    num_windows = len(audio_np) // window_samples

    if num_windows < 3:
        return 0.0

    # Compute RMS for each window
    rms_values = np.array([
        np.sqrt(np.mean(audio_np[i * window_samples:(i + 1) * window_samples] ** 2))
        for i in range(num_windows)
    ])

    # Filter out silent windows (below noise floor)
    noise_floor = 1e-6
    active_rms = rms_values[rms_values > noise_floor]

    if len(active_rms) < 3:
        return 0.0

    # Calculate statistics
    median_rms = np.median(active_rms)
    mad = np.median(np.abs(active_rms - median_rms))  # Median Absolute Deviation

    if mad < noise_floor:
        # Very uniform signal — likely clean
        return 0.0

    # Count windows that deviate significantly (> 3 MAD from median)
    spike_threshold = median_rms + 3 * mad * 1.4826  # 1.4826 scales MAD to approx std
    num_spikes = np.sum(active_rms > spike_threshold)

    # Score: ratio of spiky windows + weighted by spike intensity
    spike_ratio = num_spikes / len(active_rms)
    if num_spikes > 0:
        spike_intensities = active_rms[active_rms > spike_threshold] / median_rms
        avg_intensity = np.mean(spike_intensities)
    else:
        avg_intensity = 1.0

    score = spike_ratio * avg_intensity * 10.0  # Scale to 0-10 range roughly

    return round(float(score), 3)


def compute_spectral_artifact_score(audio_np: np.ndarray, sample_rate: int) -> float:
    """
    Computes a spectral artifact score based on spectral flatness, high-frequency
    energy ratio, and zero-crossing rate. Useful for detecting TTS hallucinations
    which often have non-speech characteristic noise.
    """
    if len(audio_np) == 0:
        return 0.0

    try:
        import librosa
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            
            # 1. Spectral Flatness: How noise-like the signal is
            flatness = librosa.feature.spectral_flatness(y=audio_np)
            avg_flatness = float(np.mean(flatness))
            
            # 2. High Frequency Energy Ratio (>8kHz)
            stft = np.abs(librosa.stft(y=audio_np, n_fft=2048, hop_length=512))
            freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=2048)
            hf_mask = freqs > 8000
            
            if np.any(hf_mask):
                hf_energy = np.sum(stft[hf_mask, :])
                total_energy = np.sum(stft) + 1e-6
                hf_ratio = float(hf_energy / total_energy)
            else:
                hf_ratio = 0.0
                
            # 3. Zero Crossing Rate: Anomalously high ZCR indicates noise
            zcr = librosa.feature.zero_crossing_rate(y=audio_np)
            avg_zcr = float(np.mean(zcr))
            
            # Heuristic scoring (0.0 to 10.0 roughly)
            # Normal speech has low flatness, moderate HF ratio, low/moderate ZCR
            # Artifacts (hiss, static, robotic noise) have high flatness and ZCR
            
            flatness_score = min(1.0, avg_flatness * 10) # 0.1 is very noisy
            zcr_score = min(1.0, avg_zcr * 5) # 0.2+ is often just hiss
            hf_score = min(1.0, hf_ratio * 3) # 33%+ HF energy is unnatural
            
            final_score = (flatness_score * 0.5 + zcr_score * 0.3 + hf_score * 0.2) * 10.0
            return round(final_score, 3)

    except ImportError:
        logger.warning("librosa not installed, falling back to basic RMS glitch score.")
        return compute_glitch_score(audio_np, sample_rate)
    except Exception as e:
        logger.error(f"Spectral score error: {e}")
        return compute_glitch_score(audio_np, sample_rate)


def tail_guard(audio_np: np.ndarray, sample_rate: int, max_tail_ms: int = 500, energy_threshold: float = 0.02) -> np.ndarray:
    """
    Scans the end of the audio buffer backwards to find where speech actually ends,
    cutting off hallucinatory artifacts generated by the TTS model.
    """
    if len(audio_np) == 0:
        return audio_np
        
    tail_samples = int(sample_rate * max_tail_ms / 1000)
    if len(audio_np) < tail_samples * 2: # Too short to reliably analyze
        return audio_np
        
    # Analyze only the tail portion
    tail_audio = audio_np[-tail_samples:]
    
    # Simple energy-based Voice Activity Detection (VAD) from the end
    window_ms = 20
    window_samples = int(sample_rate * window_ms / 1000)
    num_windows = len(tail_audio) // window_samples
    
    if num_windows < 2:
        return audio_np
        
    rms_values = np.array([
        np.sqrt(np.mean(tail_audio[i * window_samples:(i + 1) * window_samples] ** 2))
        for i in range(num_windows)
    ])
    
    # Find the last window that is considered "voiced" (energy > threshold)
    last_voiced_window = -1
    for i in range(num_windows - 1, -1, -1):
        if rms_values[i] > energy_threshold:
            last_voiced_window = i
            break
            
    if last_voiced_window == -1:
        # The entire tail is silent or noise below threshold.
        # It's relatively safe to just cut the tail where it drops below threshold.
        # But to be safe, we just cut the whole tail if it's all noise.
        cut_point = len(audio_np) - tail_samples
        logger.info(f"Tail Guard: Entire tail was below energy threshold. Trimming {max_tail_ms}ms tail.")
        return apply_microfades(audio_np[:cut_point], sample_rate, 15)
        
    elif last_voiced_window < num_windows - 3:
        # Speech ended, and there are at least 3 noise windows (60ms) after it
        # Cut after the speech ends (plus a little margin)
        cut_window = last_voiced_window + 1
        cut_point_in_tail = cut_window * window_samples
        cut_point = len(audio_np) - tail_samples + cut_point_in_tail
        
        trimmed_ms = (len(audio_np) - cut_point) / sample_rate * 1000
        logger.info(f"Tail Guard: Detected speech end. Trimming {trimmed_ms:.1f}ms of artifact tail.")
        return apply_microfades(audio_np[:cut_point], sample_rate, 15)
        
    return audio_np


def apply_microfades(audio_np: np.ndarray, sample_rate: int, duration_ms: int = 15) -> np.ndarray:
    """
    Applies short fade-in and fade-out to audio to eliminate click artifacts
    at chunk boundaries.

    Args:
        audio_np: Float32 numpy audio array.
        sample_rate: Sample rate.
        duration_ms: Fade duration in milliseconds.

    Returns:
        Audio with micro-fades applied.
    """
    if len(audio_np) == 0 or duration_ms <= 0:
        return audio_np

    fade_samples = min(int(sample_rate * duration_ms / 1000), len(audio_np) // 2)
    if fade_samples < 2:
        return audio_np

    result = audio_np.copy()

    # Fade-in: linear ramp from 0 to 1
    fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    result[:fade_samples] *= fade_in

    # Fade-out: linear ramp from 1 to 0
    fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    result[-fade_samples:] *= fade_out

    return result


def apply_loudness_normalization(audio_np: np.ndarray, sample_rate: int, target_lufs: float = -23.0) -> np.ndarray:
    """
    Normalizes audio loudness to target LUFS using pyloudnorm.

    Args:
        audio_np: Float32 numpy audio array.
        sample_rate: Sample rate.
        target_lufs: Target loudness in LUFS.

    Returns:
        Loudness-normalized audio.
    """
    if len(audio_np) == 0:
        return audio_np

    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sample_rate)
        current_lufs = meter.integrated_loudness(audio_np)

        if np.isinf(current_lufs) or np.isnan(current_lufs):
            logger.warning("Cannot measure loudness (silent audio?). Skipping normalization.")
            return audio_np

        normalized = pyln.normalize.loudness(audio_np, current_lufs, target_lufs)

        # Clip to prevent clipping artifacts
        normalized = np.clip(normalized, -1.0, 1.0)

        logger.info(f"Loudness normalized: {current_lufs:.1f} LUFS -> {target_lufs:.1f} LUFS")
        return normalized.astype(np.float32)

    except ImportError:
        logger.warning("pyloudnorm not installed. Skipping loudness normalization. Install with: pip install pyloudnorm")
        return audio_np
    except Exception as e:
        logger.error(f"Loudness normalization error: {e}")
        return audio_np


def apply_artifacts_pipeline(audio_np: np.ndarray, sample_rate: int, expected_text: str = "", is_test_mode: bool = False) -> tuple:
    """
    Applies the configured artifact reduction pipeline on a single chunk or full audio.
    Order: Denoise -> Auto-editor -> Loudness Norm -> Micro-Fades -> Glitch Detection -> Whisper validation
    Returns: Tuple of (processed audio numpy array, glitch_score or None).
    """
    if not is_test_mode and not get_artifacts_enabled():
        return audio_np, None

    if len(audio_np) == 0:
        return audio_np, None

    current_audio = audio_np

    # Measure original loudness BEFORE any processing for volume compensation
    pre_pipeline_lufs = None
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sample_rate)
        pre_pipeline_lufs = meter.integrated_loudness(audio_np)
        if np.isinf(pre_pipeline_lufs) or np.isnan(pre_pipeline_lufs):
            pre_pipeline_lufs = None
        else:
            logger.debug(f"Pre-pipeline loudness: {pre_pipeline_lufs:.1f} LUFS")
    except ImportError:
        logger.debug("pyloudnorm not installed, volume compensation unavailable.")
    except Exception as e:
        logger.debug(f"Could not measure pre-pipeline loudness: {e}")

    # 1. Trim leading and trailing silence (Librosa)
    if get_artifacts_trim_silence_enabled() or is_test_mode:
        trim_db = get_artifacts_trim_silence_threshold_db()
        original_len = len(current_audio)
        current_audio = trim_lead_trail_silence(current_audio, sample_rate, silence_threshold_db=trim_db)
        if len(current_audio) < original_len:
            logger.info("Trim Silence: Removed leading/trailing silence.")

    # 2. Remove unvoiced segments (Parselmouth)
    if get_artifacts_remove_unvoiced_enabled() or is_test_mode:
        min_dur = get_artifacts_remove_unvoiced_min_duration_ms()
        original_len = len(current_audio)
        current_audio = remove_long_unvoiced_segments(current_audio, sample_rate, min_unvoiced_duration_ms=min_dur)
        if len(current_audio) < original_len:
            logger.info("Remove Unvoiced: Removed long unvoiced segments.")

    # 3. Denoise (pyrnnoise)
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

    # 5. Tail Guard (End-of-chunk artifact removal)
    if get_artifacts_tail_guard_enabled() or is_test_mode:
        max_tail = get_artifacts_tail_guard_max_tail_ms()
        energy_thresh = get_artifacts_tail_guard_energy_threshold()
        current_audio = tail_guard(current_audio, sample_rate, max_tail_ms=max_tail, energy_threshold=energy_thresh)

    # 5.5 Volume Compensation — restore original loudness after artifact removal
    if pre_pipeline_lufs is not None:
        try:
            import pyloudnorm as pyln
            meter = pyln.Meter(sample_rate)
            post_lufs = meter.integrated_loudness(current_audio)
            if not (np.isinf(post_lufs) or np.isnan(post_lufs)):
                lufs_loss = pre_pipeline_lufs - post_lufs
                if abs(lufs_loss) > 0.5:  # Only compensate if meaningful difference
                    current_audio = pyln.normalize.loudness(current_audio, post_lufs, pre_pipeline_lufs)
                    current_audio = np.clip(current_audio, -1.0, 1.0).astype(np.float32)
                    logger.info(f"Volume compensation: {post_lufs:.1f} -> {pre_pipeline_lufs:.1f} LUFS (restored, delta={lufs_loss:+.1f} dB)")
                else:
                    logger.debug(f"Volume compensation skipped: delta={lufs_loss:+.1f} dB (below 0.5 dB threshold)")
        except Exception as e:
            logger.warning(f"Volume compensation failed: {e}")

    # 6. Loudness Normalization (pyloudnorm) — manual target override
    if get_artifacts_loudnorm_enabled():
        target_lufs = get_artifacts_loudnorm_target_lufs()
        current_audio = apply_loudness_normalization(current_audio, sample_rate, target_lufs)

    # 4. Micro-fades (fade-in/out at chunk boundaries)
    if get_artifacts_microfade_enabled():
        fade_ms = get_artifacts_microfade_duration_ms()
        current_audio = apply_microfades(current_audio, sample_rate, fade_ms)
        logger.info(f"Micro-fades applied ({fade_ms}ms).")

    # 8. Spectral Artifact Score / Glitch Detection
    glitch_score = None
    if get_artifacts_glitch_detection_enabled() or is_test_mode:
        # Use spectral score if enabled, otherwise basic RMS glitch score
        glitch_score = compute_spectral_artifact_score(current_audio, sample_rate)
        
        # 9. Artifact Fingerprinting (Reference-based)
        if get_artifacts_fingerprint_enabled():
            from flask_app.artifact_fingerprints import check_against_fingerprints
            sim_threshold = get_artifacts_fingerprint_similarity_threshold()
            is_artifact, sim, name = check_against_fingerprints(current_audio, sample_rate, threshold=sim_threshold)
            if is_artifact:
                logger.warning(f"Fingerprint MATCH: '{name}' (similarity {sim:.2f}). Boosting glitch score to force retry/flag.")
                glitch_score = max(glitch_score, 10.0)  # Force a high score
                
        threshold = get_artifacts_glitch_threshold()
        if glitch_score > threshold:
            logger.warning(f"Spectral Glitch detected! Score: {glitch_score:.3f} (threshold: {threshold:.1f})")
        else:
            logger.info(f"Spectral Glitch score: {glitch_score:.3f} (threshold: {threshold:.1f}) - OK")

    # 10. Whisper Validation
    if get_whisper_enabled() and expected_text and not is_test_mode:
        try:
            whisper_text = run_whisper_transcription(current_audio, sample_rate)
            # Dalsza logika, np. logowanie błędu jeśli difflib.SequenceMatcher ratio < 0.5
            # Obecnie: tylko log. Mógłby throwować jeśli strict validation jest włączone.
            logger.info(f"Whisper Validation: Expected: '{expected_text}' || Got: '{whisper_text}'")
        except Exception as e:
            logger.error(f"Whisper validation error: {e}")

    return current_audio, glitch_score


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

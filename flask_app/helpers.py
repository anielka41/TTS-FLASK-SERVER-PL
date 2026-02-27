import json
import io
import re
import numpy as np
import soundfile as sf
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from config import get_reference_audio_path

logger = logging.getLogger("flask_app.helpers")
DATA_DIR = Path(__file__).parent.parent / "data"
VOICE_METADATA_FILE = DATA_DIR / "voice_metadata.json"

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


from config import get_reference_audio_path, get_predefined_voices_path

def _list_voices(base_path: Optional[Path] = None, voice_type: str = "custom") -> List[Dict[str, Any]]:
    if base_path is None:
        base_path = get_reference_audio_path(ensure_absolute=True)
    base_path.mkdir(parents=True, exist_ok=True)
    
    meta = _load_voice_metadata()
    voices = []
    for f in sorted(base_path.iterdir()):
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
                "type": voice_type,
            })
    return voices

def _list_predefined_voices() -> List[Dict[str, Any]]:
    pred_path = get_predefined_voices_path(ensure_absolute=True)
    return _list_voices(base_path=pred_path, voice_type="predefined")


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



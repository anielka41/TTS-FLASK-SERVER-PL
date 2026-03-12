import os
import json
import numpy as np
import logging
from typing import List, Dict

logger = logging.getLogger("flask_app.artifact_fingerprints")

FINGERPRINT_DIR = os.path.join("data", "artifact_fingerprints")
os.makedirs(FINGERPRINT_DIR, exist_ok=True)

_fingerprints_cache = None

def extract_mfcc(audio_np: np.ndarray, sample_rate: int) -> np.ndarray:
    """Extract MFCC features from an audio array."""
    try:
        import librosa
        if len(audio_np) == 0:
            return np.array([])
            
        # Extract 13 MFCCs, standardize them for comparison
        mfccs = librosa.feature.mfcc(y=audio_np.astype(np.float32), sr=sample_rate, n_mfcc=13)
        return mfccs
    except ImportError:
        logger.warning("librosa not installed, cannot extract MFCCs.")
        return np.array([])
    except Exception as e:
        logger.error(f"Error extracting MFCCs: {e}")
        return np.array([])

def load_fingerprints() -> List[Dict]:
    """Load all saved fingerprints from disk."""
    global _fingerprints_cache
    if _fingerprints_cache is not None:
        return _fingerprints_cache
        
    fingerprints = []
    
    for filename in os.listdir(FINGERPRINT_DIR):
        if filename.endswith(".json"):
            file_path = os.path.join(FINGERPRINT_DIR, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data['mfcc_matrix'] = np.array(data['mfcc_matrix'])
                    fingerprints.append(data)
            except Exception as e:
                logger.error(f"Failed to load fingerprint {file_path}: {e}")
                
    _fingerprints_cache = fingerprints
    return fingerprints

def save_fingerprint(name: str, mfcc_matrix: np.ndarray) -> bool:
    """Save a new artifact fingerprint to disk."""
    global _fingerprints_cache
    try:
        data = {
            "id": name.replace(" ", "_").lower(),
            "name": name,
            "mfcc_matrix": mfcc_matrix.tolist()
        }
        
        file_path = os.path.join(FINGERPRINT_DIR, f"{data['id']}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            
        # Invalidate cache
        _fingerprints_cache = None
        return True
    except Exception as e:
        logger.error(f"Failed to save fingerprint {name}: {e}")
        return False

def delete_fingerprint(fingerprint_id: str) -> bool:
    """Delete an existing fingerprint by its ID."""
    global _fingerprints_cache
    file_path = os.path.join(FINGERPRINT_DIR, f"{fingerprint_id}.json")
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            # Invalidate cache
            _fingerprints_cache = None
            return True
        except Exception as e:
            logger.error(f"Failed to delete fingerprint {fingerprint_id}: {e}")
            return False
    return False

def check_against_fingerprints(audio_np: np.ndarray, sample_rate: int, threshold: float = 0.7) -> tuple:
    """
    Check an audio chunk against known artifact fingerprints using sliding-window DTW
    or simple correlation over MFCC matrices.
    Returns: (is_artifact: bool, max_similarity: float, matched_name: str)
    """
    fingerprints = load_fingerprints()
    if not fingerprints or len(audio_np) == 0:
        return False, 0.0, ""
        
    try:
        import librosa
        target_mfcc = extract_mfcc(audio_np, sample_rate)
        if target_mfcc.size == 0:
            return False, 0.0, ""
            
        max_sim = 0.0
        matched_name = ""
        
        for fp in fingerprints:
            ref_mfcc = fp['mfcc_matrix']
            if ref_mfcc.size == 0:
                continue
                
            # If the generated chunk is shorter than the artifact reference, 
            # we pad or just compute distance on the available part.
            # Using librosa.sequence.dtw for time-aligned distance.
            # DTW distance (lower is better, we normalize to a similarity score 0-1)
            
            # Subsequence DTW
            D, wp = librosa.sequence.dtw(X=ref_mfcc, Y=target_mfcc, subseq=True)
            
            # Cost at the end of the minimum-cost path
            min_cost = np.min(D[-1, :])
            
            # Heuristic normalization to a similarity score 0 to 1
            # Normalizing by path length and feature dimensionality roughly
            path_len = len(wp)
            norm_cost = min_cost / (path_len * 13) if path_len > 0 else float('inf')
            
            # Mapping cost to similarity (e.g. cost 0 -> sim 1.0, cost > 10 -> sim ~0)
            similarity = max(0.0, 1.0 - (norm_cost / 5.0))
            
            if similarity > max_sim:
                max_sim = similarity
                matched_name = fp['name']
                
        if max_sim > threshold:
            logger.warning(f"Artifact footprint matched! Similarity: {max_sim:.2f} (Matched: {matched_name})")
            return True, max_sim, matched_name
            
        return False, max_sim, ""
        
    except ImportError:
        logger.warning("librosa not installed, cannot verify fingerprints.")
        return False, 0.0, ""
    except Exception as e:
        logger.error(f"Error checking fingerprints: {e}")
        return False, 0.0, ""

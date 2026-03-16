# worker_chapters.py
# RQ worker for processing TTS chapter generation jobs.
# Uses file locks to prevent duplicate processes and proper signal handling.
#
# Memory management strategy:
#   The worker uses burst=True (process ONE job, then exit the work loop).
#   After each job, all TTS model state, CUDA caches, and Python garbage are
#   aggressively cleared.  The outer loop then re-initialises everything for
#   the next job.  This guarantees that the OS reclaims all leaked C-level
#   memory between chapters, preventing the RAM from filling up during long
#   audiobook generation sessions.

import os
import sys
import gc
import time
import fcntl
import logging
from dotenv import load_dotenv
from redis import Redis
from rq import SimpleWorker, Queue

import engine
import database as db

logger = logging.getLogger("worker_chapters")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

LOCK_DIR = "/tmp"

# How many jobs to process before doing a full memory cleanup cycle.
# 1 = restart after every chapter (safest for memory).
MAX_JOBS_BEFORE_RESTART = 1

# Seconds to wait between restart cycles (gives OS time to reclaim pages).
RESTART_DELAY_SECONDS = 2


def _acquire_lock(worker_id: str):
    """
    Acquire a file lock to prevent duplicate worker processes with the same ID.
    Returns the lock file descriptor on success, exits the process on failure.
    """
    lock_path = os.path.join(LOCK_DIR, f"chatterbox_worker_{worker_id}.lock")
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        logger.info(f"Lock acquired: {lock_path} (PID={os.getpid()})")
        return lock_fd
    except BlockingIOError:
        logger.error(
            f"Worker '{worker_id}' already running (lock file: {lock_path}). "
            f"Exiting duplicate process."
        )
        lock_fd.close()
        sys.exit(1)


def _cleanup_memory():
    """
    Aggressively release all memory held by the TTS engine, caches,
    and Python garbage collector so the OS can reclaim it.
    """
    import torch

    # 1. Unload the TTS model from engine globals
    try:
        if engine.chatterbox_model is not None:
            del engine.chatterbox_model
            engine.chatterbox_model = None
        engine.MODEL_LOADED = False
        engine.loaded_model_type = None
        engine.loaded_model_class_name = None
    except Exception as e:
        logger.debug(f"Engine cleanup note: {e}")

    # 2. Clear cached Whisper models in artifacts module
    try:
        from flask_app import artifacts
        if hasattr(artifacts, '_WHISPER_OPENAI_MODEL'):
            artifacts._WHISPER_OPENAI_MODEL = None
        if hasattr(artifacts, '_WHISPER_FASTER_MODEL'):
            artifacts._WHISPER_FASTER_MODEL = None
    except Exception:
        pass

    # 3. Python garbage collection (multiple passes for cyclic refs)
    gc.collect()
    gc.collect()
    gc.collect()

    # 4. CUDA cache
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()

    # 5. MPS cache (Apple Silicon)
    if torch.backends.mps.is_available():
        try:
            torch.mps.empty_cache()
        except AttributeError:
            pass

    logger.info("Memory cleanup completed (model unloaded, gc.collect, CUDA cache cleared).")


def start_worker():
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Derive stable worker ID from supervisor process name (set by supervisor)
    # or fall back to PID-based name for manual runs
    worker_id = os.environ.get("SUPERVISOR_PROCESS_NAME", f"manual_worker_{os.getpid()}")
    lock_fd = _acquire_lock(worker_id)

    try:
        redis_conn = Redis.from_url(redis_url)

        from config import get_reference_audio_path
        from flask_app.worker import JOBS_DIR
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        get_reference_audio_path(ensure_absolute=True).mkdir(parents=True, exist_ok=True)

        db.init_db()

        jobs_processed = 0

        # ── Outer loop: process one job at a time, restart memory between jobs ──
        while True:
            # (Re-)load the TTS model if it's not loaded
            if not engine.MODEL_LOADED:
                logger.info(f"Worker '{worker_id}' (PID={os.getpid()}) loading TTS model...")
                if not engine.load_model():
                    logger.error("CRITICAL: TTS Model failed to load in worker!")
                    time.sleep(5)
                    continue
                logger.info("TTS Model loaded successfully in worker.")

            # Remove stale worker records to prevent crash loops on boot
            worker_key = f"rq:worker:{worker_id}"
            redis_conn.srem("rq:workers", worker_key)
            redis_conn.delete(worker_key)

            worker = SimpleWorker(
                ['chapters'],
                connection=redis_conn,
                name=worker_id,
            )

            logger.info(f"Worker '{worker_id}' waiting for next job (burst mode, "
                         f"jobs_processed={jobs_processed})...")

            # burst=True  →  process ONE job from the queue, then return.
            # Returns True if a job was processed, False if queue was empty.
            did_work = worker.work(burst=True)

            if did_work:
                jobs_processed += 1
                if jobs_processed >= MAX_JOBS_BEFORE_RESTART:
                    logger.info(f"Worker '{worker_id}' completed {jobs_processed} job(s). "
                                f"Running full memory cleanup cycle...")
                    _cleanup_memory()
                    jobs_processed = 0
                    time.sleep(RESTART_DELAY_SECONDS)
            else:
                # Queue was empty — wait before polling again.
                # Longer delay to avoid log spam and CPU waste.
                time.sleep(3)


    except KeyboardInterrupt:
        logger.info(f"Worker '{worker_id}' received KeyboardInterrupt, shutting down.")
    except Exception as e:
        logger.error(f"Worker '{worker_id}' crashed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info(f"Worker '{worker_id}' shutting down, releasing lock...")
        _cleanup_memory()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


if __name__ == '__main__':
    start_worker()


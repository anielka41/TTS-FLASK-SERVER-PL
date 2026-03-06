# worker_chapters.py
# RQ worker for processing TTS chapter generation jobs.
# Uses file locks to prevent duplicate processes and proper signal handling.

import os
import sys
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


def start_worker():
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Derive stable worker ID from supervisor process name (set by supervisor)
    # or fall back to PID-based name for manual runs
    worker_id = os.environ.get("SUPERVISOR_PROCESS_NAME", f"manual_worker_{os.getpid()}")
    lock_fd = _acquire_lock(worker_id)

    try:
        redis_conn = Redis.from_url(redis_url)

        logger.info(f"Worker '{worker_id}' (PID={os.getpid()}) initializing TTS engine...")
        from config import get_reference_audio_path
        from flask_app.worker import JOBS_DIR
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        get_reference_audio_path(ensure_absolute=True).mkdir(parents=True, exist_ok=True)

        db.init_db()
        if not engine.load_model():
            logger.error("CRITICAL: TTS Model failed to load in worker!")
            return
        logger.info("TTS Model loaded successfully in worker.")

        logger.info(f"Worker '{worker_id}' connecting to Redis at {redis_url}, queue='chapters'...")

        worker = SimpleWorker(
            ['chapters'],
            connection=redis_conn,
            name=worker_id,
        )

        # SimpleWorker runs jobs in the same process (no fork).
        # This is required for CUDA — forked processes cannot use CUDA tensors.
        worker.work(burst=False)

    except Exception as e:
        logger.error(f"Worker '{worker_id}' crashed: {e}", exc_info=True)
    finally:
        logger.info(f"Worker '{worker_id}' shutting down, releasing lock...")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


if __name__ == '__main__':
    start_worker()

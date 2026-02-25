# worker_chapters.py
import os
import logging
from dotenv import load_dotenv
from redis import Redis
from rq import Worker, Queue

import engine
import database as db

logger = logging.getLogger("worker_chapters")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def start_worker():
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_conn = Redis.from_url(redis_url)

    logger.info("Initializing TTS engine in worker...")
    from config import get_reference_audio_path
    from flask_app.worker import JOBS_DIR
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    get_reference_audio_path(ensure_absolute=True).mkdir(parents=True, exist_ok=True)
    
    db.init_db()
    if not engine.load_model():
        logger.error("CRITICAL: TTS Model failed to load in worker!")
        return
    logger.info("TTS Model loaded successfully in worker.")

    logger.info(f"Connecting to Redis at {redis_url} listening to 'chapters' queue...")
    from rq import SimpleWorker
    worker = SimpleWorker(['chapters'], connection=redis_conn)
    worker.work()

if __name__ == '__main__':
    start_worker()

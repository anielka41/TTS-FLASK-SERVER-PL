#!/usr/bin/env python3
"""
app_flask.py – Flask backend for Chatterbox TTS Server.
Serves the Polish interface and implements all /api/* endpoints.
Uses existing engine.py, config.py, utils.py without modifications.
Persistence via SQLite (database.py).
"""

import os
import logging
import threading
from pathlib import Path

from flask import Flask
from flask_app.routes import main_bp, api_bp, _load_engine

from config import get_output_path
from typing import Dict

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("app_flask")

# --- Flask App ---
BASE_DIR = Path(__file__).parent
FLASK_APP_DIR = BASE_DIR / "flask_app"

app = Flask(
    __name__,
    static_folder=str(FLASK_APP_DIR / "static"),
    static_url_path="/static",
    template_folder=str(FLASK_APP_DIR / "templates"),
)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB


# --- Persistent Data ---
JOBS_DIR = get_output_path(ensure_absolute=True)


from flask_app.routes import main_bp, api_bp

app.register_blueprint(main_bp)
app.register_blueprint(api_bp)

from flask_app.routes import _load_engine


if __name__ == "__main__":
    _load_engine()

    host = get_host()
    port = get_port()
    logger.info(f"Starting Chatterbox Flask PL on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)

#!/usr/bin/env python3
"""
database.py – SQLite persistence for Chatterbox Flask PL.
Tables: dictionary, jobs.
"""

import json
import re
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

DB_PATH = Path(__file__).parent / "chatterbox.db"
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dictionary (
            word TEXT PRIMARY KEY,
            replacement TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Bez tytułu',
            text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0,
            current_chunk INTEGER NOT NULL DEFAULT 0,
            total_chunks INTEGER NOT NULL DEFAULT 0,
            current_chapter INTEGER NOT NULL DEFAULT 0,
            total_chapters INTEGER NOT NULL DEFAULT 0,
            output_format TEXT NOT NULL DEFAULT 'mp3',
            output_bitrate_kbps INTEGER NOT NULL DEFAULT 128,
            voice_assignments_json TEXT NOT NULL DEFAULT '{}',
            output_files_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            tts_engine TEXT NOT NULL DEFAULT 'chatterbox_mtl_local',
            split_by_chapter INTEGER NOT NULL DEFAULT 0,
            chapters_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        );
    """)
    conn.commit()


# ============================================================
# Dictionary
# ============================================================

def db_get_dictionary() -> Dict[str, str]:
    """Return all dictionary entries as {word: replacement}."""
    conn = _get_conn()
    rows = conn.execute("SELECT word, replacement FROM dictionary ORDER BY word").fetchall()
    return {r["word"]: r["replacement"] for r in rows}


def db_get_dictionary_count() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM dictionary").fetchone()
    return row["cnt"] if row else 0


def db_add_word(word: str, replacement: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO dictionary (word, replacement) VALUES (?, ?)",
        (word, replacement),
    )
    conn.commit()


def db_delete_word(word: str):
    conn = _get_conn()
    conn.execute("DELETE FROM dictionary WHERE word = ?", (word,))
    conn.commit()


def db_clear_dictionary():
    conn = _get_conn()
    conn.execute("DELETE FROM dictionary")
    conn.commit()


def db_import_dictionary(entries: Dict[str, str]):
    conn = _get_conn()
    for word, replacement in entries.items():
        conn.execute(
            "INSERT OR REPLACE INTO dictionary (word, replacement) VALUES (?, ?)",
            (word, replacement),
        )
    conn.commit()


def db_apply_dictionary(text: str) -> str:
    """Apply all dictionary replacements to text.
    
    Rules:
    - Case-insensitive matching
    - Only matches words preceded by: start-of-string, space, or hyphen
    - Only matches words followed by: end-of-string, space, or punctuation (!?.,;:)
    - Longer phrases are replaced first to avoid partial matches
    """
    d = db_get_dictionary()
    if not d:
        return text

    # Sort by length descending so longer phrases match first
    sorted_entries = sorted(d.items(), key=lambda x: len(x[0]), reverse=True)

    for word, repl in sorted_entries:
        escaped = re.escape(word)
        # Group 1 captures the left boundary (start-of-string, space, or hyphen)
        # The word itself is matched case-insensitively
        # Lookahead checks right boundary (end-of-string, space, or punctuation)
        pattern = r'(^|[\s\-])' + escaped + r'(?=$|[\s!?.,;:\-])'
        text = re.sub(pattern, lambda m: m.group(1) + repl, text, flags=re.IGNORECASE | re.MULTILINE)

    return text


# ============================================================
# Jobs
# ============================================================

def db_create_job(
    job_id: str,
    title: str,
    text: str,
    output_format: str = "mp3",
    output_bitrate_kbps: int = 128,
    voice_assignments: Optional[Dict] = None,
    tts_engine: str = "chatterbox_mtl_local",
    split_by_chapter: bool = False,
    chapters: Optional[List[str]] = None,
    total_chapters: int = 0,
) -> Dict[str, Any]:
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO jobs (
            job_id, title, text, status, progress,
            current_chunk, total_chunks, current_chapter, total_chapters,
            output_format, output_bitrate_kbps,
            voice_assignments_json, output_files_json,
            tts_engine, split_by_chapter, chapters_json,
            created_at
        ) VALUES (?, ?, ?, 'queued', 0, 0, 0, 0, ?, ?, ?, ?, '[]', ?, ?, ?, ?)""",
        (
            job_id, title, text,
            total_chapters,
            output_format, output_bitrate_kbps,
            json.dumps(voice_assignments or {}, ensure_ascii=False),
            tts_engine,
            1 if split_by_chapter else 0,
            json.dumps(chapters or [], ensure_ascii=False),
            now,
        ),
    )
    conn.commit()
    return _row_to_job(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())


def db_update_job(job_id: str, **kwargs):
    """Update one or more columns of a job."""
    conn = _get_conn()
    sets = []
    vals = []
    # Map special keys to JSON columns
    json_keys = {"voice_assignments": "voice_assignments_json", "output_files": "output_files_json", "chapters": "chapters_json"}
    for k, v in kwargs.items():
        col = json_keys.get(k, k)
        if k in json_keys:
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{col} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", vals)
    conn.commit()


def db_get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def db_get_jobs(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn()
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC", (status_filter,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [_row_to_job(r) for r in rows]


def db_get_active_job_count() -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM jobs WHERE status IN ('queued', 'processing', 'paused')"
    ).fetchone()
    return row["cnt"] if row else 0


def db_delete_job(job_id: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    return cur.rowcount > 0


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite Row to a clean dict."""
    d = dict(row)
    # Parse JSON columns
    d["voice_assignments"] = json.loads(d.pop("voice_assignments_json", "{}"))
    d["output_files"] = json.loads(d.pop("output_files_json", "[]"))
    d["chapters"] = json.loads(d.pop("chapters_json", "[]"))
    d["split_by_chapter"] = bool(d.get("split_by_chapter", 0))
    return d

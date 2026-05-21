"""
dedup.py — MeetToTicket AI
Idempotency layer: prevents the same transcript from generating
duplicate tickets even if the user submits twice.

Strategy: SHA-256 hash of the normalized transcript is stored in a
local SQLite database. On each submission we check the hash first.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/dedup_store.db")


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            hash        TEXT PRIMARY KEY,
            submitted_at TEXT NOT NULL,
            meeting_title TEXT,
            ticket_count INTEGER
        )
        """
    )
    conn.commit()
    return conn


def compute_hash(transcript: str) -> str:
    """
    Normalize (strip, lowercase, collapse whitespace) before hashing
    so minor formatting differences don't create false duplicates.
    """
    normalized = " ".join(transcript.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_duplicate(transcript_hash: str) -> bool:
    """Return True if this exact transcript was already processed."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT submitted_at FROM submissions WHERE hash = ?",
            (transcript_hash,),
        ).fetchone()
        if row:
            logger.info("Duplicate detected. Original submission: %s", row[0])
            return True
        return False
    except sqlite3.Error as e:
        logger.error("Dedup check failed: %s", e)
        return False  # Fail open: let it through if store is broken
    finally:
        conn.close()


def register_submission(
    transcript_hash: str,
    meeting_title: str = "",
    ticket_count: int = 0,
) -> None:
    """Record a successfully processed submission."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO submissions
                (hash, submitted_at, meeting_title, ticket_count)
            VALUES (?, ?, ?, ?)
            """,
            (
                transcript_hash,
                datetime.utcnow().isoformat(),
                meeting_title,
                ticket_count,
            ),
        )
        conn.commit()
        logger.info("Registered submission hash=%s title='%s'", transcript_hash[:8], meeting_title)
    except sqlite3.Error as e:
        logger.error("Failed to register submission: %s", e)
    finally:
        conn.close()


def get_submission_history() -> list[dict]:
    """Return all past submissions for display in the UI."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT hash, submitted_at, meeting_title, ticket_count "
            "FROM submissions ORDER BY submitted_at DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "hash": r[0][:8] + "…",
                "submitted_at": r[1],
                "meeting_title": r[2],
                "ticket_count": r[3],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()

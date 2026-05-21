"""
cache_manager.py — MeetToTicket AI
Graceful fallback: if Google Sheets is unavailable (rate limit, quota,
network issue), tickets are cached locally as JSON so no work is lost.
A retry mechanism can flush the cache when connectivity is restored.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR  = Path("data/cache")
CACHE_FILE = CACHE_DIR / "pending_tickets.json"


def _load_cache() -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return []
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Cache read failed: %s", e)
        return []


def _save_cache(data: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.critical("Cannot write cache to disk: %s", e)


def cache_tickets(
    analysis_dict: dict,
    transcript_hash: str,
    error_reason: str,
) -> None:
    """
    Persist a failed submission locally so it can be retried later.
    Called automatically when Google Sheets write fails.
    """
    cache = _load_cache()
    entry = {
        "cached_at":      datetime.utcnow().isoformat(),
        "transcript_hash": transcript_hash,
        "error_reason":   error_reason,
        "analysis":       analysis_dict,
        "retried":        False,
    }
    cache.append(entry)
    _save_cache(cache)
    logger.warning(
        "Cached %d tickets locally (hash=%s). Reason: %s",
        len(analysis_dict.get("tickets", [])),
        transcript_hash[:8],
        error_reason,
    )


def get_pending_cache() -> list[dict]:
    """Return all un-retried cached entries."""
    return [e for e in _load_cache() if not e.get("retried")]


def mark_retried(transcript_hash: str) -> None:
    """Mark a cached entry as successfully retried and written to Sheets."""
    cache = _load_cache()
    for entry in cache:
        if entry["transcript_hash"] == transcript_hash:
            entry["retried"] = True
    _save_cache(cache)


def pending_count() -> int:
    return len(get_pending_cache())


def export_cache_as_json(transcript_hash: Optional[str] = None) -> str:
    """
    Return the raw JSON of cached tickets — used for the
    'Download as JSON' fallback button in the UI.
    """
    cache = _load_cache()
    if transcript_hash:
        cache = [e for e in cache if e["transcript_hash"] == transcript_hash]
    return json.dumps(cache, indent=2, ensure_ascii=False)

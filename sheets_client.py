"""
sheets_client.py — MeetToTicket AI
Async Google Sheets writer with:
  - asyncio.to_thread() wrapping for non-blocking Streamlit execution
  - Exponential backoff retry on rate-limit (429) errors
  - Graceful fallback to local JSON cache on quota exhaustion
  - Batch row insertion to minimise API calls
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from models import MeetingAnalysis, Ticket
from cache_manager import cache_tickets

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

WORKSHEET_NAME   = "MeetToTicket Board"
SUMMARY_SHEET    = "Meeting Summaries"
MAX_RETRIES      = 4
BASE_BACKOFF_SEC = 2.0      # doubles each retry: 2, 4, 8, 16 seconds


# ─────────────────────────────────────────────
# Auth helper
# ─────────────────────────────────────────────

def _get_client() -> gspread.Client:
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_JSON", "credentials.json")
    if not Path(creds_path).exists():
        raise FileNotFoundError(
            f"Google credentials not found at '{creds_path}'. "
            "Download from Google Cloud Console → Service Accounts → Keys."
        )
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    headers: List[str],
) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(title)
    except WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=500, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
        logger.info("Created worksheet '%s'", title)
    return ws


# ─────────────────────────────────────────────
# Retry wrapper
# ─────────────────────────────────────────────

def _with_retry(fn, *args, **kwargs):
    """
    Execute fn(*args, **kwargs) with exponential backoff on rate-limit errors.
    Raises the last exception if all retries are exhausted.
    """
    delay = BASE_BACKOFF_SEC
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            status = getattr(e.response, "status_code", None)
            if status == 429:
                logger.warning(
                    "Rate limit hit (attempt %d/%d). Backing off %.0fs…",
                    attempt, MAX_RETRIES, delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise RuntimeError(f"Google Sheets: rate limit persisted after {MAX_RETRIES} retries.")


# ─────────────────────────────────────────────
# Sync write logic (runs in thread pool)
# ─────────────────────────────────────────────

def _sync_write_to_sheets(
    spreadsheet_id: str,
    analysis: MeetingAnalysis,
    transcript_hash: str,
) -> dict:
    """
    Blocking write to Google Sheets.  Called via asyncio.to_thread so it
    does NOT block the Streamlit event loop.

    Returns a dict with outcome info surfaced in the UI.
    """
    client      = _get_client()
    spreadsheet = client.open_by_key(spreadsheet_id)

    # ── Ticket board sheet ─────────────────────────────────────────────
    board_ws = _get_or_create_worksheet(
        spreadsheet,
        WORKSHEET_NAME,
        Ticket.sheet_headers(),
    )

    rows = [t.to_sheet_row() for t in analysis.tickets]
    _with_retry(board_ws.append_rows, rows, value_input_option="USER_ENTERED")
    logger.info("Wrote %d ticket rows to '%s'", len(rows), WORKSHEET_NAME)

    # ── Meeting summary sheet ──────────────────────────────────────────
    summary_ws = _get_or_create_worksheet(
        spreadsheet,
        SUMMARY_SHEET,
        ["Date", "Meeting Title", "Participants", "Summary",
         "Tickets Created", "Hash", "Unowned Actions", "Risks"],
    )
    summary_row = [
        analysis.meeting_date or "—",
        analysis.meeting_title,
        ", ".join(analysis.participants),
        analysis.summary,
        len(analysis.tickets),
        transcript_hash[:8],
        " | ".join(analysis.action_items_without_owner) or "—",
        " | ".join(analysis.risks_flagged) or "—",
    ]
    _with_retry(summary_ws.append_row, summary_row, value_input_option="USER_ENTERED")

    return {
        "success":       True,
        "tickets_written": len(rows),
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
    }


# ─────────────────────────────────────────────
# Public async API
# ─────────────────────────────────────────────

async def write_analysis_to_sheets(
    spreadsheet_id: str,
    analysis: MeetingAnalysis,
    transcript_hash: str,
) -> dict:
    """
    Non-blocking entry point for Streamlit.
    Falls back to local cache if Sheets is unavailable.
    """
    try:
        result = await asyncio.to_thread(
            _sync_write_to_sheets,
            spreadsheet_id,
            analysis,
            transcript_hash,
        )
        return result

    except FileNotFoundError as e:
        reason = str(e)
        logger.error("Credentials missing: %s", reason)
        cache_tickets(analysis.model_dump(), transcript_hash, reason)
        return {"success": False, "error": reason, "cached": True}

    except SpreadsheetNotFound:
        reason = f"Spreadsheet '{spreadsheet_id}' not found or not shared with service account."
        logger.error(reason)
        cache_tickets(analysis.model_dump(), transcript_hash, reason)
        return {"success": False, "error": reason, "cached": True}

    except RuntimeError as e:
        # Rate limit exhausted after all retries
        reason = str(e)
        logger.error("Sheets write failed permanently: %s", reason)
        cache_tickets(analysis.model_dump(), transcript_hash, reason)
        return {"success": False, "error": reason, "cached": True}

    except Exception as e:
        reason = f"Unexpected error: {type(e).__name__}: {e}"
        logger.exception("Unhandled sheets error")
        cache_tickets(analysis.model_dump(), transcript_hash, reason)
        return {"success": False, "error": reason, "cached": True}


# ─────────────────────────────────────────────
# Retry pending cached tickets
# ─────────────────────────────────────────────

async def retry_cached_tickets(spreadsheet_id: str) -> dict:
    """
    Attempt to flush all locally cached (failed) analyses to Sheets.
    Call from the UI 'Retry Pending' button.
    """
    from cache_manager import get_pending_cache, mark_retried
    from models import MeetingAnalysis

    pending = get_pending_cache()
    if not pending:
        return {"flushed": 0, "failed": 0}

    flushed = failed = 0
    for entry in pending:
        try:
            analysis = MeetingAnalysis.model_validate(entry["analysis"])
            result   = await write_analysis_to_sheets(
                spreadsheet_id, analysis, entry["transcript_hash"]
            )
            if result["success"]:
                mark_retried(entry["transcript_hash"])
                flushed += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("Retry failed for %s: %s", entry["transcript_hash"][:8], e)
            failed += 1

    return {"flushed": flushed, "failed": failed}

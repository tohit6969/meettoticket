# 🎫 MeetToTicket AI

> **Meetings → Structured Tickets. Automatically.**
>
> Paste a transcript. MeetToTicket AI extracts action items, assigns owners,
> infers priorities, writes acceptance criteria, and pushes everything to your
> Google Sheets board — with zero manual effort.

---

## Architecture

```
User pastes transcript
        │
        ▼
┌──────────────────────────────────────────────────────┐
│                  Streamlit Frontend                  │
│  Tab 1: Submit  │  Tab 2: Board  │  Tab 3: History  │
└─────────────────────────┬────────────────────────────┘
                          │
                          ▼
           ┌──────────────────────────┐
           │   Deduplication Layer    │  ← SHA-256 hash → SQLite
           │   (dedup.py)             │     Blocks duplicate submits
           └──────────────┬───────────┘
                          │
                          ▼
           ┌──────────────────────────┐
           │   Gemini Client          │  ← Pydantic Structured Output
           │   (gemini_client.py)     │     Forces JSON schema at API layer
           └──────────────┬───────────┘
                          │
                    MeetingAnalysis
                    (Pydantic model)
                          │
                          ▼
           ┌──────────────────────────┐
           │   Async Sheets Writer    │  ← asyncio.to_thread (non-blocking)
           │   (sheets_client.py)     │     Exponential backoff on 429s
           └──────┬───────────┬───────┘
                  │           │
            Success        Failure
                  │           │
             Sheets ✅    Local Cache 📦
                          (cache_manager.py)
                          Retry later
```

---

## Senior Engineering Patterns Implemented

### 1. Structured Outputs via Pydantic
**Problem**: Open-ended JSON prompting breaks — the LLM returns extra text, wrong keys, or nested objects in wrong shapes.

**Solution**: `gemini_client.py` passes `response_mime_type="application/json"` to the Gemini API. The response is then validated against `MeetingAnalysis` (a strict Pydantic v2 model). If the LLM violates the schema at any level, a `ValidationError` is raised immediately — not silently corrupted downstream.

```python
# models.py — schema is the source of truth
class Ticket(BaseModel):
    title: str = Field(..., min_length=5, max_length=120)
    priority: Priority                    # enum, not free string
    acceptance_criteria: List[str]        # validated for min length
    ...

class MeetingAnalysis(BaseModel):
    tickets: List[Ticket] = Field(..., min_length=1)
    ...

# gemini_client.py — LLM forced to comply at API layer
response = model.generate_content(
    prompt,
    generation_config=GenerationConfig(
        response_mime_type="application/json",  # ← key
    )
)
analysis = MeetingAnalysis.model_validate(json.loads(response.text))
```

---

### 2. Asynchronous Request Handling (`asyncio`)
**Problem**: Writing 20 tickets to Google Sheets row-by-row synchronously blocks the Streamlit event loop. For long transcripts this causes UI timeouts.

**Solution**: `sheets_client.py` wraps all blocking gspread I/O in `asyncio.to_thread()`, which runs it in a thread pool without blocking the main event loop. Rows are batch-inserted in a single `append_rows()` call instead of N individual API calls.

```python
# sheets_client.py
async def write_analysis_to_sheets(...) -> dict:
    result = await asyncio.to_thread(      # ← non-blocking
        _sync_write_to_sheets,
        spreadsheet_id, analysis, transcript_hash,
    )
    return result

def _sync_write_to_sheets(...):
    board_ws.append_rows(rows, ...)        # ← batch insert, not row-by-row
```

---

### 3. Idempotency & Deduplication
**Problem**: Double-clicking Submit creates duplicate tickets on the board.

**Solution**: `dedup.py` computes a SHA-256 hash of the normalized transcript before doing any work. The hash is checked against a local SQLite store. If it already exists, the pipeline exits immediately with a clear user message.

```python
# dedup.py
def compute_hash(transcript: str) -> str:
    normalized = " ".join(transcript.lower().split())   # normalize
    return hashlib.sha256(normalized.encode()).hexdigest()

def is_duplicate(hash: str) -> bool:
    # queries SQLite — returns True if seen before
    ...
```

Minor whitespace or casing differences between submissions are normalized away, so they don't bypass the dedup check.

---

### 4. Graceful Exception Handling & Fallbacks
**Problem**: Google Sheets API has quota limits. A raw traceback crashing the UI is unacceptable in production.

**Solution** (`sheets_client.py` + `cache_manager.py`):

- **Retry with exponential backoff** on HTTP 429 (rate limit). Waits 2s, 4s, 8s, 16s before giving up.
- **Cache fallback**: If all retries are exhausted (or credentials are missing, or the Sheet doesn't exist), tickets are serialized to `data/cache/pending_tickets.json`. The UI shows a yellow warning instead of a stack trace.
- **Retry Pending** button in the sidebar flushes the cache to Sheets when connectivity is restored.
- All errors are logged to `data/meettoticket.log` for post-mortem debugging.

```python
# Retry with exponential backoff
def _with_retry(fn, *args, **kwargs):
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            if e.response.status_code == 429:
                time.sleep(delay); delay *= 2
            else:
                raise
    raise RuntimeError("Rate limit persisted after all retries")

# Fallback to local cache
except RuntimeError as e:
    cache_tickets(analysis.model_dump(), transcript_hash, str(e))
    return {"success": False, "cached": True, "error": str(e)}
```

---

## Setup

### Prerequisites
- Python 3.11+
- A Gemini API key (Google AI Studio — free tier works)
- A Google Cloud project with Sheets API + Drive API enabled
- A service account JSON key

### Step 1 — Clone & install

```bash
git clone https://github.com/yourname/meettoticket
cd meettoticket
pip install -r requirements.txt
```

### Step 2 — Configure credentials

```bash
cp .env.example .env
# Fill in GEMINI_API_KEY, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON
```

Place your Google service account JSON file as `credentials.json` (or update the path in `.env`).

**Share your Google Sheet** with the service account email (e.g. `meettoticket@your-project.iam.gserviceaccount.com`) with **Editor** access.

### Step 3 — Run

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## Project Structure

```
meettoticket/
├── app.py              ← Streamlit UI & pipeline orchestration
├── models.py           ← Pydantic schemas (Ticket, MeetingAnalysis)
├── gemini_client.py    ← LLM structured output extraction
├── sheets_client.py    ← Async Google Sheets writer + retry logic
├── dedup.py            ← SHA-256 deduplication via SQLite
├── cache_manager.py    ← Local JSON fallback cache
├── requirements.txt
├── .env.example
├── credentials.json    ← (your file, not committed)
└── data/               ← Auto-created at runtime
    ├── dedup_store.db
    ├── meettoticket.log
    └── cache/
        └── pending_tickets.json
```

---

## Real-World Problem This Solves

In IT teams, meeting action items fall through the cracks because:
- Manual ticket creation takes time and gets skipped
- No one owns the task of turning standup notes into Jira/ADO items
- Duplicate tickets get created when someone tries to re-create from memory

MeetToTicket AI solves all three: it converts any meeting transcript into
structured, assigned, priority-sorted tickets in under 30 seconds —
with deduplication, async I/O, and offline resilience built in.

---

## Made for

Edoofa Tech, Data & Systems Lead Application — Chitkara University, May 2026.
Demonstrating: Python automation, AI integration, system design, and production-grade engineering discipline.

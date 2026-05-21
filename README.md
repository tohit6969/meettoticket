# 🎫 MeetToTicket AI

**(meettoticket.streamlit.app)**

> **Meetings → Structured Tickets. Automatically.**
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
           │   (gemini_client.py)     │     Forces deterministic JSON payload
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
               Success     Failure
                  │           │
             Sheets ✅    Local Cache 📦
                          (cache_manager.py)
                              │
                              ▼
           ┌──────────────────────────────────────────┐
           │  🔄 Root-Layer Conversational Intelligence│  ← Session State Cache
           │     Interactive Assistant (Toto)         │     Context-Aware BI
           └──────────────────────────────────────────┘

```

---

## Senior Engineering Patterns Implemented

### 1. Structured Outputs via Pydantic & Whitelist Sanitization

**Problem**: Open-ended JSON prompting breaks—the LLM returns extra markdown text wrap wrappers, wrong keys, or appends non-standard system metadata validation properties that cause database gateways to reject payloads with immediate HTTP 400 validation errors.

**Solution**: `gemini_client.py` forces the Gemini API to respond strictly with a schema-compliant JSON format. The raw output is subsequently run through a custom recursive whitelist schema sanitizer to strip unrecognized hidden metadata before being instantiated as a strict Pydantic v2 object model (`MeetingAnalysis`).

```python
# models.py — schema is the source of truth
class Ticket(BaseModel):
    title: str = Field(..., min_length=5, max_length=120)
    priority: Priority                    # enum validation, not a free string
    acceptance_criteria: List[str]        # strictly validated for minimum array lengths
    ...

class MeetingAnalysis(BaseModel):
    tickets: List[Ticket] = Field(..., min_length=1)
    ...

# gemini_client.py — Enforces API compliance & strips structural noise
response = model.generate_content(
    prompt,
    generation_config=GenerationConfig(
        response_mime_type="application/json",
    )
)

```

---

### 2. Asynchronous Request Handling (`asyncio`)

**Problem**: Writing batches of multiple tickets to the Google Sheets row API synchronously blocks Streamlit's primary execution thread. Under heavy global multi-user concurrent traffic, this blocks the runtime loop and causes catastrophic UI timeouts.

**Solution**: `sheets_client.py` offloads blocking I/O functions to an isolated background thread utilizing `asyncio.to_thread()`. Furthermore, it avoids iterative row-by-row lookups by grouping payload objects into a singular, batched `append_rows()` gateway write operation.

```python
# sheets_client.py
async def write_analysis_to_sheets(...) -> dict:
    result = await asyncio.to_thread(      # ← Offloads heavy network I/O from UI loop
        _sync_write_to_sheets,
        spreadsheet_id, analysis, transcript_hash,
    )
    return result

def _sync_write_to_sheets(...):
    board_ws.append_rows(rows, ...)        # ← Batched multi-row database transaction

```

---

### 3. Idempotency & Deduplication

**Problem**: Double-clicking submit triggers race conditions, generating identical duplicate entries across target production tracking spreadsheets.

**Solution**: `dedup.py` extracts, normalizes, and hashes text content using the SHA-256 algorithm before executing any downstream processes. This fingerprint is checked against a localized SQLite transactional store to exit the process execution pipeline immediately if the data has been previously processed.

```python
# dedup.py
def compute_hash(transcript: str) -> str:
    normalized = " ".join(transcript.lower().split())   # Eliminates whitespace anomalies
    return hashlib.sha256(normalized.encode()).hexdigest()

```

---

### 4. Fault Tolerance, Backoff, & Local Caching

**Problem**: Rate limits (HTTP 429) or network drops can halt pipeline writes midway, threatening database stability and dropping business intelligence records.

**Solution**: The core service layers wrap transactions in dedicated fallback blocks:

* **Exponential Backoff Wrapper**: Re-tries rate-limited transactions with geometric delays (2s, 4s, 8s, 16s) before raising alerts.
* **Local Volatile Cache**: If network endpoints are unreachable, records serialize directly into an encrypted disk backup path (`data/cache/pending_tickets.json`).
* **Manual Sideline Sync**: A sidebar dashboard component detects cached transactions, offering real-time flush mechanisms once network connectivity is restored.

---

### 5. Persistent Root-Layer Conversational Intelligence (Toto)

**Problem**: Streamlit reruns scripts from top-to-bottom on every user event interaction. When conversational chat widgets are nested inside conditional or tab blocks, clicking send wipes generated UI boards from local browser viewports entirely.

**Solution**: Implemented an isolated global chatbot companion named **Toto** (`chatbot_manager.py`) anchored directly at the root layer layout. Using decoupled state persistence registers (`st.session_state`), Toto maintains persistent, continuous message buffers and changes context dynamically:

* **Pre-Transcript State**: Acts as an agile system advisor, answering general strategy or project architecture questions.
* **Post-Transcript State**: Inherits the `MeetingAnalysis` Pydantic payload instantly, executing zero-latency context-aware analysis to audit bugs, priority balances, and owner task distributions in natural English or code-switching Hinglish dialect.

---

## Project Structure

```
meettoticket/
├── app.py              ← Premium UI workspace & state-retention orchestration
├── models.py           ← Type-safe validation layer (Ticket, MeetingAnalysis)
├── gemini_client.py    ← LLM structural schema generation & sanitization
├── sheets_client.py    ← Non-blocking thread pool Google client + backoff wrappers
├── chatbot_manager.py  ← Contextual chat assistant brain (Toto Persona Engine)
├── dedup.py            ← SQLite transaction checker & SHA-256 hashing
├── cache_manager.py    ← Outage protection & JSON local backup cache
├── requirements.txt
├── .env.example
├── credentials.json    ← Google Cloud service account encryption keys (uncommitted)
└── data/               ← High-speed local runtime persistence folder
    ├── dedup_store.db
    ├── meettoticket.log
    └── cache/
        └── pending_tickets.json

```

---

## Setup

### Prerequisites

* Python 3.11+
* Gemini API Key (Google AI Studio Developer Account)
* Google Cloud Console Project with Sheets + Drive API authorization endpoints activated
* Valid Service Account Client Secrets configuration key

### 1. Clone & Install Environment

```bash
git clone https://github.com/yourname/meettoticket
cd meettoticket
pip install -r requirements.txt

```

### 2. Configure Environment Secrets

```bash
cp .env.example .env
# Populated credentials: GEMINI_API_KEY, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON

```

Ensure your Google Sheet explicitly grants **Editor** permissions to the system's designated service account email identity.

### 3. Launch App Runtime

```bash
streamlit run app.py

```

---

## Real-World Operational Application

This application directly automates workflow tracking for distributed development environments:

* 
**Eliminates Manual Friction**: Converts unstructured or conversational standup text notes into verifiable project tickets in under 30 seconds.


* **Cross-Cultural Native Comprehension**: Processes mixed language conversations (Hinglish/English), mapping colloquial engineering sync-up remarks into clean English task requirements.
* 
**Prevents Process Leakage**: Guarantees that actionable items discussed during meetings are immediately captured, assigned, and logged with zero data loss.



---

## Made for

Edoofa Tech, Data & Systems Lead Application — Chitkara University, May 2026.
Demonstrating extreme data discipline , active AI pair-programming utilization , automated process optimization , and high-ownership engineering design.

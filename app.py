"""
app.py — MeetToTicket AI
Main Streamlit application.
Pipeline: Transcript → Dedup → Gemini (Structured Output) → Sheets (Async)
"""

import asyncio
import json
import logging
import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from cache_manager import export_cache_as_json, pending_count
from dedup import compute_hash, get_submission_history, is_duplicate, register_submission
from gemini_client import extract_tickets_from_transcript
from models import MeetingAnalysis, Priority, TicketStatus
from sheets_client import retry_cached_tickets, write_analysis_to_sheets

# ─────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/meettoticket.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="MeetToTicket AI",
    page_icon="🎫",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

    h1, h2, h3 { font-family: 'Space Mono', monospace; }

    .stApp { background: #0d1117; color: #e6edf3; }

    .metric-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px 20px;
        text-align: center;
    }
    .metric-card h3 { margin: 0; font-size: 2rem; color: #58a6ff; }
    .metric-card p  { margin: 4px 0 0; font-size: 0.85rem; color: #8b949e; }

    .ticket-card {
        background: #161b22;
        border: 1px solid #21262d;
        border-left: 3px solid #58a6ff;
        border-radius: 6px;
        padding: 14px 18px;
        margin-bottom: 10px;
    }
    .ticket-card.critical { border-left-color: #f85149; }
    .ticket-card.high     { border-left-color: #e3b341; }
    .ticket-card.medium   { border-left-color: #58a6ff; }
    .ticket-card.low      { border-left-color: #3fb950; }

    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 4px;
    }
    .badge-critical { background: #3d1d1d; color: #f85149; }
    .badge-high     { background: #2d2200; color: #e3b341; }
    .badge-medium   { background: #1d2d45; color: #58a6ff; }
    .badge-low      { background: #1a2d1a; color: #3fb950; }
    .badge-type     { background: #21262d; color: #8b949e; }

    .warning-box {
        background: #2d2200;
        border: 1px solid #e3b341;
        border-radius: 6px;
        padding: 12px 16px;
        color: #e3b341;
        margin: 8px 0;
    }
    .success-box {
        background: #1a2d1a;
        border: 1px solid #3fb950;
        border-radius: 6px;
        padding: 12px 16px;
        color: #3fb950;
        margin: 8px 0;
    }
    .info-box {
        background: #1d2d45;
        border: 1px solid #58a6ff;
        border-radius: 6px;
        padding: 12px 16px;
        color: #58a6ff;
        margin: 8px 0;
    }
    div[data-testid="stTextArea"] textarea {
        background: #0d1117 !important;
        border: 1px solid #30363d !important;
        color: #e6edf3 !important;
        font-family: 'Space Mono', monospace;
        font-size: 0.85rem;
    }
    .stButton > button {
        background: #238636;
        color: white;
        border: none;
        font-family: 'DM Sans', sans-serif;
        font-weight: 600;
        border-radius: 6px;
    }
    .stButton > button:hover { background: #2ea043; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

PRIORITY_BADGE = {
    "Critical": "badge-critical",
    "High":     "badge-high",
    "Medium":   "badge-medium",
    "Low":      "badge-low",
}

def priority_badge(p: str) -> str:
    cls = PRIORITY_BADGE.get(p, "badge-type")
    return f'<span class="badge {cls}">{p}</span>'


def render_ticket_card(ticket) -> None:
    prio_class = ticket.priority.value.lower()
    ac_html = "".join(f"<li>{c}</li>" for c in ticket.acceptance_criteria)
    tags_html = (
        " ".join(f'<span class="badge badge-type">{t}</span>' for t in ticket.tags)
        if ticket.tags else ""
    )
    st.markdown(
        f"""
        <div class="ticket-card {prio_class}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <strong style="font-size:1rem;color:#e6edf3;">{ticket.title}</strong>
                <span style="font-size:0.75rem;color:#8b949e;font-family:monospace;">{ticket.ticket_id}</span>
            </div>
            <div style="margin:6px 0;">
                {priority_badge(ticket.priority.value)}
                <span class="badge badge-type">{ticket.ticket_type.value}</span>
                <span class="badge badge-type">👤 {ticket.assigned_to}</span>
                {"<span class='badge badge-type'>📅 " + ticket.due_date + "</span>" if ticket.due_date else ""}
            </div>
            <p style="color:#8b949e;font-size:0.85rem;margin:6px 0;">{ticket.description}</p>
            <details>
                <summary style="cursor:pointer;color:#58a6ff;font-size:0.8rem;">Acceptance Criteria</summary>
                <ul style="margin:6px 0;padding-left:18px;color:#c9d1d9;font-size:0.82rem;">{ac_html}</ul>
            </details>
            {f'<div style="margin-top:6px;">{tags_html}</div>' if tags_html else ""}
            <div style="margin-top:8px;padding-top:8px;border-top:1px solid #21262d;">
                <em style="font-size:0.78rem;color:#484f58;">💬 "{ticket.source_quote}"</em>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


async def run_pipeline(
    transcript: str,
    spreadsheet_id: str,
    custom_instructions: str,
) -> dict:
    """Full async pipeline: dedup → LLM → Sheets."""

    # 1. Deduplication
    tx_hash = compute_hash(transcript)
    if is_duplicate(tx_hash):
        return {
            "status":  "duplicate",
            "message": "This exact transcript was already processed. No duplicate tickets created.",
        }

    # 2. LLM extraction (runs in Streamlit's thread but is fast enough; heavy IO is sheets)
    try:
        analysis: MeetingAnalysis = extract_tickets_from_transcript(
            transcript, custom_instructions or None
        )
    except (ValueError, RuntimeError, EnvironmentError) as e:
        return {"status": "llm_error", "message": str(e)}

    # 3. Async Sheets write
    sheets_result = await write_analysis_to_sheets(spreadsheet_id, analysis, tx_hash)

    # 4. Register in dedup store (even if Sheets failed — we cached locally)
    register_submission(tx_hash, analysis.meeting_title, len(analysis.tickets))

    return {
        "status":        "success",
        "analysis":      analysis,
        "sheets_result": sheets_result,
        "tx_hash":       tx_hash,
    }


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🎫 MeetToTicket AI")
    st.markdown("*Transcripts → Tickets, automatically.*")
    st.divider()

    spreadsheet_id = st.text_input(
        "Google Sheet ID",
        value=os.environ.get("SPREADSHEET_ID", ""),
        placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
        help="Found in the Sheet URL: /spreadsheets/d/**{ID}**/edit",
    )

    st.divider()
    st.markdown("**⚙️ Pipeline Status**")

    pending = pending_count()
    if pending:
        st.markdown(
            f'<div class="warning-box">⚠️ {pending} ticket batch(es) cached locally — Sheets unreachable.</div>',
            unsafe_allow_html=True,
        )
        if st.button("🔄 Retry Pending to Sheets"):
            if spreadsheet_id:
                result = asyncio.run(retry_cached_tickets(spreadsheet_id))
                st.success(f"Flushed {result['flushed']}, failed {result['failed']}.")
            else:
                st.error("Enter a Spreadsheet ID first.")
    else:
        st.markdown('<div class="success-box">✅ No pending cache.</div>', unsafe_allow_html=True)

    st.divider()
    st.caption("Built for Edoofa Tech Role Application · May 2026")


# ─────────────────────────────────────────────
# Main tabs
# ─────────────────────────────────────────────

tab_submit, tab_board, tab_history = st.tabs(
    ["📝 Submit Transcript", "📋 Ticket Board", "🕐 History"]
)


# ══════════════════════════════════════════════
# TAB 1 — Submit Transcript
# ══════════════════════════════════════════════

with tab_submit:
    st.markdown("# 📝 Submit Meeting Transcript")
    st.markdown(
        "Paste your meeting notes or transcript below. "
        "MeetToTicket AI will extract tickets, assign owners, and push to your board."
    )

    col1, col2 = st.columns([2, 1])

    with col1:
        transcript_text = st.text_area(
            "Meeting Transcript",
            height=320,
            placeholder=(
                "Example:\n\n"
                "Rahul: We need to fix the login redirect bug before release. I'll handle it by Friday.\n"
                "Priya: I'll design the new dashboard wireframes by end of this week.\n"
                "Manager: Rahul, also look into the API timeout issue — it's blocking QA.\n"
                "Dev: Should we add dark mode? Not urgent though.\n"
            ),
        )

    with col2:
        st.markdown("**Options**")
        custom_instructions = st.text_area(
            "Custom instructions (optional)",
            height=100,
            placeholder="e.g. All tickets should default to High priority. Assign unowned tasks to Priya.",
        )
        st.markdown("**Priority filter preview**")
        for p in Priority:
            st.markdown(priority_badge(p.value), unsafe_allow_html=True)

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        submit_clicked = st.button("🚀 Generate Tickets", use_container_width=True)
    with col_info:
        if not spreadsheet_id:
            st.markdown(
                '<div class="info-box">ℹ️ No Spreadsheet ID set — tickets will be cached locally.</div>',
                unsafe_allow_html=True,
            )

    if submit_clicked:
        if not transcript_text.strip():
            st.error("Please paste a transcript before submitting.")
        elif len(transcript_text.strip()) < 50:
            st.error("Transcript is too short. Paste the actual meeting notes.")
        else:
            os.makedirs("data", exist_ok=True)

            with st.status("⚙️ Running MeetToTicket pipeline…", expanded=True) as status:
                st.write("🔍 Checking for duplicate submissions…")
                result = asyncio.run(
                    run_pipeline(transcript_text, spreadsheet_id, custom_instructions)
                )
                st.write("✅ Pipeline complete.")
                status.update(label="Done!", state="complete")

            # ── Render results ──────────────────────────────────────────

            if result["status"] == "duplicate":
                st.markdown(
                    f'<div class="warning-box">⚠️ {result["message"]}</div>',
                    unsafe_allow_html=True,
                )

            elif result["status"] in ("llm_error",):
                st.error(f"❌ AI extraction failed: {result['message']}")
                st.info("Check your GEMINI_API_KEY and try again.")

            elif result["status"] == "success":
                analysis: MeetingAnalysis = result["analysis"]
                sheets_res = result["sheets_result"]

                # Success / warning banner
                if sheets_res.get("success"):
                    st.markdown(
                        f'<div class="success-box">✅ {len(analysis.tickets)} tickets written to Google Sheets.'
                        f' <a href="{sheets_res["spreadsheet_url"]}" target="_blank">'
                        f'Open Sheet →</a></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="warning-box">⚠️ Sheets unavailable: {sheets_res.get("error", "Unknown error")}'
                        f"<br>Tickets cached locally — use 'Retry Pending' when connectivity is restored."
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                # Meeting summary
                st.markdown(f"### 📋 {analysis.meeting_title}")
                c1, c2, c3, c4 = st.columns(4)
                for col, val, label in [
                    (c1, len(analysis.tickets),                   "Tickets Created"),
                    (c2, len(analysis.participants),              "Participants"),
                    (c3, len(analysis.action_items_without_owner),"Unowned Actions"),
                    (c4, len(analysis.risks_flagged),             "Risks Flagged"),
                ]:
                    col.markdown(
                        f'<div class="metric-card"><h3>{val}</h3><p>{label}</p></div>',
                        unsafe_allow_html=True,
                    )

                st.markdown(f"> {analysis.summary}")

                # Risks
                if analysis.risks_flagged:
                    with st.expander("⚠️ Risks & Blockers Flagged"):
                        for r in analysis.risks_flagged:
                            st.markdown(f"- {r}")

                # Unowned actions
                if analysis.action_items_without_owner:
                    with st.expander("🔍 Actions Without Owner (Not ticketed)"):
                        for a in analysis.action_items_without_owner:
                            st.markdown(f"- {a}")

                # Tickets
                st.markdown(f"### 🎫 Generated Tickets ({len(analysis.tickets)})")
                for ticket in analysis.tickets:
                    render_ticket_card(ticket)

                # Download JSON
                st.divider()
                st.download_button(
                    "⬇️ Download as JSON",
                    data=analysis.model_dump_json(indent=2),
                    file_name=f"tickets_{result['tx_hash'][:8]}.json",
                    mime="application/json",
                )

                # Store in session for board tab
                st.session_state["last_analysis"] = analysis


# ══════════════════════════════════════════════
# TAB 2 — Ticket Board
# ══════════════════════════════════════════════

with tab_board:
    st.markdown("# 📋 Ticket Board")

    if "last_analysis" not in st.session_state:
        st.info("Submit a transcript first to see tickets here.")
    else:
        analysis: MeetingAnalysis = st.session_state["last_analysis"]

        # Filter bar
        col_p, col_a, col_t = st.columns(3)
        with col_p:
            filter_priority = st.multiselect(
                "Priority", [p.value for p in Priority], default=[]
            )
        with col_a:
            all_owners = sorted({t.assigned_to for t in analysis.tickets})
            filter_owner = st.multiselect("Assigned To", all_owners, default=[])
        with col_t:
            filter_status = st.multiselect(
                "Status", [s.value for s in TicketStatus], default=[]
            )

        filtered = analysis.tickets
        if filter_priority:
            filtered = [t for t in filtered if t.priority.value in filter_priority]
        if filter_owner:
            filtered = [t for t in filtered if t.assigned_to in filter_owner]
        if filter_status:
            filtered = [t for t in filtered if t.status.value in filter_status]

        st.markdown(f"**Showing {len(filtered)} / {len(analysis.tickets)} tickets**")
        st.divider()

        if not filtered:
            st.warning("No tickets match your filters.")
        else:
            # Group by assignee
            by_owner: dict = {}
            for t in filtered:
                by_owner.setdefault(t.assigned_to, []).append(t)

            for owner, tickets in by_owner.items():
                st.markdown(f"#### 👤 {owner} ({len(tickets)} ticket{'s' if len(tickets)>1 else ''})")
                for ticket in tickets:
                    render_ticket_card(ticket)
                st.divider()


# ══════════════════════════════════════════════
# TAB 3 — Submission History
# ══════════════════════════════════════════════

with tab_history:
    st.markdown("# 🕐 Submission History")
    st.caption("All processed transcripts from this machine (stored in local SQLite).")

    history = get_submission_history()
    if not history:
        st.info("No submissions recorded yet.")
    else:
        st.dataframe(
            history,
            use_container_width=True,
            column_config={
                "hash":          st.column_config.TextColumn("Hash (short)"),
                "submitted_at":  st.column_config.TextColumn("Submitted At (UTC)"),
                "meeting_title": st.column_config.TextColumn("Meeting Title"),
                "ticket_count":  st.column_config.NumberColumn("Tickets", format="%d"),
            },
        )

    st.divider()
    pending = pending_count()
    if pending:
        st.markdown(f"**📦 Locally Cached (Not yet in Sheets): {pending}**")
        st.download_button(
            "⬇️ Download Pending Cache",
            data=export_cache_as_json(),
            file_name="meettoticket_pending_cache.json",
            mime="application/json",
        )

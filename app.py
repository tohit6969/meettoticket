"""
app.py — MeetToTicket AI
Main Streamlit application.
Pipeline: Transcript → Dedup → Gemini (Structured Output) → Sheets (Async) → Global Chatbot (Toto)
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

# NATIVE INTEGRATION: Import the chatbot assistant utility module
from chatbot_manager import answer_ticket_query

# ─────────────────────────────────────────────
# Bootstrap & System Logging Setup
# ─────────────────────────────────────────────

load_dotenv()
os.makedirs("data", exist_ok=True) 

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
# Custom Premium SaaS UI Styling Layouts (CSS)
# ─────────────────────────────────────────────

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] { 
        font-family: 'Plus Jakarta Sans', sans-serif; 
    }

    h1, h2, h3, h4, h5, h6 { 
        font-family: 'Space Mono', monospace; 
        letter-spacing: -0.5px;
    }

    .stApp { 
        background: #090d13; 
        color: #e6edf3; 
    }

    /* Modern Minimalist Metrics */
    .metric-card {
        background: #121824;
        border: 1px solid #212e46;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        transition: all 0.25s ease-in-out;
    }
    .metric-card:hover {
        transform: translateY(-4px);
        border-color: #58a6ff;
        box-shadow: 0 8px 20px rgba(88, 166, 255, 0.15);
    }
    .metric-card h3 { 
        margin: 0; 
        font-size: 2.2rem; 
        color: #58a6ff; 
        font-weight: 700;
    }
    .metric-card p { 
        margin: 6px 0 0; 
        font-size: 0.9rem; 
        color: #8b949e; 
        font-weight: 500;
    }

    /* Sleek Elevated Tickets */
    .ticket-card {
        background: #121824;
        border: 1px solid #1e293b;
        border-left: 4px solid #58a6ff;
        border-radius: 10px;
        padding: 16px 22px;
        margin-bottom: 14px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.15);
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .ticket-card:hover {
        transform: scale(1.008);
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.3);
        border-color: #38bdf8;
    }
    .ticket-card.critical { border-left-color: #f85149; }
    .ticket-card.high     { border-left-color: #e3b341; }
    .ticket-card.medium   { border-left-color: #58a6ff; }
    .ticket-card.low      { border-left-color: #3fb950; }

    /* Clean Pill Badges */
    .badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 6px;
        letter-spacing: 0.2px;
    }
    .badge-critical { background: rgba(248, 81, 73, 0.12); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.2); }
    .badge-high     { background: rgba(227, 179, 65, 0.12); color: #e3b341; border: 1px solid rgba(227, 179, 65, 0.2); }
    .badge-medium   { background: rgba(88, 166, 255, 0.12); color: #58a6ff; border: 1px solid rgba(88, 166, 255, 0.2); }
    .badge-low      { background: rgba(63, 185, 80, 0.12); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.2); }
    .badge-type     { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }

    /* Custom Banners */
    .warning-box {
        background: #1e1b12;
        border: 1px solid #e3b341;
        border-radius: 8px;
        padding: 14px 18px;
        color: #e3b341;
        margin: 12px 0;
    }
    .success-box {
        background: #0f1c15;
        border: 1px solid #3fb950;
        border-radius: 8px;
        padding: 14px 18px;
        color: #3fb950;
        margin: 12px 0;
    }
    .info-box {
        background: #0c192c;
        border: 1px solid #58a6ff;
        border-radius: 8px;
        padding: 14px 18px;
        color: #58a6ff;
        margin: 12px 0;
    }

    /* Input & Text Fields Formats */
    div[data-testid="stTextArea"] textarea {
        background: #0f141c !important;
        border: 1px solid #222f44 !important;
        color: #f1f5f9 !important;
        font-family: 'Space Mono', monospace;
        font-size: 0.88rem;
        border-radius: 8px !important;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.3);
    }
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #58a6ff !important;
    }
    
    /* Premium Action Button */
    .stButton > button {
        background: linear-gradient(135deg, #2ea043 0%, #238636 100%) !important;
        color: white !important;
        border: 1px solid #3fb950 !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        padding: 10px 24px !important;
        box-shadow: 0 4px 12px rgba(35, 134, 54, 0.25) !important;
        transition: all 0.2s ease;
    }
    .stButton > button:hover { 
        background: linear-gradient(135deg, #34b24b 0%, #2ea043 100%) !important;
        transform: translateY(-1px);
        box-shadow: 0 6px 16px rgba(35, 134, 54, 0.4) !important;
    }
    
    /* Clean Sidebar Customization */
    section[data-testid="stSidebar"] {
        background-color: #0c1017 !important;
        border-right: 1px solid #212e46 !important;
    }
    
    /* Details Summary Tweaks */
    details summary {
        color: #38bdf8;
        font-weight: 500;
        outline: none;
        margin-top: 4px;
    }
    details[open] summary {
        color: #7dd3fc;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# UI Elements & Component Helpers
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
                <strong style="font-size:1.05rem;color:#f1f5f9;font-weight:600;">{ticket.title}</strong>
                <span style="font-size:0.75rem;color:#64748b;font-family:monospace;background:#0f141c;padding:2px 6px;border-radius:4px;">{ticket.ticket_id}</span>
            </div>
            <div style="margin:8px 0;">
                {priority_badge(ticket.priority.value)}
                <span class="badge badge-type">{ticket.ticket_type.value}</span>
                <span class="badge badge-type">👤 {ticket.assigned_to}</span>
                {"<span class='badge badge-type'>📅 " + ticket.due_date + "</span>" if ticket.due_date else ""}
            </div>
            <p style="color:#94a3b8;font-size:0.88rem;margin:8px 0;line-height:1.5;">{ticket.description}</p>
            <details>
                <summary style="cursor:pointer;font-size:0.82rem;">Acceptance Criteria</summary>
                <ul style="margin:8px 0;padding-left:18px;color:#cbd5e1;font-size:0.85rem;line-height:1.4;">{ac_html}</ul>
            </details>
            {f'<div style="margin-top:8px;">{tags_html}</div>' if tags_html else ""}
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;">
                <em style="font-size:0.8rem;color:#64748b;font-style:italic;">💬 "{ticket.source_quote}"</em>
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

    # 2. LLM extraction
    try:
        analysis: MeetingAnalysis = extract_tickets_from_transcript(
            transcript, custom_instructions or None
        )
    except (ValueError, RuntimeError, EnvironmentError) as e:
        return {"status": "llm_error", "message": str(e)}

    # 3. Async Sheets write
    sheets_result = await write_analysis_to_sheets(spreadsheet_id, analysis, tx_hash)

    # 4. Register in dedup store
    register_submission(tx_hash, analysis.meeting_title, len(analysis.tickets))

    return {
        "status":        "success",
        "analysis":      analysis,
        "sheets_result": sheets_result,
        "tx_hash":       tx_hash,
    }


# ─────────────────────────────────────────────
# Sidebar Context Management
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
# Workspace Tabs Definition
# ─────────────────────────────────────────────

tab_submit, tab_board, tab_history = st.tabs(
    ["📝 Submit Transcript", "📋 Ticket Board", "🕐 History"]
)


# ══════════════════════════════════════════════
# TAB 1 — Submit Transcript Area
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

    # Execution Action Block
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

            # Persistent cache storage for layout retention
            st.session_state["active_run_outcome"] = result
            if result["status"] == "success":
                st.session_state["last_analysis"] = result["analysis"]
                # Flush chat history arrays on fresh generation loads
                if "chat_messages" in st.session_state:
                    del st.session_state["chat_messages"]

    # Persistent Dashboard Rendering Layer (Maintains views during Chatbot reruns)
    if "active_run_outcome" in st.session_state:
        result = st.session_state["active_run_outcome"]

        if result["status"] == "duplicate":
            st.markdown(
                f'<div class="warning-box">⚠️ {result["message"]}</div>',
                unsafe_allow_html=True,
            )

        elif result["status"] == "llm_error":
            st.error(f"❌ AI extraction failed: {result['message']}")
            st.info("Check your GEMINI_API_KEY and try again.")

        elif result["status"] == "success":
            analysis: MeetingAnalysis = st.session_state["last_analysis"]
            sheets_res = result["sheets_result"]

            # Connection outcome notice banners
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

            # Dashboard analytics card layout blocks
            st.markdown(f"### 📋 {analysis.meeting_title}")
            c1, c2, c3, c4 = st.columns(4)
            for col, val, label in [
                (c1, len(analysis.tickets),                    "Tickets Created"),
                (c2, len(analysis.participants),               "Participants"),
                (c3, len(analysis.action_items_without_owner), "Unowned Actions"),
                (c4, len(analysis.risks_flagged),              "Risks Flagged"),
            ]:
                col.markdown(
                    f'<div class="metric-card"><h3>{val}</h3><p>{label}</p></div>',
                    unsafe_allow_html=True,
                )

            st.write("")
            st.markdown(f"> {analysis.summary}")

            # Risk parameters expansion frames
            if analysis.risks_flagged:
                with st.expander("⚠️ Risks & Blockers Flagged"):
                    for r in analysis.risks_flagged:
                        st.markdown(f"- {r}")

            # Action assignment coverage tracking frames
            if analysis.action_items_without_owner:
                with st.expander("🔍 Actions Without Owner (Not ticketed)"):
                    for a in analysis.action_items_without_owner:
                        st.markdown(f"- {a}")

            # Extracted visual card structures
            st.markdown(f"### 🎫 Generated Tickets ({len(analysis.tickets)})")
            for ticket in analysis.tickets:
                render_ticket_card(ticket)

            # JSON Data backup element
            st.divider()
            st.download_button(
                "⬇️ Download as JSON",
                data=analysis.model_dump_json(indent=2),
                file_name=f"tickets_{result['tx_hash'][:8]}.json",
                mime="application/json",
            )


# ─────────────────────────────────────────────
# TAB 2 — Ticket Board
# ─────────────────────────────────────────────

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
            by_owner: dict = {}
            for t in filtered:
                by_owner.setdefault(t.assigned_to, []).append(t)

            for owner, tickets in by_owner.items():
                st.markdown(f"#### 👤 {owner} ({len(tickets)} ticket{'s' if len(tickets)>1 else ''})")
                for ticket in tickets:
                    render_ticket_card(ticket)
                st.divider()


# ─────────────────────────────────────────────
# TAB 3 — Submission History Archive
# ─────────────────────────────────────────────

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


# ══════════════════════════════════════════════
# 💬 GLOBAL COMPONENT — Interactive Contextual Chatbot (Toto)
# ══════════════════════════════════════════════

# Placed at the absolute root level (no tab nesting block) to render flawlessly!
st.write("---")
st.markdown("### 🤖 Chat with Toto")
st.caption("Ask Toto about total weights, allocations, bugs, or general Agile strategy in Hinglish or English!")

# Grab active analysis payload safely if it exists in system state memory
analysis_context = st.session_state.get("last_analysis", None)

# Seed adaptive initial conversation responses depending on data states
if "chat_messages" not in st.session_state:
    if analysis_context is None:
        st.session_state.chat_messages = [
            {
                "role": "assistant", 
                "content": "Oi! **Toto** here. 🐕 Sab kuch ready hai! Abhi tak koi meeting transcript load nahi hui hai. Aap upar box me transcript paste karke **Generate Tickets** dabayein, tab tak aap mujhse koi bhi general Agile ya project management ka sawaal pooch sakte hain!"
            }
        ]
    else:
        st.session_state.chat_messages = [
            {
                "role": "assistant", 
                "content": "Oi! **Toto** here. 🐕 Live sprint context loaded successfully! Aap mujhse tickets, bugs counter, deadlines, ya allocations ke baare me kuch bhi pooch sakte hain. Boliyen, kya help chahiye?"
            }
        ]

# Render persistent conversation layout streams
for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# Monitor interactive prompt text inputs dynamically
if chat_prompt := st.chat_input("Ask Toto..."):
    with st.chat_message("user"):
        st.write(chat_prompt)
    st.session_state.chat_messages.append({"role": "user", "content": chat_prompt})

    # Process query strings passing the active context state safely (could be None)
    with st.chat_message("assistant"):
        with st.spinner("Toto is analyzing..."):
            answer = answer_ticket_query(analysis_context, chat_prompt)
            st.write(answer)
    st.session_state.chat_messages.append({"role": "assistant", "content": answer})

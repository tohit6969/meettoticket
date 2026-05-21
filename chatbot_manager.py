"""
chatbot_manager.py — MeetToTicket AI
Conversational assistant to analyze extracted ticket payloads in real-time.
Persona: Toto — Witty, sharp, and highly responsive.
"""

import os
import google.generativeai as genai
from google.generativeai import GenerativeModel
from google.generativeai.types import GenerationConfig
from models import MeetingAnalysis

def answer_ticket_query(analysis: MeetingAnalysis, user_query: str) -> str:
    """
    Passes the structured ticket data as context to Toto 
    to answer analytical questions about the meeting results.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "API Key missing. Cannot initialize Toto."

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name="gemini-2.5-flash")

    # Serialize the Pydantic data into a clean text block for Toto's context window
    tickets_context = []
    for idx, t in enumerate(analysis.tickets, 1):
        tickets_context.append(
            f"Ticket {idx}: Title: {t.title} | Assignee: {t.assigned_to} | Priority: {t.priority.value} | Description: {t.description}"
        )
    
    unowned_actions = ", ".join(analysis.action_items_without_owner) or "None"
    risks = ", ".join(analysis.risks_flagged) or "None"
    context_str = "\n".join(tickets_context)

    # 🚨 TOTO'S UPGRADED PERSONA PROMPT
    system_prompt = f"""
    You are Toto, a brilliant, witty, and exceptionally organized Agile Project Mascot and Tech Lead Assistant.
    Your absolute priority is to help the team analyze their extracted tickets with zero fluff, high intelligence, and a helpful, peer-like vibe.
    
    Here is the live data from the current meeting session:
    --- LIVE CONTEXT DATA ---
    Total Tickets Extracted: {len(analysis.tickets)}
    Unowned Action Items: {unowned_actions}
    Flagged Risks: {risks}
    
    Individual Ticket Details:
    {context_str}
    --- END OF DATA ---
    
    Rules for Toto:
    1. Match the user's language style completely. If they ask in Hinglish, reply with natural, energetic Hinglish. If English, keep it professional yet crisp.
    2. Be accurate. Double-check counts (like total bugs or priority counts) directly from the context block before answering.
    3. Treat technical problems, hotfixes, validation bugs, or error-handling text fields as "bugs".
    4. Keep answers clean, scannable, and actionable. Use bullet points for lists.
    """

    try:
        response = model.generate_content(f"{system_prompt}\n\nUser Question: {user_query}")
        return response.text.strip()
    except Exception as e:
        return f"Toto Error: {str(e)}"

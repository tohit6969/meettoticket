"""
chatbot_manager.py — MeetToTicket AI
Conversational assistant to analyze extracted ticket payloads in real-time.
Supports English, Hindi, and Hinglish analytics queries.
"""

import os
import google.generativeai as genai
from google.generativeai import GenerativeModel
from google.generativeai.types import GenerationConfig
from models import MeetingAnalysis

def answer_ticket_query(analysis: MeetingAnalysis, user_query: str) -> str:
    """
    Passes the structured ticket data as context to Gemini 
    to answer analytical questions about the meeting results.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "API Key missing. Cannot initialize chatbot."

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name="gemini-2.5-flash")

    # Serialize the Pydantic data into a clean text block for the LLM context
    tickets_context = []
    for idx, t in enumerate(analysis.tickets, 1):
        tickets_context.append(
            f"Ticket {idx}: Title: {t.title} | Assignee: {t.assignee} | Priority: {t.priority} | Description: {t.description}"
        )
    
    unowned_actions = ", ".join(analysis.action_items_without_owner) or "None"
    risks = ", ".join(analysis.risks_flagged) or "None"
    
    context_str = "\n".join(tickets_context)

    system_prompt = f"""
    You are the MeetToTicket AI Analytics Assistant. 
    Your job is to answer questions about the extracted tickets from the current meeting session.
    
    Here is the exact data from the current meeting session:
    --- DATA START ---
    Total Tickets Extracted: {len(analysis.tickets)}
    Unowned Action Items: {unowned_actions}
    Flagged Risks: {risks}
    
    Individual Tickets Details:
    {context_str}
    --- DATA END ---
    
    Rules:
    1. Respond naturally in the language the user asked (English, Hindi, or Hinglish).
    2. Be precise. If they ask for totals, count them carefully from the data provided.
    3. If they ask about "bugs", look for technical problems, hotfixes, or mentions of errors in the ticket titles/descriptions.
    4. Keep answers short, punchy, and developer-friendly.
    """

    try:
        response = model.generate_content(f"{system_prompt}\n\nUser Question: {user_query}")
        return response.text.strip()
    except Exception as e:
        return f"Chatbot error: {str(e)}"
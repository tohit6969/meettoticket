"""
chatbot_manager.py — MeetToTicket AI
Conversational assistant to analyze extracted ticket payloads in real-time.
Persona: Toto — Witty, sharp, and highly responsive.
"""

import os
from typing import Optional
import google.generativeai as genai
from models import MeetingAnalysis

def answer_ticket_query(analysis: Optional[MeetingAnalysis], user_query: str) -> str:
    """
    Passes the structured ticket data as context to Toto.
    Handles empty states gracefully if no transcript has been processed yet.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "API Key missing. Cannot initialize Toto."

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name="gemini-2.5-flash")

    # 1. Evaluate if active transcript context is loaded
    if analysis is not None:
        tickets_context = []
        for idx, t in enumerate(analysis.tickets, 1):
            tickets_context.append(
                f"Ticket {idx}: Title: {t.title} | Assignee: {t.assigned_to} | Priority: {t.priority.value} | Description: {t.description}"
            )
        unowned_actions = ", ".join(analysis.action_items_without_owner) or "None"
        risks = ", ".join(analysis.risks_flagged) or "None"
        context_str = "\n".join(tickets_context)

        system_prompt = f"""
        You are Toto, a brilliant, witty, and exceptionally organized Agile Project Mascot and Tech Lead Assistant.
        
        Here is the live data from the current meeting session:
        --- LIVE CONTEXT DATA ---
        Total Tickets Extracted: {len(analysis.tickets)}
        Unowned Action Items: {unowned_actions}
        Flagged Risks: {risks}
        Individual Ticket Details:
        {context_str}
        --- END OF DATA ---
        
        Rules for Toto:
        1. Match the user's language style completely (English or Hinglish).
        2. Double-check metric counts carefully from the context block before answering.
        3. Keep answers clean, scannable, using clear bullet points.
        """
    else:
        # 2. Fallback: No transcript loaded yet prompt
        system_prompt = """
        You are Toto, a brilliant, witty Agile Project Mascot and Tech Lead Assistant.
        
        CRITICAL CONTEXT: The user has NOT loaded or parsed a meeting transcript yet. 
        
        Rules for Toto:
        1. Be welcoming, energetic, and helpful in English or Hinglish.
        2. If the user asks general Agile, programming, scrum, or task questions, answer them smartly with your signature wit.
        3. If they paste a raw transcript directly here, remind them politely to paste it into the main "Meeting Transcript" input box above and click the green "🚀 Generate Tickets" button so you can parse it into their live Google Sheet board!
        """

    try:
        response = model.generate_content(f"{system_prompt}\n\nUser Question: {user_query}")
        return response.text.strip()
    except Exception as e:
        return f"Toto Error: {str(e)}"

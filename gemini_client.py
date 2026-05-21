"""
gemini_client.py — MeetToTicket AI
Structured output extraction from meeting transcripts via Gemini.

Key pattern: response_mime_type="application/json" + response_schema
forces the LLM to comply with the Pydantic schema at the API layer —
no fragile regex or JSON-parsing hacks needed.
"""

import json
import logging
import os
from typing import Optional

import google.generativeai as genai
from google.generativeai import GenerativeModel
from google.generativeai.types import GenerationConfig
from pydantic import ValidationError

from models import MeetingAnalysis

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────

def _init_client() -> GenerativeModel:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY not set. Add it to your secrets."
        )
    genai.configure(api_key=api_key)
    
    # Get the raw schema dictionary from Pydantic
    base_schema = MeetingAnalysis.model_json_schema()
    
    # Extract the definitions mapping out of the schema if it exists
    definitions = base_schema.pop("$defs", {})
    
    def inline_and_sanitize(d):
        """
        Recursively steps through the dictionary to replace Pydantic's 
        '$ref' pointers with their literal definitions, while removing 
        any unsupported OpenAPI properties.
        """
        if isinstance(d, dict):
            # 1. Resolve nested schema references instantly
            if "$ref" in d:
                ref_path = d.pop("$ref")
                ref_name = ref_path.split("/")[-1]
                if ref_name in definitions:
                    # Merge the internal definition straight into the current block
                    d.update(definitions[ref_name])
            
            # 2. Drop any unsupported keyword arguments
            d.pop("default", None)
            d.pop("title", None)
            
            # 3. Recursively parse child parameters
            for k, v in list(d.items()):
                inline_and_sanitize(v)
                
        elif isinstance(d, list):
            for item in d:
                inline_and_sanitize(item)

    # Transform the schema dictionary completely inline
    inline_and_sanitize(base_schema)
    
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=GenerationConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=base_schema,
        ),
    )

# ─────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """
You are MeetToTicket AI, an expert Agile project manager and business analyst.

Your ONLY job is to read meeting transcripts and extract actionable work items
as structured ticket data. You must be precise, concise, and developer-friendly.

Rules:
1. ONLY create tickets for concrete, actionable work items with a clear owner.
2. Do NOT create tickets for vague statements like "we should improve performance"
   unless a specific person committed to a specific task.
3. Each acceptance criterion must be a concrete, testable statement.
4. Priority must be inferred from urgency language:
   - "blocker", "ASAP", "by EOD" → Critical or High
   - "this week", "next sprint" → Medium
   - "when we get to it", "nice to have" → Low
5. source_quote must be a near-verbatim short excerpt from the transcript.
6. If a task lacks a clear owner, add it to action_items_without_owner instead.
7. The entire response MUST be valid JSON matching the MeetingAnalysis schema.
   No preamble, no markdown fences, just raw JSON.
"""


# ─────────────────────────────────────────────
# Main extraction function
# ─────────────────────────────────────────────

def extract_tickets_from_transcript(
    transcript: str,
    custom_instructions: Optional[str] = None,
) -> MeetingAnalysis:
    """
    Send a transcript to Gemini and return a validated MeetingAnalysis object.

    Raises:
        EnvironmentError: If API key is missing.
        ValueError: If the LLM returns malformed JSON or fails Pydantic validation.
        RuntimeError: If the Gemini API call itself fails.
    """
    model = _init_client()

    user_instructions = (
        f"\n\nAdditional instructions from the user:\n{custom_instructions}"
        if custom_instructions
        else ""
    )

    prompt = f"""{SYSTEM_PROMPT}{user_instructions}

---TRANSCRIPT START---
{transcript.strip()}
---TRANSCRIPT END---

Extract all tickets and return the MeetingAnalysis JSON now.
"""

    logger.info("Sending transcript to Gemini (%d chars)...", len(transcript))

    try:
        response = model.generate_content(prompt)
        raw_json = response.text.strip()
    except Exception as api_err:
        logger.error("Gemini API call failed: %s", api_err)
        raise RuntimeError(f"Gemini API error: {api_err}") from api_err

    # Strip accidental markdown fences if the model adds them
    if raw_json.startswith("```"):
        raw_json = raw_json.split("```")[1]
        if raw_json.startswith("json"):
            raw_json = raw_json[4:]
        raw_json = raw_json.strip()

    try:
        parsed_dict = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s\nRaw: %s", e, raw_json[:500])
        raise ValueError(f"LLM returned malformed JSON: {e}") from e

    try:
        analysis = MeetingAnalysis.model_validate(parsed_dict)
    except ValidationError as e:
        logger.error("Schema validation failed:\n%s", e)
        raise ValueError(f"LLM output failed schema validation: {e}") from e

    logger.info(
        "Extracted %d tickets from '%s'",
        len(analysis.tickets),
        analysis.meeting_title,
    )
    return analysis

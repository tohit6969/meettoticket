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
    
    # 1. Grab raw schema dictionary out of your Pydantic blueprint
    base_schema = MeetingAnalysis.model_json_schema()
    
    # 2. Isolate internal sub-model maps if they exist
    definitions = base_schema.pop("$defs", {})
    
    def clean_schema(d):
        if not isinstance(d, dict):
            return

        # Handle nested Pydantic references ($ref) instantly
        if "$ref" in d:
            ref_path = d.pop("$ref")
            ref_name = ref_path.split("/")[-1]
            if ref_name in definitions:
                d.update(definitions[ref_name])
        
        # Handle complex 'anyOf' arrays to primitive nullable types
        if "anyOf" in d:
            any_of_list = d.pop("anyOf")
            non_null = [t for t in any_of_list if isinstance(t, dict) and t.get("type") != "null"]
            if non_null:
                d.update(non_null[0])
                d["nullable"] = True

        # Whitelist layer for schema validation attributes
        allowed_keys = {
            "type", "properties", "required", "items", 
            "enum", "description", "nullable"
        }
        
        for forbidden_key in list(d.keys()):
            if forbidden_key not in allowed_keys:
                d.pop(forbidden_key)
        
        
        if "properties" in d and isinstance(d["properties"], dict):
            for field_schema in d["properties"].values():
                clean_schema(field_schema)
                
        if "items" in d:
            if isinstance(d["items"], dict):
                clean_schema(d["items"])
            elif isinstance(d["items"], list):
                for item in d["items"]:
                    clean_schema(item)

    
    clean_schema(base_schema)
    
    return genai.GenerativeModel(
        model_name="gemini-3.5-flash",
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

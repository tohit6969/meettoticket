"""
models.py — MeetToTicket AI
Pydantic schemas for strict structured outputs from Gemini.
The LLM is forced to comply with these shapes at the API layer.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
import uuid


# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────

class Priority(str, Enum):
    CRITICAL = "Critical"
    HIGH     = "High"
    MEDIUM   = "Medium"
    LOW      = "Low"


class TicketStatus(str, Enum):
    TODO        = "To Do"
    IN_PROGRESS = "In Progress"
    DONE        = "Done"
    BLOCKED     = "Blocked"


class TicketType(str, Enum):
    TASK    = "Task"
    BUG     = "Bug"
    STORY   = "Story"
    SPIKE   = "Spike"          # Research / investigation


# ─────────────────────────────────────────────
# Core Ticket Model
# ─────────────────────────────────────────────

class Ticket(BaseModel):
    """
    A single work item extracted from a meeting transcript.
    Maps 1-to-1 to a row in the Google Sheets board.
    """

    ticket_id: str = Field(
        default_factory=lambda: f"MTT-{str(uuid.uuid4())[:8].upper()}",
        description="Unique ticket identifier, auto-generated.",
    )
    title: str = Field(
        ...,
        min_length=5,
        max_length=120,
        description="Short, action-oriented title. E.g. 'Fix login redirect bug'.",
    )
    description: str = Field(
        ...,
        min_length=10,
        description=(
            "Detailed description of the work needed. "
            "Must include WHAT and WHY, derived from meeting context."
        ),
    )
    acceptance_criteria: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Concrete, testable criteria (Given / When / Then style preferred). "
            "Minimum 1 item required."
        ),
    )
    assigned_to: str = Field(
        ...,
        description="Full name of the person responsible, exactly as mentioned in the meeting.",
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Ticket priority inferred from urgency language in the meeting.",
    )
    ticket_type: TicketType = Field(
        default=TicketType.TASK,
        description="Nature of the work item.",
    )
    status: TicketStatus = Field(
        default=TicketStatus.TODO,
        description="Always 'To Do' for freshly created tickets.",
    )
    due_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 date string (YYYY-MM-DD) if a deadline was explicitly mentioned, "
            "else null."
        ),
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Optional labels like 'backend', 'frontend', 'infra', 'design'.",
    )
    source_quote: str = Field(
        ...,
        description=(
            "Verbatim short quote (≤ 30 words) from the transcript that triggered "
            "this ticket. Used as audit evidence."
        ),
    )

    @field_validator("acceptance_criteria")
    @classmethod
    def criteria_must_be_actionable(cls, v: List[str]) -> List[str]:
        if any(len(c.strip()) < 10 for c in v):
            raise ValueError("Each acceptance criterion must be at least 10 characters.")
        return v

    def to_sheet_row(self) -> List[str]:
        """Serialize to a flat list for Google Sheets row insertion."""
        return [
            self.ticket_id,
            self.title,
            self.description,
            " | ".join(self.acceptance_criteria),
            self.assigned_to,
            self.priority.value,
            self.ticket_type.value,
            self.status.value,
            self.due_date or "—",
            ", ".join(self.tags) if self.tags else "—",
            self.source_quote,
        ]

    @classmethod
    def sheet_headers(cls) -> List[str]:
        return [
            "Ticket ID", "Title", "Description",
            "Acceptance Criteria", "Assigned To",
            "Priority", "Type", "Status",
            "Due Date", "Tags", "Source Quote",
        ]


# ─────────────────────────────────────────────
# Top-level LLM Response Schema
# ─────────────────────────────────────────────

class MeetingAnalysis(BaseModel):
    """
    The complete structured output from a single transcript analysis.
    Gemini must return exactly this shape — no free text allowed.
    """

    meeting_title: str = Field(
        ...,
        description="Inferred title of the meeting (e.g. 'Sprint 14 Planning — May 2026').",
    )
    meeting_date: Optional[str] = Field(
        default=None,
        description="ISO-8601 date if mentioned, else null.",
    )
    participants: List[str] = Field(
        ...,
        description="All unique names mentioned in the transcript.",
    )
    summary: str = Field(
        ...,
        min_length=30,
        max_length=500,
        description="2–4 sentence executive summary of the meeting.",
    )
    tickets: List[Ticket] = Field(
        ...,
        min_length=1,
        description="All actionable work items found in the transcript.",
    )
    action_items_without_owner: List[str] = Field(
        default_factory=list,
        description=(
            "Tasks mentioned but without a clear owner. "
            "These cannot become tickets but are surfaced for awareness."
        ),
    )
    risks_flagged: List[str] = Field(
        default_factory=list,
        description="Any blockers, dependencies, or risks the team discussed.",
    )

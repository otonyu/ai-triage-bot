"""
Output schemas for the triage system.

Three output shapes:
- ActionableTicket: enough signal to file work
- NeedsClarification: too vague; produce questions for the reporter
- NotABug: feature request, user error, question, spam, etc.

Plus an internal ExtractionResult that the LLM populates and the
routing code consumes to decide which output shape to emit.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------- Enums ----------


class Platform(str, Enum):
    web = "web"
    ios = "ios"
    android = "android"
    desktop = "desktop"
    api = "api"
    unknown = "unknown"


class Scope(str, Enum):
    one = "one"
    some = "some"
    many = "many"
    unknown = "unknown"


class TriBool(str, Enum):
    """True / False / Unknown. We use a string enum rather than
    Optional[bool] because 'unknown' carries different meaning from
    'not yet set' and the LLM handles strings more reliably."""

    yes = "yes"
    no = "no"
    unknown = "unknown"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class NotABugClassification(str, Enum):
    feature_request = "feature_request"
    user_error = "user_error"
    question = "question"
    duplicate_shaped = "duplicate_shaped"
    spam = "spam"
    unclear_intent = "unclear_intent"


# ---------- Shared sub-schemas ----------


class Environment(BaseModel):
    platform: Platform = Platform.unknown
    browser: Optional[str] = None
    version: Optional[str] = None
    other: Optional[str] = None


class ScopeAssessment(BaseModel):
    users_affected: Scope = Scope.unknown
    evidence: Optional[str] = Field(
        default=None,
        description="Direct quote or paraphrase from the report supporting this scope.",
    )


class Signals(BaseModel):
    """Extracted facts that feed prioritization. Each is yes/no/unknown.

    These are deliberately separated from `priority` so that an engineer
    can disagree with the priority while still trusting the signals,
    and so the prioritization step is deterministic and testable.
    """

    user_blocked: TriBool = TriBool.unknown
    data_loss_risk: TriBool = TriBool.unknown
    security_implication: TriBool = TriBool.unknown
    regression_suspected: TriBool = TriBool.unknown
    workaround_exists: TriBool = TriBool.unknown


# ---------- LLM extraction result (internal) ----------


class ExtractionResult(BaseModel):
    """What the LLM returns from the first call. This is the raw
    structured extraction; routing logic decides what shape to emit
    based on these fields."""

    # Classification — is this even a bug?
    is_bug: TriBool = TriBool.unknown
    not_a_bug_classification: Optional[NotABugClassification] = None
    not_a_bug_reasoning: Optional[str] = None

    # Core ticket fields (may all be None for vague reports)
    title: Optional[str] = None
    summary: Optional[str] = None
    reproduction_steps: Optional[List[str]] = Field(
        default=None,
        description=(
            "ONLY populate if the report contains an actual sequence of actions. "
            "Do not synthesize plausible steps from a symptom description."
        ),
    )
    expected_behavior: Optional[str] = None
    actual_behavior: Optional[str] = None

    environment: Environment = Field(default_factory=Environment)
    scope: ScopeAssessment = Field(default_factory=ScopeAssessment)
    signals: Signals = Field(default_factory=Signals)

    suggested_component: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # Multi-issue flag — single report describing multiple distinct bugs
    multiple_issues_detected: bool = False
    multiple_issues_note: Optional[str] = None

    # Self-assessment
    extraction_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "1.0 = nearly everything was explicit in the report. "
            "0.3 = had to infer most fields. <0.5 generally implies "
            "the report is too vague to act on."
        ),
    )
    missing_critical_fields: List[str] = Field(
        default_factory=list,
        description=(
            "Fields that are null/unknown AND would block an engineer from acting. "
            "At minimum, an actionable ticket needs reproduction_steps OR "
            "(expected_behavior AND actual_behavior)."
        ),
    )


# ---------- Output shapes ----------


class ActionableTicket(BaseModel):
    status: Literal["actionable"] = "actionable"
    original_report: str

    title: str
    summary: str
    reproduction_steps: Optional[List[str]] = None
    expected_behavior: Optional[str] = None
    actual_behavior: Optional[str] = None

    environment: Environment
    scope: ScopeAssessment
    signals: Signals

    priority: Priority
    priority_rationale: str = Field(
        description="Cites which specific signals drove the priority assignment."
    )

    suggested_component: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    multiple_issues_detected: bool = False
    multiple_issues_note: Optional[str] = None

    extraction_confidence: float


class ClarificationQuestion(BaseModel):
    question: str
    why_it_matters: str


class NeedsClarification(BaseModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    original_report: str

    what_we_understood: str = Field(
        description="Best-effort one-sentence interpretation of what the reporter meant."
    )
    blocking_questions: List[ClarificationQuestion] = Field(
        description="The minimum set of questions needed to move forward."
    )
    tentative_signals: Signals = Field(
        description="Best-effort signal extraction. Most fields may be 'unknown'."
    )
    suggested_reply_to_reporter: str = Field(
        description=(
            "Draft message support can send back to the reporter to elicit "
            "the missing information."
        )
    )

    extraction_confidence: float


class NotABug(BaseModel):
    status: Literal["not_a_bug"] = "not_a_bug"
    original_report: str

    classification: NotABugClassification
    reasoning: str
    suggested_action: str = Field(
        description="What support should do with this (route to product, reply with docs, ignore, etc.)"
    )


TriageOutput = Union[ActionableTicket, NeedsClarification, NotABug]

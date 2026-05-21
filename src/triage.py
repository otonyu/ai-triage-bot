"""
Triage orchestration.

For each raw report:

  1. Call the LLM to extract structured fields (ExtractionResult).
  2. Decide which output shape applies based on extraction:
       - not_a_bug if classified as such
       - needs_clarification if too vague (see _is_too_vague)
       - actionable otherwise
  3. For actionable, compute priority deterministically from signals.
  4. For needs_clarification, make a second LLM call to draft questions
     and a reply to the reporter.

The vagueness threshold is a product decision. We require at least one
of: reproduction steps, OR (expected_behavior + actual_behavior). Without
one of those, an engineer cannot meaningfully act, regardless of how many
other fields are populated.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from anthropic import Anthropic
from pydantic import ValidationError

from .prioritize import assign_priority
from .prompts import (
    CLARIFICATION_SYSTEM,
    CLARIFICATION_USER_TEMPLATE,
    EXTRACTION_SYSTEM,
    EXTRACTION_USER_TEMPLATE,
)
from .schemas import (
    ActionableTicket,
    ExtractionResult,
    NeedsClarification,
    NotABug,
    TriageOutput,
    TriBool,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"  # cheap-enough, capable-enough for extraction
MAX_TOKENS = 2000

# Routing thresholds — see write-up. These are intentionally explicit so
# they can be tuned against real labeled data later.
MIN_CONFIDENCE_FOR_ACTIONABLE = 0.5


def triage_report(raw_report: str, client: Optional[Anthropic] = None) -> TriageOutput:
    """Triage a single raw bug report into one of the three output shapes."""
    if client is None:
        client = Anthropic()

    # Edge case: empty or near-empty input. No point calling the LLM.
    if not raw_report or not raw_report.strip() or len(raw_report.strip()) < 3:
        return NotABug(
            original_report=raw_report,
            classification="spam",  # type: ignore[arg-type]
            reasoning="Empty or near-empty report; nothing to triage.",
            suggested_action="Discard; reply to reporter only if they re-engage with detail.",
        )

    extraction = _extract(raw_report, client)

    # Route 1: Not a bug
    if extraction.is_bug == TriBool.no and extraction.not_a_bug_classification:
        return NotABug(
            original_report=raw_report,
            classification=extraction.not_a_bug_classification,
            reasoning=extraction.not_a_bug_reasoning or "Classified as non-bug by extraction.",
            suggested_action=_suggest_not_a_bug_action(extraction),
        )

    # Route 2: Needs clarification
    if _is_too_vague(extraction):
        return _build_clarification(raw_report, extraction, client)

    # Route 3: Actionable ticket
    return _build_actionable(raw_report, extraction)


# ---------- Extraction ----------


def _extract(raw_report: str, client: Anthropic) -> ExtractionResult:
    schema_json = json.dumps(ExtractionResult.model_json_schema(), indent=2)
    user = EXTRACTION_USER_TEMPLATE.format(report=raw_report, schema=schema_json)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = _first_text(response)
    data = _parse_json(text)

    try:
        return ExtractionResult.model_validate(data)
    except ValidationError as e:
        logger.warning("Extraction validation failed; falling back to vague-extraction. %s", e)
        # Fallback: return a minimally-valid extraction that will route to
        # clarification. Never crash on a single bad report.
        return ExtractionResult(
            extraction_confidence=0.0,
            missing_critical_fields=["reproduction_steps", "expected_behavior", "actual_behavior"],
        )


# ---------- Routing helpers ----------


def _is_too_vague(extraction: ExtractionResult) -> bool:
    """An actionable ticket needs at least one of:
       - reproduction_steps (non-empty)
       - both expected_behavior AND actual_behavior
    And reasonable extraction confidence.
    """
    has_repro = bool(extraction.reproduction_steps)
    has_expected_actual = bool(extraction.expected_behavior) and bool(extraction.actual_behavior)
    if not (has_repro or has_expected_actual):
        return True
    if extraction.extraction_confidence < MIN_CONFIDENCE_FOR_ACTIONABLE:
        return True
    return False


def _suggest_not_a_bug_action(extraction: ExtractionResult) -> str:
    classification = extraction.not_a_bug_classification
    if classification is None:
        return "Review and route appropriately."
    mapping = {
        "feature_request": "Route to product management; thank the reporter and let them know it's been logged as feedback.",
        "user_error": "Reply to reporter with documentation or guidance. If many users hit this, consider a UX fix.",
        "question": "Reply with answer or documentation link; consider adding to FAQ if it recurs.",
        "duplicate_shaped": "Search existing tickets; link to the original if found, otherwise treat as a fresh report.",
        "spam": "Discard.",
        "unclear_intent": "Reply asking the reporter to clarify what they're trying to communicate.",
    }
    return mapping.get(classification.value, "Review and route appropriately.")


# ---------- Builders ----------


def _build_actionable(raw_report: str, extraction: ExtractionResult) -> ActionableTicket:
    priority, rationale = assign_priority(extraction.signals, extraction.scope.users_affected)

    # Title and summary should exist if we got here; if not, synthesize
    # minimal versions rather than failing.
    title = extraction.title or "Untitled bug report"
    summary = extraction.summary or raw_report[:200]

    return ActionableTicket(
        original_report=raw_report,
        title=title,
        summary=summary,
        reproduction_steps=extraction.reproduction_steps,
        expected_behavior=extraction.expected_behavior,
        actual_behavior=extraction.actual_behavior,
        environment=extraction.environment,
        scope=extraction.scope,
        signals=extraction.signals,
        priority=priority,
        priority_rationale=rationale,
        suggested_component=extraction.suggested_component,
        tags=extraction.tags,
        multiple_issues_detected=extraction.multiple_issues_detected,
        multiple_issues_note=extraction.multiple_issues_note,
        extraction_confidence=extraction.extraction_confidence,
    )


def _build_clarification(
    raw_report: str, extraction: ExtractionResult, client: Anthropic
) -> NeedsClarification:
    """Second LLM call: generate specific questions and a draft reply."""
    schema_json = json.dumps(NeedsClarification.model_json_schema(), indent=2)
    extraction_json = extraction.model_dump_json(indent=2)
    user = CLARIFICATION_USER_TEMPLATE.format(
        report=raw_report, extraction=extraction_json, schema=schema_json
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=CLARIFICATION_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = _first_text(response)
    data = _parse_json(text)

    # The LLM doesn't see the status/original_report; fill them in.
    data["status"] = "needs_clarification"
    data["original_report"] = raw_report
    data.setdefault("extraction_confidence", extraction.extraction_confidence)
    # Use extracted signals as the tentative ones.
    data.setdefault("tentative_signals", extraction.signals.model_dump())

    try:
        return NeedsClarification.model_validate(data)
    except ValidationError as e:
        logger.warning("Clarification validation failed; using minimal fallback. %s", e)
        return NeedsClarification(
            original_report=raw_report,
            what_we_understood="The report does not contain enough detail to interpret with confidence.",
            blocking_questions=[],
            tentative_signals=extraction.signals,
            suggested_reply_to_reporter=(
                "Thanks for the report. Could you share a bit more detail about what you "
                "were trying to do, what you expected to happen, and what actually happened? "
                "If you can include the page or screen you were on, that would help us track it down."
            ),
            extraction_confidence=extraction.extraction_confidence,
        )


# ---------- LLM response helpers ----------


def _first_text(response) -> str:
    """Pull the first text block from a Messages API response."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("No text block in LLM response.")


def _parse_json(text: str) -> dict:
    """Strip optional markdown fences and parse JSON."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove ```json or ``` fence and trailing ```
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
        cleaned = cleaned.strip()
    return json.loads(cleaned)

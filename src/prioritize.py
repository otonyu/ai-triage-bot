"""
Deterministic priority assignment based on extracted signals.

This is deliberately a pure function with no LLM involvement. The LLM's
job is to extract signals from messy prose; the priority is then a
predictable function of those signals. This makes priority decisions:

  - explainable (we can cite which signals fired)
  - consistent (same signals -> same priority, always)
  - testable (no model-call needed in unit tests)
  - debatable (an engineer can disagree with a rule, not a vibe)

The exact rules below are a starting point and would be calibrated
with the eng team. They are intentionally
conservative: when in doubt, lean lower-priority and let the engineer
escalate.
"""

from __future__ import annotations

from typing import Tuple

from .schemas import Priority, Scope, Signals, TriBool


def _yes(v: TriBool) -> bool:
    return v == TriBool.yes


def _unknown(v: TriBool) -> bool:
    return v == TriBool.unknown


def assign_priority(signals: Signals, scope: Scope) -> Tuple[Priority, str]:
    """Return (priority, rationale). Rationale cites which signals fired."""
    fired = []

    # ---- P0: anything that can't wait ----
    if _yes(signals.data_loss_risk):
        fired.append("data_loss_risk=yes")
        return Priority.P0, _rationale("P0", fired, "data loss outranks everything else")

    if _yes(signals.security_implication):
        fired.append("security_implication=yes")
        return Priority.P0, _rationale("P0", fired, "security issue, treat as urgent until disproven")

    if _yes(signals.user_blocked) and scope == Scope.many:
        fired.append("user_blocked=yes")
        fired.append("scope=many")
        return Priority.P0, _rationale("P0", fired, "many users completely blocked")

    # ---- P1: serious but bounded ----
    if _yes(signals.user_blocked) and scope in (Scope.some, Scope.many):
        fired.append("user_blocked=yes")
        fired.append(f"scope={scope.value}")
        return Priority.P1, _rationale("P1", fired, "multiple users blocked but not data-loss/security severity")

    if _yes(signals.regression_suspected) and scope != Scope.one:
        fired.append("regression_suspected=yes")
        fired.append(f"scope={scope.value}")
        return Priority.P1, _rationale("P1", fired, "regression affecting more than one user, likely needs a fix in this cycle")

    # ---- P2: real but not urgent ----
    if _yes(signals.user_blocked) and scope == Scope.one and not _yes(signals.workaround_exists):
        fired.append("user_blocked=yes")
        fired.append("scope=one")
        fired.append("workaround_exists=no/unknown")
        return Priority.P2, _rationale("P2", fired, "single user blocked, no workaround, needs attention but not urgent")

    if _yes(signals.regression_suspected) and scope == Scope.one:
        fired.append("regression_suspected=yes")
        fired.append("scope=one")
        return Priority.P2, _rationale("P2", fired, "regression but limited blast radius")

    # ---- P3: degraded or cosmetic ----
    # Fallback for cases where nothing fires hard. If signals are mostly
    # unknown, this also catches "we don't know enough to escalate."
    if all(_unknown(getattr(signals, f)) for f in (
        "user_blocked", "data_loss_risk", "security_implication", "regression_suspected"
    )):
        return Priority.P3, _rationale(
            "P3",
            ["all severity signals unknown"],
            "no severity signals could be extracted; defaulting low so an engineer can re-triage with more context",
        )

    return Priority.P3, _rationale(
        "P3",
        ["no escalating signals fired"],
        "no signals suggesting blocking, data loss, security, or regression",
    )


def _rationale(priority: str, fired: list[str], explanation: str) -> str:
    if fired:
        return f"{priority}: {', '.join(fired)}. {explanation}."
    return f"{priority}: {explanation}."

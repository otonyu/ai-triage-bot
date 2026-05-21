"""Tests for the deterministic prioritization function.

We don't test LLM extraction here — that path is non-deterministic and
better validated with eval rubrics. We test the rule logic in isolation.
"""

from __future__ import annotations

import pytest

from src.prioritize import assign_priority
from src.schemas import Priority, Scope, Signals, TriBool


def make_signals(**overrides) -> Signals:
    base = {
        "user_blocked": TriBool.unknown,
        "data_loss_risk": TriBool.unknown,
        "security_implication": TriBool.unknown,
        "regression_suspected": TriBool.unknown,
        "workaround_exists": TriBool.unknown,
    }
    base.update(overrides)
    return Signals(**base)


class TestP0Rules:
    def test_data_loss_alone_is_p0(self):
        p, r = assign_priority(make_signals(data_loss_risk=TriBool.yes), Scope.one)
        assert p == Priority.P0
        assert "data_loss_risk" in r

    def test_security_alone_is_p0(self):
        p, r = assign_priority(make_signals(security_implication=TriBool.yes), Scope.one)
        assert p == Priority.P0
        assert "security_implication" in r

    def test_many_users_blocked_is_p0(self):
        p, r = assign_priority(make_signals(user_blocked=TriBool.yes), Scope.many)
        assert p == Priority.P0
        assert "user_blocked" in r and "scope=many" in r

    def test_data_loss_outranks_other_signals(self):
        # Even with security and blocking and scope, data_loss wins (cited first).
        p, r = assign_priority(
            make_signals(
                data_loss_risk=TriBool.yes,
                security_implication=TriBool.yes,
                user_blocked=TriBool.yes,
            ),
            Scope.many,
        )
        assert p == Priority.P0


class TestP1Rules:
    def test_blocked_some_is_p1(self):
        p, _ = assign_priority(make_signals(user_blocked=TriBool.yes), Scope.some)
        assert p == Priority.P1

    def test_regression_some_is_p1(self):
        p, _ = assign_priority(make_signals(regression_suspected=TriBool.yes), Scope.some)
        assert p == Priority.P1

    def test_regression_many_is_p1(self):
        p, _ = assign_priority(make_signals(regression_suspected=TriBool.yes), Scope.many)
        assert p == Priority.P1


class TestP2Rules:
    def test_single_blocked_no_workaround_is_p2(self):
        p, _ = assign_priority(
            make_signals(user_blocked=TriBool.yes, workaround_exists=TriBool.no),
            Scope.one,
        )
        assert p == Priority.P2

    def test_single_blocked_unknown_workaround_is_p2(self):
        # Unknown workaround should still be P2, not lower.
        p, _ = assign_priority(make_signals(user_blocked=TriBool.yes), Scope.one)
        assert p == Priority.P2

    def test_single_regression_is_p2(self):
        p, _ = assign_priority(make_signals(regression_suspected=TriBool.yes), Scope.one)
        assert p == Priority.P2


class TestP3Rules:
    def test_single_blocked_with_workaround_is_p3(self):
        p, _ = assign_priority(
            make_signals(user_blocked=TriBool.yes, workaround_exists=TriBool.yes),
            Scope.one,
        )
        assert p == Priority.P3

    def test_all_unknown_is_p3_with_explicit_rationale(self):
        p, r = assign_priority(make_signals(), Scope.unknown)
        assert p == Priority.P3
        assert "unknown" in r.lower()

    def test_explicit_no_signals_is_p3(self):
        p, _ = assign_priority(
            make_signals(
                user_blocked=TriBool.no,
                data_loss_risk=TriBool.no,
                security_implication=TriBool.no,
                regression_suspected=TriBool.no,
            ),
            Scope.one,
        )
        assert p == Priority.P3


class TestRationale:
    def test_rationale_always_cites_signals(self):
        p, r = assign_priority(make_signals(data_loss_risk=TriBool.yes), Scope.one)
        # Rationale should always cite the priority and explain.
        assert r.startswith("P0:")
        assert "." in r  # has a closing sentence

    def test_rationale_distinguishes_unknown_case(self):
        _, r_unknown = assign_priority(make_signals(), Scope.unknown)
        _, r_explicit_no = assign_priority(
            make_signals(
                user_blocked=TriBool.no,
                data_loss_risk=TriBool.no,
                security_implication=TriBool.no,
                regression_suspected=TriBool.no,
            ),
            Scope.one,
        )
        # The two P3 paths should have distinguishable rationale.
        assert r_unknown != r_explicit_no

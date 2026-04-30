"""Unit tests for intent classification.

Covers the generic classifier and the rule sets shipped by the
``flights`` and ``support_tickets`` domain packs. The goal is to lock
down the intended strategy (``sql_only`` / ``vector_only`` /
``vector_and_sql``) and tool selection for canned questions per
domain.
"""

from __future__ import annotations

import pytest
from app.core.agent.intent import IntentPlan, RuleBasedIntentClassifier
from app.core.domain import IntentRule
from app.domains.flights.intent_rules import (
    INTENT_RULES as FLIGHT_RULES,
    default_plan as flights_default,
)
from app.domains.support_tickets.intent_rules import (
    INTENT_RULES as TICKET_RULES,
    default_plan as tickets_default,
)


def _classify(rules, default, q: str) -> IntentPlan:
    return RuleBasedIntentClassifier(rules, default).classify(q)


# --- generic classifier ----------------------------------------------------


def test_classifier_returns_default_when_no_rule_matches():
    sentinel = IntentPlan(strategy="vector_only", vector_query="x", notes="default")

    def _default(q: str) -> IntentPlan:
        return sentinel

    classifier = RuleBasedIntentClassifier(rules=(), default_plan=_default)
    assert classifier.classify("anything") is sentinel


def test_classifier_first_matching_rule_wins():
    a = IntentPlan(strategy="sql_only", notes="A")
    b = IntentPlan(strategy="vector_only", notes="B")
    rules = (
        IntentRule(matches=lambda q: True, plan=lambda q: a),
        IntentRule(matches=lambda q: True, plan=lambda q: b),
    )
    classifier = RuleBasedIntentClassifier(
        rules=rules,
        default_plan=lambda q: IntentPlan(strategy="vector_only"),
    )
    assert classifier.classify("hi") is a


# --- flights rules ---------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected_strategy,expected_tool",
    [
        (
            "Which airports are most associated with delays?",
            "sql_only",
            "get_top_delay_airports",
        ),
        (
            "Were there any critical safety incidents this week?",
            "vector_and_sql",
            "get_incidents_by_severity",
        ),
        (
            "Any safety issues we should know about?",
            "vector_and_sql",
            "get_incidents_by_severity",
        ),
        (
            "Why are flights delayed today?",
            "vector_and_sql",
            "get_delayed_flights",
        ),
    ],
)
def test_flights_rules_pick_expected_strategy(question, expected_strategy, expected_tool):
    plan = _classify(FLIGHT_RULES, flights_default, question)
    assert plan.strategy == expected_strategy
    tool_names = [name for name, _ in plan.sql_calls]
    assert expected_tool in tool_names


def test_flights_default_plan_is_vector_only_over_flight_logs():
    plan = _classify(FLIGHT_RULES, flights_default, "What happened on the runway?")
    assert plan.strategy == "vector_only"
    assert plan.vector_resource == "flight_logs"
    assert plan.vector_query == "What happened on the runway?"


def test_flights_recent_window_attached_when_question_is_time_scoped():
    plan = _classify(
        FLIGHT_RULES,
        flights_default,
        "Were there any critical incidents in the last 3 days?",
    )
    # We can't assert exact timestamps, but the SQL call should have a
    # bounded window rather than None.
    assert plan.sql_calls, "expected an SQL call"
    args = plan.sql_calls[0][1]
    assert args["start_time"] is not None
    assert args["end_time"] is not None


# --- support_tickets rules -------------------------------------------------


@pytest.mark.parametrize(
    "question,expected_strategy,expected_tool",
    [
        (
            "Show me open tickets from this week.",
            "sql_only",
            "get_open_tickets",
        ),
        (
            "Any critical urgent tickets?",
            "vector_and_sql",
            "get_tickets_by_priority",
        ),
    ],
)
def test_tickets_rules_pick_expected_strategy(question, expected_strategy, expected_tool):
    plan = _classify(TICKET_RULES, tickets_default, question)
    assert plan.strategy == expected_strategy
    tool_names = [name for name, _ in plan.sql_calls]
    assert expected_tool in tool_names


@pytest.mark.parametrize(
    "question",
    [
        "I was charged twice for my last invoice",
        "I cannot login with my password",
    ],
)
def test_tickets_topical_rules_use_vector_only(question):
    plan = _classify(TICKET_RULES, tickets_default, question)
    assert plan.strategy == "vector_only"
    assert plan.vector_query == question


def test_tickets_default_plan_targets_ticket_messages():
    plan = _classify(TICKET_RULES, tickets_default, "what's going on?")
    assert plan.strategy == "vector_only"
    assert plan.vector_resource == "ticket_messages"

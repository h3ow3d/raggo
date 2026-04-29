"""Intent classification rules for the flights domain."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.core.agent.intent import IntentPlan
from app.core.domain import IntentRule


# Compiled keyword groups. Kept simple on purpose so the routing remains
# predictable; the LLM is never asked to pick a tool.
_DELAY_RE = re.compile(r"\bdelay(s|ed|ing)?\b", re.IGNORECASE)
_SAFETY_RE = re.compile(r"\bsafety\b|\bsafety issue|\bincident", re.IGNORECASE)
_SEVERE_RE = re.compile(
    r"\bsevere|\bcritical|\bserious|\bmajor\b", re.IGNORECASE
)
_AIRPORT_LIST_RE = re.compile(
    r"\bwhich airport|airports? (are )?most|airports? .* delays?",
    re.IGNORECASE,
)
_RECENT_RE = re.compile(
    r"\brecent(ly)?\b|\bthis week\b|\blast (\d+\s*)?(day|days|week|weeks)",
    re.IGNORECASE,
)


def _recent_window(question: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Return a ``(start, end)`` window when the question is time-scoped.

    Recognises "this week", "recent(ly)", and "last N day(s)/week(s)".
    Otherwise returns ``(None, None)`` so callers fall back to the
    default behaviour of the SQL tool.
    """
    if not _RECENT_RE.search(question):
        return None, None

    now = datetime.now(timezone.utc)
    # "last N day(s)/week(s)"
    m = re.search(
        r"last\s+(\d+)?\s*(day|days|week|weeks)", question, re.IGNORECASE
    )
    if m:
        n = int(m.group(1) or 1)
        unit = m.group(2).lower()
        delta = timedelta(days=n if unit.startswith("day") else 7 * n)
        return now - delta, now

    if re.search(r"this week", question, re.IGNORECASE):
        return now - timedelta(days=7), now

    # "recent" / "recently": 7-day window is a reasonable default.
    return now - timedelta(days=7), now


# --- Rule matchers and plan builders ---


def _airport_delay_aggregation_matches(question: str) -> bool:
    return bool(_AIRPORT_LIST_RE.search(question) and _DELAY_RE.search(question))


def _airport_delay_aggregation_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="sql_only",
        sql_calls=[
            (
                "get_top_delay_airports",
                {"start_time": start, "end_time": end, "limit": 10},
            )
        ],
        notes="airport-delay aggregation",
    )


def _severe_incident_matches(question: str) -> bool:
    return bool(_SEVERE_RE.search(question) and (
        _SAFETY_RE.search(question) or "incident" in question.lower()
    ))


def _severe_incident_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="vector_and_sql",
        sql_calls=[
            (
                "get_incidents_by_severity",
                {
                    "severity": ["critical"],
                    "start_time": start,
                    "end_time": end,
                    "limit": 10,
                },
            )
        ],
        vector_query=question,
        notes="severe-incident lookup with corroborating logs",
    )


def _safety_overview_matches(question: str) -> bool:
    return bool(_SAFETY_RE.search(question))


def _safety_overview_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="vector_and_sql",
        sql_calls=[
            (
                "get_incidents_by_severity",
                {
                    "severity": ["warning", "critical"],
                    "start_time": start,
                    "end_time": end,
                    "limit": 10,
                },
            )
        ],
        vector_query=question,
        vector_filters={"log_type": "safety"},
        notes="recent safety overview",
    )


def _delay_explanation_matches(question: str) -> bool:
    return bool(_DELAY_RE.search(question))


def _delay_explanation_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="vector_and_sql",
        sql_calls=[
            (
                "get_delayed_flights",
                {"start_time": start, "end_time": end, "limit": 10},
            )
        ],
        vector_query=question,
        notes="delay explanation: SQL summary + vector evidence",
    )


# --- Exported rules tuple ---


INTENT_RULES: Tuple[IntentRule, ...] = (
    IntentRule(matches=_airport_delay_aggregation_matches, plan=_airport_delay_aggregation_plan),
    IntentRule(matches=_severe_incident_matches, plan=_severe_incident_plan),
    IntentRule(matches=_safety_overview_matches, plan=_safety_overview_plan),
    IntentRule(matches=_delay_explanation_matches, plan=_delay_explanation_plan),
)


def default_plan(question: str) -> IntentPlan:
    """Default plan for questions that don't match any specific rule.
    
    Vector-only RAG over flight_logs.
    """
    return IntentPlan(
        strategy="vector_only",
        vector_query=question,
        vector_resource="flight_logs",
        notes="open-ended question; vector RAG over flight logs",
    )


__all__ = [
    "INTENT_RULES",
    "default_plan",
]

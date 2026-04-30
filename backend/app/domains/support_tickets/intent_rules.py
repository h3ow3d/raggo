"""Intent classification rules for the support_tickets domain."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Tuple

from app.core.agent.intent import IntentPlan
from app.core.domain import IntentRule

# Keyword patterns
_OPEN_TICKET_RE = re.compile(r"\bopen ticket|pending|unresolved", re.IGNORECASE)
_HIGH_PRIORITY_RE = re.compile(r"\bhigh priority|critical|urgent", re.IGNORECASE)
_BILLING_RE = re.compile(r"\bbilling|invoice|payment|charged|refund", re.IGNORECASE)
_LOGIN_RE = re.compile(r"\blogin|authentication|password|access", re.IGNORECASE)
_RECENT_RE = re.compile(
    r"\brecent(ly)?\b|\bthis week\b|\blast (\d+\s*)?(day|days|week|weeks)",
    re.IGNORECASE,
)


def _recent_window(question: str) -> Tuple[datetime | None, datetime | None]:
    """Return a (start, end) window when the question is time-scoped."""
    if not _RECENT_RE.search(question):
        return None, None

    now = datetime.now(UTC)
    m = re.search(r"last\s+(\d+)?\s*(day|days|week|weeks)", question, re.IGNORECASE)
    if m:
        n = int(m.group(1) or 1)
        unit = m.group(2).lower()
        delta = timedelta(days=n if unit.startswith("day") else 7 * n)
        return now - delta, now

    if re.search(r"this week", question, re.IGNORECASE):
        return now - timedelta(days=7), now

    return now - timedelta(days=7), now


# Rule matchers and plan builders


def _open_tickets_matches(question: str) -> bool:
    return bool(_OPEN_TICKET_RE.search(question))


def _open_tickets_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="sql_only",
        sql_calls=[("get_open_tickets", {"start_time": start, "end_time": end, "limit": 10})],
        notes="open tickets overview",
    )


def _high_priority_matches(question: str) -> bool:
    return bool(_HIGH_PRIORITY_RE.search(question))


def _high_priority_plan(question: str) -> IntentPlan:
    start, end = _recent_window(question)
    return IntentPlan(
        strategy="vector_and_sql",
        sql_calls=[
            (
                "get_tickets_by_priority",
                {"priority": "critical", "start_time": start, "end_time": end, "limit": 10},
            )
        ],
        vector_query=question,
        notes="critical/urgent tickets with evidence",
    )


def _billing_matches(question: str) -> bool:
    return bool(_BILLING_RE.search(question))


def _billing_plan(question: str) -> IntentPlan:
    return IntentPlan(
        strategy="vector_only",
        vector_query=question,
        notes="billing/payment issue search",
    )


def _login_matches(question: str) -> bool:
    return bool(_LOGIN_RE.search(question))


def _login_plan(question: str) -> IntentPlan:
    return IntentPlan(
        strategy="vector_only",
        vector_query=question,
        notes="login/authentication issue search",
    )


# Exported rules tuple


INTENT_RULES: Tuple[IntentRule, ...] = (
    IntentRule(matches=_open_tickets_matches, plan=_open_tickets_plan),
    IntentRule(matches=_high_priority_matches, plan=_high_priority_plan),
    IntentRule(matches=_billing_matches, plan=_billing_plan),
    IntentRule(matches=_login_matches, plan=_login_plan),
)


def default_plan(question: str) -> IntentPlan:
    """Default plan: vector-only RAG over ticket_messages."""
    return IntentPlan(
        strategy="vector_only",
        vector_query=question,
        vector_resource="ticket_messages",
        notes="open-ended question; vector RAG over ticket messages",
    )


__all__ = [
    "INTENT_RULES",
    "default_plan",
]

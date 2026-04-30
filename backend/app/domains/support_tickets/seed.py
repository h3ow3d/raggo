"""Seed data generation for the support_tickets domain.

Creates a realistic medium-sized dataset of support tickets and messages
on first startup. Designed to be idempotent.

The generated data intentionally includes:
- varied phrasing and abbreviations
- duplicate-looking issues
- vague descriptions
- realistic timestamps
- a mix of priorities and statuses
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Iterable, List, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.support_tickets.models import SupportTicket, TicketMessage

logger = logging.getLogger(__name__)

# Reference data
PRIORITIES: Sequence[str] = ("low", "medium", "high", "critical")
PRIORITY_WEIGHTS: Sequence[int] = (40, 35, 20, 5)

STATUSES: Sequence[str] = ("open", "in_progress", "resolved", "closed")
STATUS_WEIGHTS: Sequence[int] = (25, 20, 35, 20)

AUTHORS: Sequence[str] = ("customer", "agent", "system")

CUSTOMERS: Sequence[str] = (
    "alice@example.com",
    "bob@example.com",
    "charlie@example.com",
    "diana@example.com",
    "eve@example.com",
    "frank@example.com",
    "grace@example.com",
    "henry@example.com",
    "iris@example.com",
    "jack@example.com",
    "karen@example.com",
    "leo@example.com",
)

# Realistic subject pool
SUBJECTS: Sequence[str] = (
    "Login issues after password reset",
    "Can't access my account",
    "Billing discrepancy on latest invoice",
    "Double charged this month",
    "Feature request: dark mode",
    "Export to CSV not working",
    "Slow performance on dashboard",
    "Page loading very slowly",
    "Integration with Slack failing",
    "API timeout errors",
    "Missing data in report",
    "Report showing incorrect totals",
    "Need help understanding analytics",
    "How do I set up webhooks?",
    "Password reset email not received",
    "Account locked after too many login attempts",
    "Request refund for last month",
    "Cancellation request",
    "Upgrade plan to enterprise",
    "Add team member to account",
    "Remove user access",
    "Change billing email address",
    "Two-factor auth not working",
    "Mobile app crashing on iOS",
    "Android app won't sync",
    "API key not recognized",
    "Permission denied error on dashboard",
    "Chart not displaying correctly",
    "Download button broken",
    "Email notifications not arriving",
    "Webhook delivery failed",
)

# Realistic message bodies pool
MESSAGE_BODIES: Sequence[str] = (
    "I can't log in to my account. Tried resetting password but still getting errors.",
    "The invoice shows I was charged twice this month. Can you please check?",
    "Please add dark mode to the UI. My eyes hurt.",
    "When I try to export data to CSV, nothing happens. No error, just nothing.",
    "The dashboard is taking 30+ seconds to load. Is there an issue on your end?",
    "Our Slack integration stopped working yesterday. Getting 401 errors.",
    "API requests are timing out. Timeout set to 60s.",
    "The monthly report is missing data for the last week.",
    "Totals in the analytics dashboard don't match our records.",
    "Can someone explain what the engagement metric means?",
    "I didn't receive the password reset email. Checked spam too.",
    "Account locked. I think I entered wrong password 3 times.",
    "I'd like a refund for last month. Wasn't using the service.",
    "Please cancel my subscription effective immediately.",
    "We want to upgrade to the enterprise plan. What's the process?",
    "Need to add john.doe@company.com to our account.",
    "Remove jane.smith@company.com. She left the company.",
    "Change billing email to accounts@company.com instead of my personal email.",
    "2FA code not working. I've tried multiple times.",
    "App crashes when I open the settings page on my iPhone.",
    "Android app won't sync with the web version. Shows old data.",
    "My API key returns 'invalid key' error.",
    "Getting 'permission denied' when trying to access reports.",
    "The chart on the homepage isn't rendering. Just shows a grey box.",
    "Download button does nothing. No file downloaded.",
    "I'm not getting email notifications anymore. Checked settings, they're enabled.",
    "Webhook failed to deliver. Checked endpoint logs, nothing received.",
    "Thanks for looking into this!",
    "Understood, I'll try that.",
    "This is urgent, please prioritize.",
    "Still not working after your suggestion.",
    "That worked! Closing this ticket.",
    "Issue resolved. Thanks!",
    "We've checked on our end and found the issue. Fixed now.",
    "Assigned to the engineering team.",
    "This is a known bug, fix scheduled for next release.",
)


def _weighted_choice(rng: random.Random, options: Sequence[str], weights: Sequence[int]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def _generate_ticket_row(rng: random.Random, now: datetime) -> dict:
    offset_days = rng.uniform(-90, 0)
    opened_at = now + timedelta(days=offset_days, hours=rng.randint(0, 23))

    priority = _weighted_choice(rng, PRIORITIES, PRIORITY_WEIGHTS)
    status = _weighted_choice(rng, STATUSES, STATUS_WEIGHTS)

    closed_at = None
    if status in {"resolved", "closed"}:
        # Close 1-30 days after opening
        closed_at = opened_at + timedelta(days=rng.uniform(0.1, 30))

    return {
        "subject": rng.choice(SUBJECTS),
        "priority": priority,
        "status": status,
        "customer": rng.choice(CUSTOMERS),
        "opened_at": opened_at,
        "closed_at": closed_at,
    }


def _generate_message_row(rng: random.Random, ticket: SupportTicket) -> dict:
    # First message is usually from customer, then alternates
    offset_hours = rng.uniform(0, 24 * 5)  # Messages within 5 days of ticket opening
    message_time = ticket.opened_at + timedelta(hours=offset_hours)

    author = _weighted_choice(rng, AUTHORS, (50, 40, 10))
    body = rng.choice(MESSAGE_BODIES)

    return {
        "ticket_id": ticket.id,
        "author": author,
        "body": body,
        "created_at": message_time,
    }


def _bulk_insert(session: Session, model, rows: Iterable[dict], batch_size: int = 1000) -> int:
    total = 0
    batch: List[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            session.bulk_insert_mappings(model, batch)
            session.flush()
            total += len(batch)
            batch.clear()
    if batch:
        session.bulk_insert_mappings(model, batch)
        session.flush()
        total += len(batch)
    return total


def has_existing_data(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(SupportTicket)) > 0


def seed_database(session: Session, *, force: bool = False, seed: int = 42) -> dict:
    """Seed the database with realistic support ticket data.

    Returns a dict with insert counts. Idempotent unless ``force=True``.
    """
    if not force and has_existing_data(session):
        logger.info("Seed skipped: support_tickets table is not empty.")
        return {"tickets": 0, "messages": 0, "skipped": True}

    rng = random.Random(seed)
    now = datetime.now(tz=UTC)

    # Default: ~200 tickets, ~1000 messages
    ticket_count = 200
    avg_messages_per_ticket = 5

    logger.info(
        "Seeding %d tickets with ~%d messages...",
        ticket_count,
        ticket_count * avg_messages_per_ticket,
    )

    ticket_rows = (_generate_ticket_row(rng, now) for _ in range(ticket_count))
    inserted_tickets = _bulk_insert(session, SupportTicket, ticket_rows)

    # Reload tickets
    tickets = session.execute(select(SupportTicket.id, SupportTicket.opened_at)).all()
    if not tickets:
        logger.warning("No tickets present after insert; aborting message seed.")
        return {
            "tickets": inserted_tickets,
            "messages": 0,
            "skipped": False,
        }

    # Generate messages (variable count per ticket: 1-10)
    def _message_iter():
        for ticket_id, opened_at in tickets:
            # Create a minimal ticket object for _generate_message_row
            ticket_obj = type("obj", (object,), {"id": ticket_id, "opened_at": opened_at})()
            num_messages = rng.randint(1, 10)
            for _ in range(num_messages):
                yield _generate_message_row(rng, ticket_obj)

    inserted_messages = _bulk_insert(session, TicketMessage, _message_iter())

    logger.info(
        "Seed complete: tickets=%d messages=%d",
        inserted_tickets,
        inserted_messages,
    )

    return {
        "tickets": inserted_tickets,
        "messages": inserted_messages,
        "skipped": False,
    }

"""Support tickets domain pack assembly.

Exports DOMAIN, a complete DomainPack instance for customer support ticket operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.domain import DomainPack, DisplayMetadata, EmbeddableResource, FilterSpec
from app.domains.support_tickets.intent_rules import INTENT_RULES, default_plan
from app.domains.support_tickets.models import Base, SupportTicket, TicketMessage
from app.domains.support_tickets.prompts import DOMAIN_CONTEXT, format_evidence
from app.domains.support_tickets.seed import has_existing_data, seed_database
from app.domains.support_tickets.sql_tools import SQL_TOOLS


def _ticket_message_evidence_projection(row: TicketMessage) -> Dict[str, Any]:
    """Project a TicketMessage ORM row into evidence format."""
    ticket = row.ticket  # SQLAlchemy relationship
    return {
        "type": "ticket_message",
        "id": row.id,
        "ticket_id": row.ticket_id,
        "ticket_subject": ticket.subject if ticket else None,
        "ticket_priority": ticket.priority if ticket else None,
        "ticket_status": ticket.status if ticket else None,
        "author": row.author,
        "message": row.body,
    }


# EmbeddableResource definition


TICKET_MESSAGES_RESOURCE = EmbeddableResource(
    name="ticket_messages",
    model=TicketMessage,
    text_column="body",
    embedding_column="embedding",
    embedding_model_column="embedding_model",
    embedding_dim_column="embedding_dim",
    embedded_at_column="embedded_at",
    filter_spec=FilterSpec(columns={
        "author": "author",
        "ticket_id": "ticket_id",
    }),
    evidence_projection=_ticket_message_evidence_projection,
    joins=(
        # Join to SupportTicket to include ticket context in evidence
        (SupportTicket, lambda tm, st: st.id == tm.ticket_id),
    ),
)


def _stats(session: Session) -> Dict[str, int]:
    """Return dashboard stats for support_tickets domain."""
    total_tickets = session.scalar(select(func.count()).select_from(SupportTicket)) or 0
    total_messages = session.scalar(select(func.count()).select_from(TicketMessage)) or 0
    open_tickets = (
        session.scalar(
            select(func.count()).where(SupportTicket.status.in_({"open", "in_progress"}))
        )
        or 0
    )
    critical_tickets = (
        session.scalar(
            select(func.count()).where(SupportTicket.priority == "critical")
        )
        or 0
    )
    
    return {
        "total_tickets": int(total_tickets),
        "total_messages": int(total_messages),
        "open_tickets": int(open_tickets),
        "critical_tickets": int(critical_tickets),
    }


# DomainPack assembly


DOMAIN = DomainPack(
    name="support_tickets",
    sqlalchemy_base=Base,
    init_sql_path=str(Path(__file__).parent / "init.sql"),
    embeddable_resources=(TICKET_MESSAGES_RESOURCE,),
    sql_tools=SQL_TOOLS,
    intent_rules=INTENT_RULES,
    seed=seed_database,
    has_existing_data=has_existing_data,
    stats=_stats,
    domain_context=DOMAIN_CONTEXT,
    evidence_formatter=format_evidence,
    display=DisplayMetadata(
        domain_name="support_tickets",
        title="rag-support-lab",
        record_label_singular="ticket",
        record_label_plural="tickets",
        dashboard_stats=[
            {"label": "Tickets", "kind": "count", "resource": "support_tickets"},
            {"label": "Messages", "kind": "count", "resource": "ticket_messages"},
            {"label": "Embedded", "kind": "count_embedded", "resource": "ticket_messages"},
            {"label": "Open", "kind": "count_open", "resource": "support_tickets"},
        ],
    ),
)


__all__ = [
    "DOMAIN",
]

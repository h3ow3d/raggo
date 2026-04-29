"""Allowlisted, parameterised SQL tools for the support_tickets domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.domain import SafeSqlTool
from app.domains.support_tickets.models import SupportTicket, TicketMessage

# Bounds
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

ALLOWED_PRIORITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})
ALLOWED_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "resolved", "closed"})


def _clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        return 1
    if limit > MAX_LIMIT:
        return MAX_LIMIT
    return int(limit)


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return str(value) if value is not None else ""


def _serialise_ticket(ticket: SupportTicket) -> Dict[str, Any]:
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "priority": ticket.priority,
        "status": ticket.status,
        "customer": ticket.customer,
        "opened_at": ticket.opened_at,
        "closed_at": ticket.closed_at,
    }


def _serialise_message(msg: TicketMessage, ticket: Optional[SupportTicket] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": msg.id,
        "ticket_id": msg.ticket_id,
        "author": msg.author,
        "body": msg.body,
    }
    if ticket is not None:
        row["ticket_subject"] = ticket.subject
        row["ticket_priority"] = ticket.priority
        row["ticket_status"] = ticket.status
    return row


# Pydantic argument models


class GetOpenTicketsArgs(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = Field(default=None, ge=1, le=MAX_LIMIT)


class GetTicketsByPriorityArgs(BaseModel):
    priority: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = Field(default=None, ge=1, le=MAX_LIMIT)


class GetMessagesByTicketArgs(BaseModel):
    ticket_id: int = Field(..., ge=1)
    limit: Optional[int] = Field(default=None, ge=1, le=MAX_LIMIT)


class GetTopPriorityCountsArgs(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = Field(default=None, ge=1, le=MAX_LIMIT)


# Tool implementations


def get_open_tickets(
    session: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return tickets with status 'open' or 'in_progress'."""
    eff_limit = _clamp_limit(limit)
    
    stmt = select(SupportTicket).where(
        SupportTicket.status.in_({"open", "in_progress"})
    )
    if start_time is not None:
        stmt = stmt.where(SupportTicket.opened_at >= start_time)
    if end_time is not None:
        stmt = stmt.where(SupportTicket.opened_at <= end_time)
    
    stmt = stmt.order_by(desc(SupportTicket.opened_at)).limit(eff_limit)
    rows = session.execute(stmt).scalars().all()
    return [_serialise_ticket(t) for t in rows]


def get_tickets_by_priority(
    session: Session,
    priority: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return tickets matching a specific priority."""
    if priority not in ALLOWED_PRIORITIES:
        raise ValueError(
            f"unsupported priority: {priority!r}. Allowed: {sorted(ALLOWED_PRIORITIES)}"
        )
    
    eff_limit = _clamp_limit(limit)
    
    stmt = select(SupportTicket).where(SupportTicket.priority == priority)
    if start_time is not None:
        stmt = stmt.where(SupportTicket.opened_at >= start_time)
    if end_time is not None:
        stmt = stmt.where(SupportTicket.opened_at <= end_time)
    
    stmt = stmt.order_by(desc(SupportTicket.opened_at)).limit(eff_limit)
    rows = session.execute(stmt).scalars().all()
    return [_serialise_ticket(t) for t in rows]


def get_messages_by_ticket(
    session: Session,
    ticket_id: int,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return messages for a given ticket."""
    try:
        ticket_id_int = int(ticket_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ticket_id must be a positive integer") from exc
    if ticket_id_int <= 0:
        raise ValueError("ticket_id must be a positive integer")
    
    eff_limit = _clamp_limit(limit)
    
    stmt = (
        select(TicketMessage, SupportTicket)
        .join(SupportTicket, SupportTicket.id == TicketMessage.ticket_id)
        .where(TicketMessage.ticket_id == ticket_id_int)
        .order_by(TicketMessage.created_at.asc())
        .limit(eff_limit)
    )
    return [
        _serialise_message(msg, ticket) for msg, ticket in session.execute(stmt).all()
    ]


def get_top_priority_counts(
    session: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return ticket counts grouped by priority."""
    eff_limit = _clamp_limit(limit)
    
    stmt = select(
        SupportTicket.priority,
        func.count().label("count"),
    )
    if start_time is not None:
        stmt = stmt.where(SupportTicket.opened_at >= start_time)
    if end_time is not None:
        stmt = stmt.where(SupportTicket.opened_at <= end_time)
    
    stmt = (
        stmt.group_by(SupportTicket.priority)
        .order_by(func.count().desc())
        .limit(eff_limit)
    )
    
    return [
        {"priority": row.priority, "count": int(row.count)}
        for row in session.execute(stmt).all()
    ]


# Evidence builders


def _evidence_from_ticket(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "ticket",
        "id": int(row["id"]),
        "subject": row.get("subject"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "customer": row.get("customer"),
        "opened_at": _format_dt(row.get("opened_at")),
        "closed_at": _format_dt(row.get("closed_at")),
    }


def _evidence_from_message(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "ticket_message",
        "id": int(row["id"]),
        "ticket_id": row.get("ticket_id"),
        "ticket_subject": row.get("ticket_subject"),
        "ticket_priority": row.get("ticket_priority"),
        "ticket_status": row.get("ticket_status"),
        "author": row.get("author"),
        "message": row.get("body"),
    }


def _evidence_from_priority_count(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "priority_count",
        "priority": row.get("priority"),
        "count": row.get("count"),
    }


# SafeSqlTool exports


SQL_TOOLS: Tuple[SafeSqlTool, ...] = (
    SafeSqlTool(
        name="get_open_tickets",
        func=get_open_tickets,
        args_model=GetOpenTicketsArgs,
        evidence_builder=_evidence_from_ticket,
        description="Return tickets with status 'open' or 'in_progress'",
    ),
    SafeSqlTool(
        name="get_tickets_by_priority",
        func=get_tickets_by_priority,
        args_model=GetTicketsByPriorityArgs,
        evidence_builder=_evidence_from_ticket,
        description="Return tickets matching a specific priority",
    ),
    SafeSqlTool(
        name="get_messages_by_ticket",
        func=get_messages_by_ticket,
        args_model=GetMessagesByTicketArgs,
        evidence_builder=_evidence_from_message,
        description="Return messages for a given ticket",
    ),
    SafeSqlTool(
        name="get_top_priority_counts",
        func=get_top_priority_counts,
        args_model=GetTopPriorityCountsArgs,
        evidence_builder=_evidence_from_priority_count,
        description="Return ticket counts grouped by priority",
    ),
)


__all__ = [
    "SQL_TOOLS",
    "get_open_tickets",
    "get_tickets_by_priority",
    "get_messages_by_ticket",
    "get_top_priority_counts",
]

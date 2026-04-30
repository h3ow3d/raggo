"""Flights domain pack for raggo.

This module bundles all flight operations-specific configuration:
- ORM models (Flight, FlightLog, Incident)
- Seed data generation
- Safe SQL tools
- Intent classification rules
- Prompt fragments
- Vector search configuration
"""

from __future__ import annotations

import os
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.domain import (
    DisplayMetadata,
    DomainPack,
    EmbeddableResource,
    FilterSpec,
)
from app.domains.flights import intent_rules, prompts, sql_tools
from app.domains.flights.models import Base, Flight, FlightLog, Incident
from app.domains.flights.seed import has_existing_data, seed_database

# ---------------------------------------------------------------------------
# Embeddable resources
# ---------------------------------------------------------------------------


def _flight_log_evidence_projection(log: FlightLog) -> Dict[str, Any]:
    """Project a flight_log row (with joined flight) into evidence metadata."""
    # When joined, we have access to both log and flight via relationships
    flight = log.flight if hasattr(log, "flight") else None
    return {
        "log_id": log.id,
        "flight_id": log.flight_id,
        "flight_number": flight.flight_number if flight else None,
        "origin": flight.origin if flight else None,
        "destination": flight.destination if flight else None,
        "log_time": log.log_time,
        "log_type": log.log_type,
        "source_system": log.source_system,
        "severity": log.severity,
        "message": log.message,
    }


FLIGHT_LOGS_RESOURCE = EmbeddableResource(
    name="flight_logs",
    model=FlightLog,
    text_column="message",
    embedding_column="embedding",
    embedding_model_column="embedding_model",
    embedding_dim_column="embedding_dim",
    embedded_at_column="embedded_at",
    filter_spec=FilterSpec(
        columns={
            "severity": "severity",
            "source_system": "source_system",
            "log_type": "log_type",
            "flight_id": "flight_id",
        }
    ),
    evidence_projection=_flight_log_evidence_projection,
    joins=((Flight, lambda log_cls, flight_cls: flight_cls.id == log_cls.flight_id),),
)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def stats(session: Session) -> Dict[str, Any]:
    """Return dashboard stats for the flights domain."""
    flights_count = session.scalar(select(func.count()).select_from(Flight)) or 0
    logs_count = session.scalar(select(func.count()).select_from(FlightLog)) or 0
    incidents_count = session.scalar(select(func.count()).select_from(Incident)) or 0
    embedded_count = (
        session.scalar(
            select(func.count()).select_from(FlightLog).where(FlightLog.embedding.is_not(None))
        )
        or 0
    )

    return {
        "flights": flights_count,
        "flight_logs": logs_count,
        "incidents": incidents_count,
        "embedded_logs": embedded_count,
        "unembedded_logs": logs_count - embedded_count,
    }


# ---------------------------------------------------------------------------
# Domain pack assembly
# ---------------------------------------------------------------------------


_INIT_SQL_PATH = os.path.join(os.path.dirname(__file__), "init.sql")


DOMAIN = DomainPack(
    name="flights",
    sqlalchemy_base=Base,
    init_sql_path=_INIT_SQL_PATH,
    embeddable_resources=(FLIGHT_LOGS_RESOURCE,),
    sql_tools=sql_tools.SQL_TOOLS,
    intent_rules=intent_rules.INTENT_RULES,
    seed=seed_database,
    has_existing_data=has_existing_data,
    stats=stats,
    domain_context=prompts.DOMAIN_CONTEXT,
    evidence_formatter=prompts.format_evidence,
    default_intent_plan=intent_rules.default_plan,
    display=DisplayMetadata(
        domain_name="flights",
        title="rag-flight-lab",
        record_label_singular="flight log",
        record_label_plural="flight logs",
        version="0.1.0",
        description="Flight operations analysis with logs, incidents, and delays.",
        dashboard_stats=[
            {"label": "Flights", "kind": "count", "resource": "flights"},
            {"label": "Logs", "kind": "count", "resource": "flight_logs"},
            {"label": "Incidents", "kind": "count", "resource": "incidents"},
            {"label": "Embedded", "kind": "count_embedded", "resource": "flight_logs"},
        ],
    ),
)


__all__ = [
    "DOMAIN",
    "Flight",
    "FlightLog",
    "Incident",
]

"""Allowlisted, parameterised SQL tools for the agent.

The agent must never execute arbitrary model-generated SQL. Instead it
chooses among a small set of well-defined functions in this module. Each
function:

- accepts plain Python arguments (validated below),
- builds its query through SQLAlchemy ORM / Core (parameterised),
- applies a sensible default and hard cap on result count,
- returns a list of plain dicts that are easy to serialise as evidence.

These tools are also useful directly from the API for ad-hoc inspection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from .models import Flight, FlightLog, Incident

# ---------------------------------------------------------------------------
# Bounds and allowlists
# ---------------------------------------------------------------------------

# Hard cap applied to every safe-SQL tool, regardless of caller input.
# Keeps query work bounded even if the agent asks for too much.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

# Severities mirror the seed data; centralising the allowlist keeps the
# agent's intent classifier and the SQL layer in agreement.
ALLOWED_SEVERITIES: frozenset[str] = frozenset(
    {"info", "low", "warning", "critical"}
)

# Statuses that count as "delayed" for the delay-oriented tools. The seed
# also produces flights with "delayed" status; we additionally treat any
# flight whose actual_departure runs significantly later than the
# scheduled time as delayed.
DELAYED_STATUSES: frozenset[str] = frozenset({"delayed"})
# Minutes of departure slip required to be considered delayed when the
# status itself is not "delayed".
DELAY_MINUTES_THRESHOLD = 15


def _clamp_limit(limit: Optional[int]) -> int:
    """Clamp a caller-supplied limit to ``[1, MAX_LIMIT]``."""
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        return 1
    if limit > MAX_LIMIT:
        return MAX_LIMIT
    return int(limit)


def _serialise_flight(flight: Flight) -> Dict[str, Any]:
    return {
        "id": flight.id,
        "flight_number": flight.flight_number,
        "airline": flight.airline,
        "aircraft_type": flight.aircraft_type,
        "tail_number": flight.tail_number,
        "origin": flight.origin,
        "destination": flight.destination,
        "scheduled_departure": flight.scheduled_departure,
        "actual_departure": flight.actual_departure,
        "scheduled_arrival": flight.scheduled_arrival,
        "actual_arrival": flight.actual_arrival,
        "status": flight.status,
    }


def _serialise_log(log: FlightLog, flight: Optional[Flight] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": log.id,
        "flight_id": log.flight_id,
        "log_time": log.log_time,
        "log_type": log.log_type,
        "source_system": log.source_system,
        "severity": log.severity,
        "message": log.message,
    }
    if flight is not None:
        row["flight_number"] = flight.flight_number
        row["origin"] = flight.origin
        row["destination"] = flight.destination
    return row


def _serialise_incident(
    incident: Incident, flight: Optional[Flight] = None
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": incident.id,
        "flight_id": incident.flight_id,
        "incident_time": incident.incident_time,
        "severity": incident.severity,
        "category": incident.category,
        "description": incident.description,
        "resolution_status": incident.resolution_status,
    }
    if flight is not None:
        row["flight_number"] = flight.flight_number
        row["origin"] = flight.origin
        row["destination"] = flight.destination
    return row


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def get_delayed_flights(
    session: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return flights that are delayed in ``[start_time, end_time]``.

    A flight is "delayed" if its status is one of
    :data:`DELAYED_STATUSES` *or* its ``actual_departure`` is at least
    :data:`DELAY_MINUTES_THRESHOLD` minutes after ``scheduled_departure``.
    """
    eff_limit = _clamp_limit(limit)

    delay_seconds = DELAY_MINUTES_THRESHOLD * 60
    delay_expr = func.extract(
        "epoch", Flight.actual_departure - Flight.scheduled_departure
    )

    stmt = select(Flight).where(
        or_(
            Flight.status.in_(DELAYED_STATUSES),
            and_(
                Flight.actual_departure.is_not(None),
                delay_expr >= delay_seconds,
            ),
        )
    )
    if start_time is not None:
        stmt = stmt.where(Flight.scheduled_departure >= start_time)
    if end_time is not None:
        stmt = stmt.where(Flight.scheduled_departure <= end_time)

    stmt = stmt.order_by(desc(Flight.scheduled_departure)).limit(eff_limit)
    rows = session.execute(stmt).scalars().all()
    return [_serialise_flight(f) for f in rows]


def get_incidents_by_severity(
    session: Session,
    severity: str | Sequence[str],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return incidents matching one or more severities."""
    if isinstance(severity, str):
        severities: List[str] = [severity]
    else:
        severities = list(severity)

    cleaned: List[str] = []
    for sev in severities:
        if sev not in ALLOWED_SEVERITIES:
            raise ValueError(
                f"unsupported severity: {sev!r}. Allowed: "
                f"{sorted(ALLOWED_SEVERITIES)}"
            )
        cleaned.append(sev)
    if not cleaned:
        raise ValueError("at least one severity is required")

    eff_limit = _clamp_limit(limit)

    stmt = (
        select(Incident, Flight)
        .join(Flight, Flight.id == Incident.flight_id)
        .where(Incident.severity.in_(cleaned))
    )
    if start_time is not None:
        stmt = stmt.where(Incident.incident_time >= start_time)
    if end_time is not None:
        stmt = stmt.where(Incident.incident_time <= end_time)

    stmt = stmt.order_by(desc(Incident.incident_time)).limit(eff_limit)

    return [
        _serialise_incident(inc, fl)
        for inc, fl in session.execute(stmt).all()
    ]


def get_flights_by_airport(
    session: Session,
    airport: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return flights with origin or destination matching ``airport``."""
    if not airport or not airport.strip():
        raise ValueError("airport must be non-empty")
    code = airport.strip().upper()
    eff_limit = _clamp_limit(limit)

    stmt = select(Flight).where(
        or_(Flight.origin == code, Flight.destination == code)
    )
    if start_time is not None:
        stmt = stmt.where(Flight.scheduled_departure >= start_time)
    if end_time is not None:
        stmt = stmt.where(Flight.scheduled_departure <= end_time)

    stmt = stmt.order_by(desc(Flight.scheduled_departure)).limit(eff_limit)
    rows = session.execute(stmt).scalars().all()
    return [_serialise_flight(f) for f in rows]


def get_logs_by_flight(
    session: Session,
    flight_id: int,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return the most recent logs for a given flight."""
    try:
        flight_id_int = int(flight_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("flight_id must be a positive integer") from exc
    if flight_id_int <= 0:
        raise ValueError("flight_id must be a positive integer")

    eff_limit = _clamp_limit(limit)

    stmt = (
        select(FlightLog, Flight)
        .join(Flight, Flight.id == FlightLog.flight_id)
        .where(FlightLog.flight_id == flight_id_int)
        .order_by(desc(FlightLog.log_time))
        .limit(eff_limit)
    )
    return [
        _serialise_log(log, fl) for log, fl in session.execute(stmt).all()
    ]


def get_top_delay_airports(
    session: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return airports most frequently associated with delayed flights.

    Counts each delayed flight twice: once for its origin and once for
    its destination, so the result captures airports involved in delay
    activity overall (which is what an operator usually wants when
    asking "which airports are most associated with delays?").
    """
    eff_limit = _clamp_limit(limit)

    delay_seconds = DELAY_MINUTES_THRESHOLD * 60
    delay_expr = func.extract(
        "epoch", Flight.actual_departure - Flight.scheduled_departure
    )

    base = select(Flight).where(
        or_(
            Flight.status.in_(DELAYED_STATUSES),
            and_(
                Flight.actual_departure.is_not(None),
                delay_expr >= delay_seconds,
            ),
        )
    )
    if start_time is not None:
        base = base.where(Flight.scheduled_departure >= start_time)
    if end_time is not None:
        base = base.where(Flight.scheduled_departure <= end_time)

    delayed = base.subquery()

    # Combine origin/destination counts via UNION ALL then aggregate.
    origin_q = select(delayed.c.origin.label("airport")).select_from(delayed)
    dest_q = select(delayed.c.destination.label("airport")).select_from(delayed)
    union_q = origin_q.union_all(dest_q).subquery()

    stmt = (
        select(
            union_q.c.airport.label("airport"),
            func.count().label("delay_count"),
        )
        .group_by(union_q.c.airport)
        .order_by(desc("delay_count"))
        .limit(eff_limit)
    )

    return [
        {"airport": row.airport, "delay_count": int(row.delay_count)}
        for row in session.execute(stmt).all()
    ]


# ---------------------------------------------------------------------------
# Allowlist used by the agent
# ---------------------------------------------------------------------------

# Mapping of safe tool name -> callable. The agent only invokes tools
# whose names appear in this dict; raw model output is never used to
# pick a function or build SQL.
SAFE_TOOLS: Dict[str, Any] = {
    "get_delayed_flights": get_delayed_flights,
    "get_incidents_by_severity": get_incidents_by_severity,
    "get_flights_by_airport": get_flights_by_airport,
    "get_logs_by_flight": get_logs_by_flight,
    "get_top_delay_airports": get_top_delay_airports,
}

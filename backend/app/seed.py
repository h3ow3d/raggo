"""Seed data generation for rag-flight-lab.

Creates a realistic medium-sized dataset of flights, flight logs, and
incidents on first startup. Designed to be idempotent: if data already
exists, seeding is skipped unless explicitly requested.

The generated data intentionally includes:

- varied phrasing and abbreviations
- duplicate-looking messages
- vague messages and operational noise
- inconsistent wording
- realistic timestamps spread over the recent past
- a mix of severities and categories
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Flight, FlightLog, Incident

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

AIRLINES: Sequence[tuple[str, str]] = (
    ("BA", "British Airways"),
    ("AA", "American Airlines"),
    ("DL", "Delta Air Lines"),
    ("UA", "United Airlines"),
    ("LH", "Lufthansa"),
    ("AF", "Air France"),
    ("KL", "KLM"),
    ("EK", "Emirates"),
    ("QF", "Qantas"),
    ("SQ", "Singapore Airlines"),
    ("IB", "Iberia"),
    ("AC", "Air Canada"),
)

AIRCRAFT_TYPES: Sequence[str] = (
    "A320", "A321", "A330", "A350", "A380",
    "B737", "B738", "B739", "B777", "B787",
    "E190", "CRJ900",
)

AIRPORTS: Sequence[str] = (
    "LHR", "LGW", "JFK", "LAX", "ORD", "ATL", "DFW", "SFO",
    "CDG", "AMS", "FRA", "MUC", "MAD", "FCO", "ZRH", "VIE",
    "DXB", "DOH", "SIN", "HKG", "NRT", "HND", "SYD", "MEL",
    "YYZ", "YVR", "MEX", "GRU", "EZE", "BOM", "DEL",
)

FLIGHT_STATUSES: Sequence[str] = (
    "scheduled", "departed", "arrived", "delayed", "cancelled", "diverted",
)

LOG_TYPES: Sequence[str] = (
    "operational", "maintenance", "safety", "weather", "atc", "passenger",
    "crew", "cargo", "ground", "system",
)

SOURCE_SYSTEMS: Sequence[str] = (
    "ACARS", "FOC", "OPS_NOTES", "MX_LOG", "CREW_REPORT", "ATC_LOG", "SYSTEM_AUTO",
)

SEVERITIES: Sequence[str] = ("info", "low", "warning", "critical")
SEVERITY_WEIGHTS: Sequence[int] = (60, 20, 15, 5)

INCIDENT_CATEGORIES: Sequence[str] = (
    "weather", "maintenance", "engine", "hydraulic", "avionics",
    "crew", "passenger", "ATC", "runway", "gate", "fuelling",
    "de-icing", "turbulence", "security", "medical", "baggage",
    "catering", "cleaning", "false alarm",
)

RESOLUTION_STATUSES: Sequence[str] = (
    "open", "investigating", "resolved", "closed", "monitoring",
)

# Pool of realistic, deliberately messy log messages.
LOG_MESSAGE_POOL: Sequence[str] = (
    "ENG vibration noted during climb, within tolerance but flagged for post-flight inspection.",
    "Engine #2 vibration spike on climb-out, returned to nominal after 90s.",
    "ATC hold assigned due to congestion at destination.",
    "ATC requested holding pattern at FL240 due to traffic.",
    "Late inbound aircraft causing estimated 42 minute delay.",
    "Inbound aircraft late, knock-on delay expected.",
    "Cabin crew reported unusual odour near rear galley; MX inspection requested.",
    "Crew report: faint smell in aft galley, no smoke, MX advised.",
    "HYD pressure fluctuation detected; no immediate action required.",
    "Hydraulic system B pressure dipped briefly, self-recovered.",
    "Destination weather below approach minima; holding pattern initiated.",
    "WX at destination marginal, holding for improvement.",
    "Passenger medical event reported during boarding; departure paused.",
    "Medical assistance called for passenger in row 14.",
    "De-icing completed, queue delay due to pad congestion.",
    "De-ice pad backed up, expect 25min delay.",
    "Bird strike suspected on approach; visual inspection scheduled.",
    "Possible bird strike on short final, MX to inspect leading edge.",
    "Turbulence encountered FL360, seatbelt sign cycled.",
    "Moderate CAT reported by preceding traffic.",
    "Runway change to 27R requested by ATC.",
    "Gate change announced due to ground equipment issue.",
    "Catering loading delayed, departure impacted.",
    "Baggage loading paused due to ramp safety incident.",
    "Fuelling truck arrived late, delay in turnaround.",
    "Avionics caution: TCAS self-test flagged, cleared after reset.",
    "Avionics warning briefly displayed and cleared on its own.",
    "Cabin pressure schedule normal, no anomalies.",
    "Routine post-flight walk-around, no findings.",
    "Tire wear noted on left main, within limits.",
    "APU start unsuccessful first attempt, normal on second.",
    "Lavatory smoke detector false alarm during cruise.",
    "Smoke alarm triggered in aft lav, confirmed false alarm.",
    "Security screening flagged item; crew notified, resolved.",
    "Unattended bag reported at gate, cleared by ground security.",
    "Passenger disturbance during boarding, captain involved.",
    "Disruptive PAX in row 22, calmed by crew.",
    "Crew rest exceeded duty time, replacement scheduled.",
    "Captain duty time approaching limit, crew swap arranged.",
    "ATC frequency change handled, no impact.",
    "Minor thunderstorm activity noted along route, deviation requested.",
    "Heavy rain at origin, taxi delays.",
    "Snow accumulation on apron, runway closure 15min.",
    "Slight delay due to ramp congestion, no operational impact.",
    "No issues observed during this segment.",
    "Routine flight, no significant events to report.",
    "Standard ops, all systems normal.",
    "ACARS message acknowledged: maintenance OK.",
    "FMS waypoint reload, no further action.",
    "TCAS RA inhibited momentarily on approach (expected).",
    "Engine oil temp slightly elevated, monitor trend.",
    "Brake temp elevated after landing, cooling time extended.",
    "Pushback delayed due to tug malfunction.",
    "Tug breakdown at gate, alternate tug requested.",
    "Cargo door indicator flickered on close, reclosed and verified.",
    "Cargo loading discrepancy resolved before departure.",
    "Fuel quantity discrepancy investigated, paperwork updated.",
    "Galley equipment failure: oven inop, deferred per MEL.",
    "Coffee maker inop, MEL applied.",
    "Cabin lighting flicker resolved by reset.",
    "PA system intermittent, MX deferred.",
    "Wi-Fi system unavailable for portion of flight.",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rand_tail(rng: random.Random) -> str:
    letters = "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=4))
    return f"G-{letters}"


def _rand_flight_number(rng: random.Random, code: str) -> str:
    return f"{code}{rng.randint(1, 9999):04d}"


def _weighted_choice(rng: random.Random, options: Sequence[str], weights: Sequence[int]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def _pick_status(rng: random.Random) -> str:
    # Weighted toward typical operations.
    return rng.choices(
        FLIGHT_STATUSES,
        weights=(15, 25, 35, 15, 5, 5),
        k=1,
    )[0]


def _maybe_jitter(rng: random.Random, dt: datetime, max_minutes: int) -> datetime:
    minutes = rng.randint(-max_minutes, max_minutes)
    return dt + timedelta(minutes=minutes)


def _generate_flight_row(rng: random.Random, now: datetime) -> dict:
    code, airline = rng.choice(AIRLINES)
    aircraft = rng.choice(AIRCRAFT_TYPES)
    tail = _rand_tail(rng)
    origin, destination = rng.sample(AIRPORTS, 2)

    # Spread scheduled departures across the last 90 days and next 7 days.
    offset_days = rng.uniform(-90, 7)
    sched_dep = now + timedelta(days=offset_days, minutes=rng.randint(0, 24 * 60 - 1))
    flight_minutes = rng.randint(45, 14 * 60)
    sched_arr = sched_dep + timedelta(minutes=flight_minutes)

    status = _pick_status(rng)
    actual_dep: datetime | None
    actual_arr: datetime | None
    if status in {"arrived", "departed", "diverted"}:
        actual_dep = _maybe_jitter(rng, sched_dep, 60)
        actual_arr = (
            _maybe_jitter(rng, sched_arr, 60) if status == "arrived" else None
        )
    elif status == "delayed":
        actual_dep = sched_dep + timedelta(minutes=rng.randint(20, 240))
        actual_arr = None
    else:  # scheduled, cancelled
        actual_dep = None
        actual_arr = None

    return {
        "flight_number": _rand_flight_number(rng, code),
        "airline": airline,
        "aircraft_type": aircraft,
        "tail_number": tail,
        "origin": origin,
        "destination": destination,
        "scheduled_departure": sched_dep,
        "actual_departure": actual_dep,
        "scheduled_arrival": sched_arr,
        "actual_arrival": actual_arr,
        "status": status,
    }


def _generate_log_row(rng: random.Random, flight: Flight) -> dict:
    # Place log time near the flight's scheduled window.
    base = flight.scheduled_departure
    offset_minutes = rng.randint(-30, 12 * 60)
    log_time = base + timedelta(minutes=offset_minutes)

    severity = _weighted_choice(rng, SEVERITIES, SEVERITY_WEIGHTS)
    message = rng.choice(LOG_MESSAGE_POOL)
    log_type = rng.choice(LOG_TYPES)
    source = rng.choice(SOURCE_SYSTEMS)

    metadata = {
        "phase": rng.choice(["pre-flight", "taxi", "takeoff", "climb", "cruise",
                             "descent", "approach", "landing", "post-flight"]),
        "auto_generated": rng.random() < 0.4,
    }
    if rng.random() < 0.2:
        metadata["altitude_ft"] = rng.randint(0, 41000)
    if rng.random() < 0.15:
        metadata["heading_deg"] = rng.randint(0, 359)

    return {
        "flight_id": flight.id,
        "log_time": log_time,
        "log_type": log_type,
        "source_system": source,
        "severity": severity,
        "message": message,
        "structured_metadata": metadata,
    }


def _generate_incident_row(rng: random.Random, flight: Flight) -> dict:
    base = flight.scheduled_departure
    incident_time = base + timedelta(minutes=rng.randint(-10, 8 * 60))
    severity = _weighted_choice(
        rng, ("low", "warning", "critical"), (50, 35, 15)
    )
    category = rng.choice(INCIDENT_CATEGORIES)
    description = (
        f"{category.title()} event reported on flight {flight.flight_number} "
        f"({flight.origin}->{flight.destination}). "
        f"{rng.choice(LOG_MESSAGE_POOL)}"
    )
    resolution = rng.choice(RESOLUTION_STATUSES)
    return {
        "flight_id": flight.id,
        "incident_time": incident_time,
        "severity": severity,
        "category": category,
        "description": description,
        "resolution_status": resolution,
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def has_existing_data(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(Flight)) > 0


def seed_database(session: Session, *, force: bool = False, seed: int = 1337) -> dict:
    """Seed the database with realistic flight operations data.

    Returns a dict with insert counts. Idempotent unless ``force=True``.
    """

    settings = get_settings()

    if not force and has_existing_data(session):
        logger.info("Seed skipped: flights table is not empty.")
        return {"flights": 0, "flight_logs": 0, "incidents": 0, "skipped": True}

    rng = random.Random(seed)
    now = datetime.now(tz=timezone.utc)

    # ---- flights ---------------------------------------------------------
    flight_count = max(0, settings.seed_flight_count)
    log_count = max(0, settings.seed_log_count)
    incident_count = max(0, settings.seed_incident_count)

    logger.info(
        "Seeding %d flights, %d logs, %d incidents…",
        flight_count, log_count, incident_count,
    )

    flight_rows = (_generate_flight_row(rng, now) for _ in range(flight_count))
    inserted_flights = _bulk_insert(session, Flight, flight_rows)

    # Re-load flight identifiers (just id + scheduled_departure + flight_number
    # + origin/destination for log/incident generation context).
    flights: list[Flight] = list(
        session.scalars(select(Flight)).all()
    )
    if not flights:
        logger.warning("No flights present after insert; aborting log/incident seed.")
        return {
            "flights": inserted_flights,
            "flight_logs": 0,
            "incidents": 0,
            "skipped": False,
        }

    # ---- flight_logs -----------------------------------------------------
    def _log_iter():
        for _ in range(log_count):
            flight = rng.choice(flights)
            yield _generate_log_row(rng, flight)

    inserted_logs = _bulk_insert(session, FlightLog, _log_iter())

    # ---- incidents -------------------------------------------------------
    def _incident_iter():
        for _ in range(incident_count):
            flight = rng.choice(flights)
            yield _generate_incident_row(rng, flight)

    inserted_incidents = _bulk_insert(session, Incident, _incident_iter())

    logger.info(
        "Seed complete: flights=%d logs=%d incidents=%d",
        inserted_flights, inserted_logs, inserted_incidents,
    )

    return {
        "flights": inserted_flights,
        "flight_logs": inserted_logs,
        "incidents": inserted_incidents,
        "skipped": False,
    }

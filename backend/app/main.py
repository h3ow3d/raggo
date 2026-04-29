"""FastAPI application entrypoint for rag-flight-lab (Phase 1).

Phase 1 responsibilities:
- expose `GET /health`
- expose `GET /stats` (basic counts, useful sanity check)
- on startup: wait for the database, ensure schema exists, and seed data
  if the database is empty.

Later phases will add ingestion, vector search, and the agent endpoints.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import engine, session_scope
from .models import FlightLog, Flight, Incident
from .schemas import HealthResponse, StatsResponse
from .seed import seed_database

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _wait_for_database(max_attempts: int = 60, delay_seconds: float = 1.0) -> None:
    """Block until PostgreSQL accepts connections.

    The database container needs a moment to become ready on first start;
    `init.sql` also runs there before the backend can connect.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database is reachable (attempt %d).", attempt)
            return
        except OperationalError as exc:
            last_error = exc
            logger.info("Database not ready yet (attempt %d/%d)…", attempt, max_attempts)
            time.sleep(delay_seconds)
    raise RuntimeError(f"Database did not become ready in time: {last_error}")


def _ensure_pgvector_extension() -> None:
    """Make sure the pgvector extension exists.

    `db/init.sql` already enables it for new clusters; this is defensive in
    case the backend points at an existing database.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _validate_embedding_dim() -> None:
    """Fail fast if `EMBEDDING_DIM` does not match the database schema.

    `db/init.sql` declares `flight_logs.embedding` as `vector(384)`. If the
    operator changes `EMBEDDING_DIM`, the ORM's `Vector(...)` type and the
    actual column dimension will disagree and inserts will fail at runtime
    with a confusing pgvector error. Raise a clear startup error instead.
    """
    settings = get_settings()
    with engine.connect() as conn:
        # `atttypmod` for a pgvector column encodes the declared dimension.
        row = conn.execute(
            text(
                """
                SELECT a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'flight_logs'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """
            )
        ).first()
    if row is None:
        # Schema not yet present — init.sql hasn't run. Nothing to validate.
        return
    column_dim = int(row[0])
    if column_dim <= 0:
        # Unknown / not constrained; skip validation rather than guess.
        return
    if column_dim != settings.embedding_dim:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} does not match the "
            f"flight_logs.embedding column dimension ({column_dim}) created by "
            f"db/init.sql. For Phase 1 the schema is fixed at 384 dimensions; "
            f"either set EMBEDDING_DIM=384 or recreate the database with a "
            f"matching schema (docker compose down -v)."
        )


def _run_startup_seed() -> None:
    with session_scope() as session:
        result = seed_database(session)
    if result.get("skipped"):
        logger.info("Startup seed skipped (data already present).")
    else:
        logger.info("Startup seed inserted: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _wait_for_database()
    _ensure_pgvector_extension()
    _validate_embedding_dim()
    _run_startup_seed()
    yield


app = FastAPI(title="rag-flight-lab", version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe and basic database connectivity check."""
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Health check DB ping failed: %s", exc)
        db_status = "unavailable"
    return HealthResponse(status="ok", database=db_status)


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    with session_scope() as session:
        flights = session.scalar(select(func.count()).select_from(Flight)) or 0
        logs = session.scalar(select(func.count()).select_from(FlightLog)) or 0
        incidents = session.scalar(select(func.count()).select_from(Incident)) or 0
        embedded = (
            session.scalar(
                select(func.count())
                .select_from(FlightLog)
                .where(FlightLog.embedding.is_not(None))
            )
            or 0
        )
    return StatsResponse(
        flights=flights,
        flight_logs=logs,
        incidents=incidents,
        embedded_logs=embedded,
        unembedded_logs=logs - embedded,
    )

"""FastAPI application entrypoint for rag-flight-lab.

Phase 1 responsibilities:
- expose `GET /health`
- expose `GET /stats` (basic counts, useful sanity check)
- on startup: wait for the database, ensure schema exists, and seed data
  if the database is empty.

Phase 4 adds the embedding ingestion pipeline and pgvector similarity
search:
- `POST /ingest` runs a bounded ingestion pass.
- `POST /search/vector` performs similarity search with structured filters.
- A bounded startup ingestion task runs in the background so the API/UI
  is not blocked while the initial ~50k seed logs get embedded.

Later phases will add the agent endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import engine, session_scope
from .ingestion import ingest_unembedded_logs
from .models import FlightLog, Flight, Incident
from . import agent as agent_module
from .schemas import (
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueryEvidence,
    QueryRequest,
    QueryResponse,
    StatsResponse,
    VectorSearchRequest,
    VectorSearchResponse,
    VectorSearchResult,
)
from .seed import seed_database
from .vector_search import (
    VectorSearchDependencyError,
    VectorSearchError,
    search_logs,
)

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
    # The startup work is intentionally blocking (sync SQLAlchemy + a sleep
    # retry loop while the database container becomes ready). Run it in a
    # worker thread so we don't block the asyncio event loop during boot.
    def _startup() -> None:
        _wait_for_database()
        _ensure_pgvector_extension()
        _validate_embedding_dim()
        _run_startup_seed()

    await anyio.to_thread.run_sync(_startup)

    # Kick off a bounded ingestion pass in the background. Running it as a
    # background task (rather than awaiting) ensures `docker compose up`
    # reaches a healthy `/health` quickly even when 50k seed logs still
    # need embeddings. The pass is naturally bounded by
    # `STARTUP_INGEST_LIMIT`; a threading.Event lets us request a clean
    # cooperative stop on shutdown without waiting for the worker thread
    # to finish on its own (anyio cancellation does not interrupt threads).
    stop_event = threading.Event()
    ingest_task = asyncio.create_task(_run_startup_ingestion(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if not ingest_task.done():
            try:
                # Give the worker a brief grace period to observe the flag
                # between batches, then move on regardless.
                await asyncio.wait_for(asyncio.shield(ingest_task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # pragma: no cover - best-effort
                pass


async def _run_startup_ingestion(stop_event: threading.Event) -> None:
    settings = get_settings()
    if settings.startup_ingest_limit <= 0:
        logger.info("Startup ingestion disabled (STARTUP_INGEST_LIMIT<=0).")
        return

    def _do_ingest() -> dict:
        result = ingest_unembedded_logs(
            limit=settings.startup_ingest_limit,
            should_stop=stop_event.is_set,
        )
        return result.to_dict()

    logger.info(
        "Starting bounded startup ingestion (limit=%d, batch_size=%d)…",
        settings.startup_ingest_limit,
        settings.ingest_batch_size,
    )
    try:
        result = await anyio.to_thread.run_sync(_do_ingest, abandon_on_cancel=True)
    except Exception as exc:  # pragma: no cover - defensive, logs and exits
        logger.warning("Startup ingestion failed: %s", exc)
        return
    logger.info("Startup ingestion finished: %s", result)


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


@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest | None = None) -> IngestResponse:
    """Run a bounded ingestion pass over logs without embeddings."""
    payload = request or IngestRequest()

    def _run() -> dict:
        result = ingest_unembedded_logs(
            limit=payload.limit,
            batch_size=payload.batch_size,
        )
        return result.to_dict()

    result = await anyio.to_thread.run_sync(_run)
    return IngestResponse(**result)


@app.post("/search/vector", response_model=VectorSearchResponse)
async def search_vector(request: VectorSearchRequest) -> VectorSearchResponse:
    """Embed `query` and return the most similar flight logs."""

    def _run() -> list[dict]:
        with session_scope() as session:
            return search_logs(
                session=session,
                query_text=request.query,
                top_k=request.top_k,
                filters=request.filters,
            )

    try:
        rows = await anyio.to_thread.run_sync(_run)
    except VectorSearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VectorSearchDependencyError as exc:
        # Upstream embedding service unavailable / failing — surface as a
        # 503 so callers and monitoring treat it as a transient server-side
        # condition rather than a bad request.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VectorSearchResponse(
        query=request.query,
        top_k=request.top_k,
        results=[VectorSearchResult(**row) for row in rows],
    )


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Run the basic RAG agent for a single question.

    The agent classifies intent deterministically, selects safe SQL
    tools and/or vector search, retrieves evidence, and asks the local
    generation model for a grounded answer. The model never executes
    arbitrary SQL — only allowlisted, parameterised tools in
    :mod:`app.safe_sql_tools` are used.
    """

    def _run() -> agent_module.AgentResult:
        with session_scope() as session:
            return agent_module.run(
                session=session,
                question=request.question,
                top_k=request.top_k,
            )

    try:
        result = await anyio.to_thread.run_sync(_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return QueryResponse(
        answer=result.answer,
        evidence=[QueryEvidence(**item) for item in result.evidence],
        agent_trace=result.agent_trace,
    )

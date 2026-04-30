"""FastAPI application entrypoint for raggo.

Generic domain-agnostic API server with pluggable DomainPacks.

Responsibilities:
- `GET /health` — liveness probe
- `GET /stats` — dashboard stats from current domain
- `GET /domain` — domain metadata and available resources/tools
- `POST /ingest` — run bounded ingestion pass for embeddable resources
- `POST /search/vector` — vector similarity search over a domain resource
- `POST /query` — RAG agent with safe SQL, vector search, and generation

On startup:
- Wait for the database
- Load the domain specified by RAGGO_DOMAIN
- Run domain init.sql if needed
- Seed domain data if empty
- Start background ingestion for embeddable resources
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Dict, List

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.exc import OperationalError

from app.core.agent import orchestrator
from app.core.config import get_settings
from app.core.database import engine, session_scope
from app.core.domain import DomainPack, load_domain
from app.core.ingestion import embed_item_by_id, ingest_all_for_domain, ingest_unembedded
from app.core.schemas import (
    CreateLogRequest,
    CreateLogResponse,
    FlightListResponse,
    FlightSummary,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    StatsResponse,
    VectorSearchRequest,
    VectorSearchResponse,
)
from app.core.vector_search import VectorSearchDependencyError, VectorSearchError, search

# Flight-specific endpoints (`/flights`, `POST /logs`) carried over from
# Phase 6 still depend on the flights ORM models. They only work when
# `RAGGO_DOMAIN=flights` and are guarded at request time.
from app.domains.flights.models import Flight, FlightLog

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _wait_for_database(max_attempts: int = 60, delay_seconds: float = 1.0) -> None:
    """Block until PostgreSQL accepts connections."""
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
    """Make sure the pgvector extension exists."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def _run_init_sql_if_needed(domain: DomainPack) -> None:
    """Run domain init.sql if the domain's first table doesn't exist."""
    if not domain.embeddable_resources:
        logger.info("No embeddable resources in domain; skipping init.sql check.")
        return

    # Check first resource's table
    first_resource = domain.embeddable_resources[0]
    table_name = first_resource.model.__tablename__

    if _table_exists(table_name):
        logger.info("Domain table '%s' exists; skipping init.sql.", table_name)
        return

    from pathlib import Path as _Path

    init_sql_path = _Path(domain.init_sql_path) if domain.init_sql_path else None
    if init_sql_path is None or not init_sql_path.exists():
        logger.warning("Domain init.sql not found at %s", domain.init_sql_path)
        return

    logger.info("Running domain init.sql from %s…", init_sql_path)
    sql_content = init_sql_path.read_text()

    # Execute the whole file in one go. Splitting on ';' is unsafe because
    # PL/pgSQL DO blocks (used for conditional index creation) contain
    # semicolons inside `$$ ... $$` quoted bodies. psycopg supports
    # multi-statement scripts via `exec_driver_sql`.
    with engine.begin() as conn:
        conn.exec_driver_sql(sql_content)

    logger.info("Domain init.sql executed successfully.")


def _run_startup_seed(domain: DomainPack) -> None:
    """Seed domain data if the domain has no existing data."""
    with session_scope() as session:
        result = domain.seed(session)
    if result.get("skipped"):
        logger.info("Startup seed skipped (data already present).")
    else:
        logger.info("Startup seed inserted: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load domain on startup
    settings = get_settings()
    domain_name = settings.raggo_domain

    def _startup() -> DomainPack:
        _wait_for_database()
        _ensure_pgvector_extension()

        # Load domain
        logger.info("Loading domain: %s", domain_name)
        domain = load_domain(domain_name)
        logger.info(
            "Domain loaded: %s v%s — %s",
            domain.display.title,
            domain.display.version,
            domain.display.description,
        )

        # Initialize domain schema and seed
        _run_init_sql_if_needed(domain)
        _run_startup_seed(domain)

        return domain

    domain = await anyio.to_thread.run_sync(_startup)

    # Store domain on app.state for request handlers
    app.state.domain = domain

    # Kick off background ingestion
    stop_event = threading.Event()
    ingest_task = asyncio.create_task(_run_startup_ingestion(domain, stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if not ingest_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(ingest_task), timeout=5.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass


async def _run_startup_ingestion(domain: DomainPack, stop_event: threading.Event) -> None:
    """Run bounded ingestion for all domain embeddable resources."""
    settings = get_settings()
    if settings.startup_ingest_limit <= 0:
        logger.info("Startup ingestion disabled (STARTUP_INGEST_LIMIT<=0).")
        return

    def _do_ingest() -> Dict[str, Any]:
        return ingest_all_for_domain(
            domain=domain,
            limit=settings.startup_ingest_limit,
            batch_size=settings.ingest_batch_size,
            should_stop=stop_event.is_set,
        )

    logger.info(
        "Starting bounded startup ingestion (limit=%d, batch_size=%d)…",
        settings.startup_ingest_limit,
        settings.ingest_batch_size,
    )
    try:
        result = await anyio.to_thread.run_sync(_do_ingest, abandon_on_cancel=True)
    except Exception as exc:
        logger.warning("Startup ingestion failed: %s", exc)
        return
    logger.info("Startup ingestion finished: %s", result)


app = FastAPI(title="raggo", version="0.2.0", lifespan=lifespan)

# CORS is allowlist-based to avoid letting arbitrary websites read
# responses from a backend bound to localhost. The bundled production
# frontend talks to the backend same-origin via nginx (`/api/*`), so it
# does not actually need CORS; the allowlist exists for `npm run dev`
# and for direct access to the dev backend port. The list can be
# overridden via `CORS_ALLOW_ORIGINS` (comma-separated). An empty value
# disables cross-origin access entirely.
_cors_origins = get_settings().cors_allow_origin_list
if _cors_origins:
    # The bundled production frontend talks to the backend same-origin
    # via nginx (`/api/*`) and does not need CORS; the allowlist exists
    # for `npm run dev` and direct access to the dev backend port. The
    # list can be overridden via `CORS_ALLOW_ORIGINS` (comma-separated).
    # An empty value disables cross-origin access entirely. The
    # explicit "*" opt-in is honoured but discouraged: a backend bound
    # to localhost should not let arbitrary websites read its responses.
    _cors_kwargs: dict[str, object] = {
        "allow_credentials": False,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if "*" in _cors_origins:
        _cors_kwargs["allow_origins"] = ["*"]
    else:
        _cors_kwargs["allow_origins"] = _cors_origins
    app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe and basic database connectivity check."""
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check DB ping failed: %s", exc)
        db_status = "unavailable"
    return HealthResponse(status="ok", database=db_status)


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    """Return dashboard stats from the current domain."""
    domain: DomainPack = app.state.domain
    with session_scope() as session:
        domain_stats = domain.stats(session)
    return StatsResponse(**domain_stats)


@app.get("/domain")
def domain_info() -> Dict[str, Any]:
    """Return metadata about the current domain."""
    domain: DomainPack = app.state.domain
    return {
        "name": domain.name,
        "display": {
            "title": domain.display.title,
            "description": domain.display.description,
            "version": domain.display.version,
        },
        "resources": [r.name for r in domain.embeddable_resources],
        "sql_tools": [t.name for t in domain.sql_tools],
    }


@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest | None = None) -> IngestResponse:
    """Run a bounded ingestion pass over domain embeddable resources.

    When `request.resource` is supplied, ingestion is scoped to just that
    resource. Otherwise every embeddable resource in the active domain is
    processed (per-resource limit).
    """
    domain: DomainPack = app.state.domain
    payload = request or IngestRequest()

    selected_resource = None
    if payload.resource:
        for r in domain.embeddable_resources:
            if r.name == payload.resource:
                selected_resource = r
                break
        if selected_resource is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown resource: {payload.resource}. Available: "
                    f"{[r.name for r in domain.embeddable_resources]}"
                ),
            )

    def _run() -> Dict[str, Any]:
        if selected_resource is not None:
            res = ingest_unembedded(
                resource=selected_resource,
                limit=payload.limit,
                batch_size=payload.batch_size,
            )
        else:
            res = ingest_all_for_domain(
                domain=domain,
                limit=payload.limit,
                batch_size=payload.batch_size,
            )
        return res.to_dict()

    result = await anyio.to_thread.run_sync(_run)
    return IngestResponse(**result)


@app.post("/search/vector", response_model=VectorSearchResponse)
async def search_vector(request: VectorSearchRequest) -> VectorSearchResponse:
    """Embed query and return most similar items from specified resource."""
    domain: DomainPack = app.state.domain

    # Find resource by name (default to first resource if not specified)
    resource = None
    resource_name = request.resource or (
        domain.embeddable_resources[0].name if domain.embeddable_resources else None
    )

    if resource_name is None:
        raise HTTPException(
            status_code=400,
            detail="No embeddable resources available in domain.",
        )

    for r in domain.embeddable_resources:
        if r.name == resource_name:
            resource = r
            break

    if resource is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown resource: {resource_name}. Available: {[r.name for r in domain.embeddable_resources]}",
        )

    def _run() -> List[Dict[str, Any]]:
        with session_scope() as session:
            return search(
                session=session,
                resource=resource,
                query_text=request.query,
                top_k=request.top_k,
                filters=request.filters,
            )

    try:
        rows = await anyio.to_thread.run_sync(_run)
    except VectorSearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VectorSearchDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VectorSearchResponse(
        query=request.query,
        resource=resource_name,
        top_k=request.top_k,
        results=rows,
    )


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Run the RAG agent on a question using the current domain.

    The agent classifies intent, selects safe SQL tools and/or vector
    search, retrieves evidence, and asks the local generation model for
    a grounded answer. The model never executes arbitrary SQL — only
    allowlisted, parameterised tools are used.
    """
    domain: DomainPack = app.state.domain

    def _run() -> orchestrator.AgentResult:
        with session_scope() as session:
            return orchestrator.run(
                session=session,
                domain=domain,
                question=request.question,
                top_k=request.top_k,
            )

    try:
        result = await anyio.to_thread.run_sync(_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return QueryResponse(
        answer=result.answer,
        evidence=result.evidence,
        agent_trace=result.agent_trace,
    )


# --- Phase 6: flight selector + log creation -------------------------------


_VALID_LOG_SEVERITIES = {"info", "warning", "critical"}


@app.get("/flights", response_model=FlightListResponse)
def list_flights(
    search: str | None = Query(
        default=None,
        max_length=64,
        description=(
            "Optional case-insensitive substring match on flight number, "
            "origin, or destination. Whitespace-only values are ignored."
        ),
    ),
    limit: int = Query(default=25, ge=1, le=100),
) -> FlightListResponse:
    """Lightweight flight listing used by the frontend Add Flight Log selector.

    Flights-domain only: returns 404 when a different domain is active.
    """
    domain: DomainPack = app.state.domain
    if domain.name != "flights":
        raise HTTPException(
            status_code=404,
            detail="/flights is only available when RAGGO_DOMAIN=flights",
        )
    term = (search or "").strip()
    with session_scope() as session:
        stmt = select(Flight).order_by(Flight.scheduled_departure.desc())
        if term:
            pattern = f"%{term}%"
            stmt = stmt.where(
                or_(
                    Flight.flight_number.ilike(pattern),
                    Flight.origin.ilike(pattern),
                    Flight.destination.ilike(pattern),
                )
            )
        stmt = stmt.limit(limit)
        rows = list(session.scalars(stmt))
        return FlightListResponse(
            results=[
                FlightSummary(
                    id=f.id,
                    flight_number=f.flight_number,
                    airline=f.airline,
                    origin=f.origin,
                    destination=f.destination,
                    scheduled_departure=f.scheduled_departure,
                    status=f.status,
                )
                for f in rows
            ]
        )


@app.post("/logs", response_model=CreateLogResponse, status_code=201)
async def create_log(request: CreateLogRequest) -> CreateLogResponse:
    """Create a new flight log and trigger embedding for that single row.

    Flights-domain only: returns 404 when a different domain is active so
    callers don't accidentally insert into a non-existent table.
    """
    domain: DomainPack = app.state.domain
    if domain.name != "flights":
        raise HTTPException(
            status_code=404,
            detail="/logs is only available when RAGGO_DOMAIN=flights",
        )
    # The Pydantic validator on CreateLogRequest already trims and
    # requires non-empty values for the string fields.
    severity = request.severity.lower()
    if severity not in _VALID_LOG_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"severity must be one of {sorted(_VALID_LOG_SEVERITIES)}",
        )

    log_time = request.log_time or datetime.now(UTC)

    def _create() -> int:
        with session_scope() as session:
            flight = session.get(Flight, request.flight_id)
            if flight is None:
                raise ValueError(f"flight {request.flight_id} not found")
            log = FlightLog(
                flight_id=request.flight_id,
                log_time=log_time,
                log_type=request.log_type,
                source_system=request.source_system,
                severity=severity,
                message=request.message,
                structured_metadata=request.metadata or {},
            )
            session.add(log)
            session.flush()
            return int(log.id)

    try:
        log_id = await anyio.to_thread.run_sync(_create)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Best-effort single-row ingestion. If the embedding service is
    # unavailable the log is still persisted and `embedding_error` is
    # populated so the frontend can surface it; the next ingestion pass
    # (startup or `POST /ingest`) will retry the embedding.
    embedded = False
    embedding_error: str | None = None
    try:
        # Resolve the `flight_logs` embeddable resource by name rather
        # than positionally, so reordering domain resources can't break
        # this single-row ingestion path.
        resource = next(
            (r for r in domain.embeddable_resources if r.name == "flight_logs"),
            None,
        )
        if resource is not None:
            embedded, embedding_error = await anyio.to_thread.run_sync(
                embed_item_by_id, resource, log_id
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Single-row ingestion for log %s failed: %s", log_id, exc)
        embedding_error = str(exc)

    return CreateLogResponse(
        id=log_id,
        flight_id=request.flight_id,
        log_time=log_time,
        log_type=request.log_type,
        source_system=request.source_system,
        severity=severity,
        message=request.message,
        embedded=embedded,
        embedding_error=embedding_error,
    )

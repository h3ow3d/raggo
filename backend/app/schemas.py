"""Minimal Pydantic schemas exposed by the Phase 1 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    database: str


class StatsResponse(BaseModel):
    flights: int
    flight_logs: int
    incidents: int
    embedded_logs: int
    unembedded_logs: int


# --- Phase 4: ingestion -----------------------------------------------------


class IngestRequest(BaseModel):
    """Operator-triggered ingestion request body."""

    limit: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Maximum number of unembedded logs to process in this run. "
            "Defaults to STARTUP_INGEST_LIMIT when omitted."
        ),
    )
    batch_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=128,
        description=(
            "Override INGEST_BATCH_SIZE for this run. Capped at 128 to stay "
            "within the embedding service's default MAX_BATCH."
        ),
    )


class IngestResponse(BaseModel):
    scanned: int
    embedded: int
    errors: List[str] = Field(default_factory=list)


# --- Phase 4: vector search -------------------------------------------------


class VectorSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Free-text search query.")
    top_k: int = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional structured filters. Allowed keys: severity, "
            "source_system, log_type, flight_id."
        ),
    )


class VectorSearchResult(BaseModel):
    log_id: int
    flight_id: int
    flight_number: str
    origin: str
    destination: str
    log_time: datetime
    log_type: str
    source_system: str
    severity: str
    message: str
    similarity: Optional[float]
    distance: Optional[float]


class VectorSearchResponse(BaseModel):
    query: str
    top_k: int
    results: List[VectorSearchResult]


# --- Phase 5: agent / query -------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User question.")
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Optional cap on vector-search results. Capped by "
            "AGENT_MAX_VECTOR_RESULTS regardless of value."
        ),
    )


class QueryEvidence(BaseModel):
    """Loosely typed evidence item.

    The agent emits several evidence shapes (flight logs, incidents,
    flights, airport delay counts). Rather than a noisy union we expose
    a permissive schema and rely on the ``type`` field for routing.
    """

    type: str
    id: Optional[Any] = None
    message: Optional[str] = None

    model_config = {"extra": "allow"}


class QueryResponse(BaseModel):
    answer: str
    evidence: List[QueryEvidence]
    agent_trace: Dict[str, Any]


# --- Phase 6: flights & logs (frontend support) -----------------------------


class FlightSummary(BaseModel):
    """Minimal flight payload used by the frontend flight selector."""

    id: int
    flight_number: str
    airline: str
    origin: str
    destination: str
    scheduled_departure: datetime
    status: str


class FlightListResponse(BaseModel):
    results: List[FlightSummary]


class CreateLogRequest(BaseModel):
    """Body for `POST /logs`.

    `log_time` is optional; it defaults to "now" on the server when not
    provided. `metadata` is a free-form JSON object stored in the
    `structured_metadata` column.
    """

    flight_id: int = Field(..., ge=1)
    log_type: str = Field(..., min_length=1, max_length=64)
    source_system: str = Field(..., min_length=1, max_length=64)
    severity: str = Field(..., min_length=1, max_length=32)
    message: str = Field(..., min_length=1)
    log_time: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class CreateLogResponse(BaseModel):
    id: int
    flight_id: int
    log_time: datetime
    log_type: str
    source_system: str
    severity: str
    message: str
    embedded: bool
    embedding_error: Optional[str] = None


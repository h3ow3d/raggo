"""Generic Pydantic schemas exposed by the raggo API.

These schemas are domain-agnostic. Domain-specific fields flow through
permissive containers (QueryEvidence with extra="allow", VectorHit metadata dict).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    database: str


class StatsResponse(BaseModel):
    """Generic stats response. Domain determines the fields."""

    model_config = {"extra": "allow"}


# --- Ingestion -------------------------------------------------------------


class IngestRequest(BaseModel):
    """Operator-triggered ingestion request body."""

    limit: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Maximum number of unembedded items to process in this run. "
            "Defaults to STARTUP_INGEST_LIMIT when omitted."
        ),
    )
    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=128,
        description=(
            "Override INGEST_BATCH_SIZE for this run. Capped at 128 to stay "
            "within the embedding service's default MAX_BATCH."
        ),
    )
    resource: str | None = Field(
        default=None,
        description="Optional resource name. Defaults to all embeddable resources.",
    )


class IngestResponse(BaseModel):
    scanned: int
    embedded: int
    errors: List[str] = Field(default_factory=list)


# --- Vector search -------------------------------------------------


class VectorSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Free-text search query.")
    top_k: int = Field(default=10, ge=1, le=100)
    resource: str | None = Field(
        default=None,
        description="Resource name to search. Defaults to first embeddable resource.",
    )
    filters: Dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional structured filters. Allowed keys depend on the resource's FilterSpec."
        ),
    )


class VectorHit(BaseModel):
    """A single vector search result.

    Generic shape: id, score, distance, text, and a metadata dict
    containing domain-specific fields projected by the resource's
    evidence_projection callable.
    """

    id: int
    score: float | None = Field(
        default=None,
        description="Cosine similarity in [-1, 1], higher is better.",
    )
    distance: float | None = Field(
        default=None,
        description="Cosine distance in [0, 2], lower is better.",
    )
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VectorSearchResponse(BaseModel):
    query: str
    resource: str
    top_k: int
    results: List[VectorHit]


# --- Agent / query -------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User question.")
    top_k: int | None = Field(
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

    The agent emits several evidence shapes depending on the domain.
    Rather than a noisy union we expose a permissive schema and rely
    on the ``type`` field for routing.
    """

    type: str
    id: Any | None = None
    message: str | None = None

    model_config = {"extra": "allow"}


class QueryResponse(BaseModel):
    answer: str
    evidence: List[QueryEvidence]
    agent_trace: Dict[str, Any]


# --- Phase 6: flights & logs (frontend support) -----------------------------
# NOTE: These are flight-domain-specific shapes carried over from the pre-
# refactor codebase to keep the existing frontend working while the domain
# abstraction stabilises. A future phase will move them into a domain-
# specific endpoint surface so `core/schemas.py` is fully domain-agnostic.


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
    log_time: datetime | None = None
    metadata: Dict[str, Any] | None = None

    @field_validator("log_type", "source_system", "severity", "message")
    @classmethod
    def _strip_and_require_non_empty(cls, value: str) -> str:
        # `min_length=1` only checks the raw value; whitespace-only inputs
        # would otherwise pass validation and be persisted as empty
        # strings after stripping. Trim once here and reject if nothing
        # is left.
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty or whitespace-only")
        return stripped


class CreateLogResponse(BaseModel):
    id: int
    flight_id: int
    log_time: datetime
    log_type: str
    source_system: str
    severity: str
    message: str
    embedded: bool
    embedding_error: str | None = None

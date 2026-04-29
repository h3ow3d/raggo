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


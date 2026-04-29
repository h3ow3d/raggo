"""Generic Pydantic schemas exposed by the raggo API.

These schemas are domain-agnostic. Domain-specific fields flow through
permissive containers (QueryEvidence with extra="allow", VectorHit metadata dict).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    database: str


class StatsResponse(BaseModel):
    """Generic stats response. Domain determines the fields."""
    model_config = {"extra": "allow"}


# --- Ingestion -------------------------------------------------------------


class IngestRequest(BaseModel):
    """Operator-triggered ingestion request body."""

    limit: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Maximum number of unembedded items to process in this run. "
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
    resource: Optional[str] = Field(
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
    resource: Optional[str] = Field(
        default=None,
        description="Resource name to search. Defaults to first embeddable resource.",
    )
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional structured filters. Allowed keys depend on the "
            "resource's FilterSpec."
        ),
    )


class VectorHit(BaseModel):
    """A single vector search result.
    
    Generic shape: id, score, distance, text, and a metadata dict
    containing domain-specific fields projected by the resource's
    evidence_projection callable.
    """
    id: int
    score: Optional[float] = Field(
        default=None,
        description="Cosine similarity in [-1, 1], higher is better.",
    )
    distance: Optional[float] = Field(
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

    The agent emits several evidence shapes depending on the domain.
    Rather than a noisy union we expose a permissive schema and rely
    on the ``type`` field for routing.
    """

    type: str
    id: Optional[Any] = None
    message: Optional[str] = None

    model_config = {"extra": "allow"}


class QueryResponse(BaseModel):
    answer: str
    evidence: List[QueryEvidence]
    agent_trace: Dict[str, Any]

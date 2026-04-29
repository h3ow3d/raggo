"""pgvector-backed similarity search over `flight_logs`.

The query text is embedded via the embedding model service, then the
resulting vector is compared to stored log embeddings using pgvector's
cosine distance operator (`<=>`). The endpoint also supports simple
structured filters (severity, source_system, log_type, flight_id) so
callers can narrow the search to relevant slices.

Cosine distance is in [0, 2]; we expose a more intuitive cosine
similarity score in [-1, 1] (`1 - distance`) where higher is better.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from .config import get_settings
from .model_clients import EmbeddingClient, EmbeddingServiceError
from .models import Flight, FlightLog

logger = logging.getLogger(__name__)


# Whitelist of filterable columns. Anything not in this map is rejected
# rather than silently ignored, so callers spot typos early.
_ALLOWED_FILTERS: Dict[str, Any] = {
    "severity": FlightLog.severity,
    "source_system": FlightLog.source_system,
    "log_type": FlightLog.log_type,
    "flight_id": FlightLog.flight_id,
}


class VectorSearchError(ValueError):
    """Raised for client-correctable vector search failures.

    These are validation/usage problems (empty query, bad filter key,
    non-positive top_k, dim mismatch with the configured schema) and
    should map to HTTP 4xx. Upstream dependency failures are surfaced
    separately as :class:`VectorSearchDependencyError`.
    """


class VectorSearchDependencyError(RuntimeError):
    """Raised when an upstream dependency (embedding service) fails.

    These are server-side/transient conditions and should map to HTTP
    5xx (typically 502/503), not 400.
    """


def _build_filter_clauses(filters: Optional[Dict[str, Any]]):
    """Translate the filter dict into SQLAlchemy WHERE clauses."""
    clauses = []
    if not filters:
        return clauses
    for key, value in filters.items():
        if value is None:
            continue
        column = _ALLOWED_FILTERS.get(key)
        if column is None:
            raise VectorSearchError(f"unsupported filter: {key}")
        clauses.append(column == value)
    return clauses


def search_logs(
    session: Session,
    query_text: str,
    top_k: int = 10,
    filters: Optional[Dict[str, Any]] = None,
    client: Optional[EmbeddingClient] = None,
) -> List[Dict[str, Any]]:
    """Return the `top_k` most similar embedded logs for `query_text`.

    Each result is a dict containing the log id and message, the joined
    flight metadata (id, flight number, origin, destination), the log
    timestamp/severity, and a `similarity` score in [-1, 1].
    """
    if not query_text or not query_text.strip():
        raise VectorSearchError("query must not be empty")
    if top_k <= 0:
        raise VectorSearchError("top_k must be positive")

    settings = get_settings()
    # Cap top_k defensively so a caller can't request a huge scan.
    effective_top_k = min(top_k, 100)

    owns_client = client is None
    embedding_client = client or EmbeddingClient()
    try:
        try:
            embed_result = embedding_client.embed([query_text])
        except EmbeddingServiceError as exc:
            raise VectorSearchDependencyError(
                f"failed to embed query: {exc}"
            ) from exc
    finally:
        if owns_client:
            embedding_client.close()

    if embed_result.dim != settings.embedding_dim:
        raise VectorSearchError(
            f"embedding dim mismatch: service returned {embed_result.dim}, "
            f"expected {settings.embedding_dim}"
        )

    query_vector = embed_result.embeddings[0]

    # Cosine distance via pgvector's `<=>` operator (registered on the
    # SQLAlchemy `Vector` column type). Lower distance = more similar.
    distance = FlightLog.embedding.cosine_distance(query_vector).label("distance")

    f = aliased(Flight)
    stmt = (
        select(
            FlightLog.id,
            FlightLog.flight_id,
            FlightLog.log_time,
            FlightLog.log_type,
            FlightLog.source_system,
            FlightLog.severity,
            FlightLog.message,
            f.flight_number,
            f.origin,
            f.destination,
            distance,
        )
        .join(f, f.id == FlightLog.flight_id)
        .where(FlightLog.embedding.is_not(None))
    )

    for clause in _build_filter_clauses(filters):
        stmt = stmt.where(clause)

    stmt = stmt.order_by(distance.asc()).limit(effective_top_k)

    rows = session.execute(stmt).all()

    results: List[Dict[str, Any]] = []
    for row in rows:
        dist = float(row.distance) if row.distance is not None else None
        similarity = (1.0 - dist) if dist is not None else None
        results.append(
            {
                "log_id": row.id,
                "flight_id": row.flight_id,
                "flight_number": row.flight_number,
                "origin": row.origin,
                "destination": row.destination,
                "log_time": row.log_time,
                "log_type": row.log_type,
                "source_system": row.source_system,
                "severity": row.severity,
                "message": row.message,
                "similarity": similarity,
                "distance": dist,
            }
        )
    return results

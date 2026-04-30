"""Generic pgvector-backed similarity search.

Operates on any EmbeddableResource from a DomainPack. The query text
is embedded via the embedding model service, then compared to stored
embeddings using pgvector's cosine distance operator.

Returns a generic list of dicts with id, score, distance, text, and
metadata projected by the resource's evidence_projection callable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.domain import EmbeddableResource
from app.core.model_clients import EmbeddingClient, EmbeddingServiceError

logger = logging.getLogger(__name__)


class VectorSearchError(ValueError):
    """Raised for client-correctable vector search failures.

    These are validation/usage problems (empty query, bad filter key,
    non-positive top_k, dim mismatch) and should map to HTTP 4xx.
    Upstream dependency failures are surfaced separately as
    :class:`VectorSearchDependencyError`.
    """


class VectorSearchDependencyError(RuntimeError):
    """Raised when an upstream dependency (embedding service) fails.

    These are server-side/transient conditions and should map to HTTP
    5xx (typically 502/503), not 400.
    """


def _build_filter_clauses(resource: EmbeddableResource, filters: Dict[str, Any] | None):
    """Translate the filter dict into SQLAlchemy WHERE clauses."""
    clauses = []
    if not filters:
        return clauses
    for key, value in filters.items():
        if value is None:
            continue
        column_name = resource.filter_spec.columns.get(key)
        if column_name is None:
            raise VectorSearchError(
                f"unsupported filter: {key!r} for resource {resource.name!r}. "
                f"Allowed: {sorted(resource.filter_spec.columns.keys())}"
            )
        # Get the actual column from the ORM model
        column = getattr(resource.model, column_name, None)
        if column is None:
            raise VectorSearchError(
                f"filter column {column_name!r} not found on {resource.model.__name__}"
            )
        clauses.append(column == value)
    return clauses


def search(
    session: Session,
    *,
    resource: EmbeddableResource,
    query_text: str,
    top_k: int = 10,
    filters: Dict[str, Any] | None = None,
    client: EmbeddingClient | None = None,
) -> List[Dict[str, Any]]:
    """Return the `top_k` most similar embedded items for `query_text`.

    Each result is a dict with: {id, score, distance, text, metadata}.
    The metadata dict is built by the resource's evidence_projection callable.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session.
    resource : EmbeddableResource
        The resource to search.
    query_text : str
        Free-text query to embed and search.
    top_k : int
        Number of results to return.
    filters : dict, optional
        Structured filters validated against resource.filter_spec.
    client : EmbeddingClient, optional
        Injected for tests; created lazily otherwise.

    Returns
    -------
    list[dict]
        List of hits with {id, score, distance, text, metadata}.
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
            raise VectorSearchDependencyError(f"failed to embed query: {exc}") from exc
    finally:
        if owns_client:
            embedding_client.close()

    if embed_result.dim != settings.embedding_dim:
        raise VectorSearchError(
            f"embedding dim mismatch: service returned {embed_result.dim}, "
            f"expected {settings.embedding_dim}"
        )

    query_vector = embed_result.embeddings[0]

    # Build the query. Get the embedding column from the model.
    embedding_col = getattr(resource.model, resource.embedding_column, None)
    if embedding_col is None:
        raise VectorSearchError(
            f"embedding column {resource.embedding_column!r} not found on {resource.model.__name__}"
        )

    text_col = getattr(resource.model, resource.text_column, None)
    if text_col is None:
        raise VectorSearchError(
            f"text column {resource.text_column!r} not found on {resource.model.__name__}"
        )

    # Cosine distance via pgvector's `<=>` operator (registered on the
    # SQLAlchemy `Vector` column type). Lower distance = more similar.
    distance = embedding_col.cosine_distance(query_vector).label("distance")

    # Start with the resource's model and the distance
    stmt = select(
        resource.model,
        distance,
    ).where(embedding_col.is_not(None))

    # Apply joins if the resource specifies any. Joins are used to
    # filter/order rows; to avoid an N+1 lazy-load when
    # `evidence_projection` accesses related attributes (e.g.
    # `log.flight`, `tm.ticket`), eager-load any relationship from the
    # resource model that targets a joined model with `selectinload`.
    joined_models: List[type] = []
    for joined_model, on_clause_factory in resource.joins:
        on_clause = on_clause_factory(resource.model, joined_model)
        stmt = stmt.join(joined_model, on_clause)
        joined_models.append(joined_model)

    if joined_models:
        try:
            mapper = sa_inspect(resource.model)
            for rel in mapper.relationships:
                if rel.mapper.class_ in joined_models:
                    stmt = stmt.options(selectinload(getattr(resource.model, rel.key)))
        except Exception:  # pragma: no cover - defensive: fall back to lazy load
            logger.debug(
                "Could not configure eager loading for %s; "
                "evidence_projection may issue extra queries.",
                resource.model.__name__,
            )

    # Apply filters
    for clause in _build_filter_clauses(resource, filters):
        stmt = stmt.where(clause)

    stmt = stmt.order_by(distance.asc()).limit(effective_top_k)

    rows = session.execute(stmt).all()

    results: List[Dict[str, Any]] = []
    for row in rows:
        # row is a tuple: (resource_model_instance, distance_value, ...)
        # The first element is the main model instance
        item = row[0]
        dist = float(row.distance) if row.distance is not None else None
        similarity = (1.0 - dist) if dist is not None else None

        # Use evidence_projection to build metadata
        # For joins, we need to pass the joined instances too
        # The evidence_projection should handle the ORM object properly
        metadata = resource.evidence_projection(item)

        # Get the ID and text
        item_id = getattr(item, "id", None)
        text = getattr(item, resource.text_column, "")

        results.append(
            {
                "id": item_id,
                "score": similarity,
                "distance": dist,
                "text": text,
                "metadata": metadata,
            }
        )
    return results

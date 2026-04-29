"""Generic embedding ingestion pipeline.

Finds rows without an embedding in any EmbeddableResource, sends their
text to the embedding model service in batches, and writes the resulting
vectors back to PostgreSQL together with optional provenance columns.

The pipeline is intentionally simple and synchronous: it is called from
- the startup hook (with a small `STARTUP_INGEST_LIMIT` cap)
- the `POST /ingest` endpoint (operator-triggered top-up)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.domain import DomainPack, EmbeddableResource
from app.core.model_clients import EmbeddingClient, EmbeddingServiceError

logger = logging.getLogger(__name__)


class IngestionResult:
    """Outcome of an ingestion run."""

    __slots__ = ("scanned", "embedded", "errors")

    def __init__(self) -> None:
        self.scanned: int = 0
        self.embedded: int = 0
        self.errors: List[str] = []

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "embedded": self.embedded,
            "errors": list(self.errors),
        }


def _select_unembedded(
    session: Session, resource: EmbeddableResource, limit: int
) -> List[Any]:
    """Return up to `limit` items that have no embedding yet, oldest first."""
    if limit <= 0:
        return []
    embedding_col = getattr(resource.model, resource.embedding_column, None)
    if embedding_col is None:
        logger.error(
            "Resource %s model %s missing embedding column %s",
            resource.name,
            resource.model.__name__,
            resource.embedding_column,
        )
        return []
    
    stmt = (
        select(resource.model)
        .where(embedding_col.is_(None))
        .order_by(resource.model.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def ingest_unembedded(
    resource: EmbeddableResource,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
    client: Optional[EmbeddingClient] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> IngestionResult:
    """Embed up to `limit` items that currently have no embedding.

    Each batch is embedded via the embedding model service and committed
    in its own transaction so partial progress is preserved if a later
    batch fails.

    `should_stop`, if provided, is consulted between batches so callers
    (e.g. the FastAPI lifespan task on shutdown) can request cooperative
    cancellation without waiting for the configured `limit` to drain.
    """
    settings = get_settings()
    effective_limit = settings.startup_ingest_limit if limit is None else int(limit)
    effective_batch = batch_size if batch_size is not None else settings.ingest_batch_size

    result = IngestionResult()
    if effective_limit <= 0:
        return result
    if effective_batch <= 0:
        raise ValueError("batch_size must be positive")

    owns_client = client is None
    embedding_client = client or EmbeddingClient()
    try:
        remaining = effective_limit
        while remaining > 0:
            if should_stop is not None and should_stop():
                logger.info("Ingestion stopping early at caller request.")
                return result
            chunk_size = min(effective_batch, remaining)

            # New session per batch so each commit is independent and
            # long ingestion runs don't hold a single transaction open.
            with session_scope() as session:
                items = _select_unembedded(session, resource, chunk_size)
                if not items:
                    return result

                result.scanned += len(items)
                texts = [getattr(item, resource.text_column) for item in items]

                try:
                    embed_result = embedding_client.embed(texts)
                except EmbeddingServiceError as exc:
                    msg = f"embedding service error: {exc}"
                    logger.warning("Ingestion batch failed: %s", msg)
                    result.errors.append(msg)
                    # Stop the run on service errors — retrying without
                    # a backoff would only hammer the model container.
                    return result

                if embed_result.dim != settings.embedding_dim:
                    msg = (
                        f"embedding dim mismatch: service returned {embed_result.dim}, "
                        f"expected {settings.embedding_dim}"
                    )
                    logger.error("Ingestion aborted: %s", msg)
                    result.errors.append(msg)
                    return result

                now = datetime.now(timezone.utc)
                for item, vector in zip(items, embed_result.embeddings):
                    setattr(item, resource.embedding_column, vector)
                    if resource.embedding_model_column:
                        setattr(item, resource.embedding_model_column, embed_result.model)
                    if resource.embedding_dim_column:
                        setattr(item, resource.embedding_dim_column, embed_result.dim)
                    if resource.embedded_at_column:
                        setattr(item, resource.embedded_at_column, now)

                result.embedded += len(items)

            remaining -= len(items)
            if len(items) < chunk_size:
                # Nothing left to embed.
                break
    finally:
        if owns_client:
            embedding_client.close()

    return result


def ingest_all_for_domain(
    domain: DomainPack,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> IngestionResult:
    """Ingest all embeddable resources for a domain.
    
    Returns a combined IngestionResult across all resources.
    The limit is per-resource (each resource gets up to `limit` items embedded).
    """
    combined = IngestionResult()
    for resource in domain.embeddable_resources:
        if should_stop is not None and should_stop():
            logger.info("Ingestion stopping early (domain-wide).")
            break
        logger.info("Ingesting resource %s...", resource.name)
        res = ingest_unembedded(
            resource=resource,
            limit=limit,
            batch_size=batch_size,
            should_stop=should_stop,
        )
        combined.scanned += res.scanned
        combined.embedded += res.embedded
        combined.errors.extend(res.errors)
    return combined


def embed_item_by_id(
    resource: "EmbeddableResource",
    item_id: int,
    client: Optional[EmbeddingClient] = None,
) -> tuple[bool, Optional[str]]:
    """Embed a single item of `resource` by primary-key id.

    Returns a ``(embedded, error)`` tuple:

    * ``embedded`` is True when the item was (re)embedded.
    * ``error`` carries a human-readable reason when embedding was not
      performed, so callers (e.g. the ``POST /logs`` handler) can
      surface transient failures to the UI. ``error`` is ``None`` on
      success, and also ``None`` when the item id simply does not exist.

    Generic across embeddable resources — reads ``resource.text_column``
    and writes back to the embedding/provenance columns declared on the
    resource.
    """
    settings = get_settings()
    owns_client = client is None
    embedding_client = client or EmbeddingClient()
    try:
        with session_scope() as session:
            item = session.get(resource.model, item_id)
            if item is None:
                return False, None
            text_value = getattr(item, resource.text_column)
            try:
                embed_result = embedding_client.embed([text_value])
            except EmbeddingServiceError as exc:
                logger.warning("Failed to embed %s %s: %s", resource.name, item_id, exc)
                return False, f"embedding service unavailable: {exc}"
            if embed_result.dim != settings.embedding_dim:
                logger.error(
                    "Refusing to embed %s %s: dim %s != configured %s",
                    resource.name,
                    item_id,
                    embed_result.dim,
                    settings.embedding_dim,
                )
                return False, (
                    f"embedding dimension mismatch ({embed_result.dim} != "
                    f"{settings.embedding_dim})"
                )
            setattr(item, resource.embedding_column, embed_result.embeddings[0])
            if resource.embedding_model_column:
                setattr(item, resource.embedding_model_column, embed_result.model)
            if resource.embedding_dim_column:
                setattr(item, resource.embedding_dim_column, embed_result.dim)
            if resource.embedded_at_column:
                setattr(item, resource.embedded_at_column, datetime.now(timezone.utc))
            return True, None
    finally:
        if owns_client:
            embedding_client.close()

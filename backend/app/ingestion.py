"""Embedding ingestion pipeline.

Finds flight logs without an embedding, sends their messages to the
embedding model service in batches, and writes the resulting vectors
back to PostgreSQL together with provenance columns
(`embedding_model`, `embedding_dim`, `embedded_at`).

The pipeline is intentionally simple and synchronous: it is called from
- the startup hook (with a small `STARTUP_INGEST_LIMIT` cap so the API
  does not block forever on first boot),
- the `POST /ingest` endpoint (operator-triggered top-up),
- the `POST /logs` flow once a new log is created (Phase 5+).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import session_scope
from .model_clients import EmbeddingClient, EmbeddingServiceError
from .models import FlightLog

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


def _select_unembedded_logs(session: Session, limit: int) -> List[FlightLog]:
    """Return up to `limit` logs that have no embedding yet, oldest first."""
    if limit <= 0:
        return []
    stmt = (
        select(FlightLog)
        .where(FlightLog.embedding.is_(None))
        .order_by(FlightLog.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def ingest_unembedded_logs(
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
    client: Optional[EmbeddingClient] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> IngestionResult:
    """Embed up to `limit` logs that currently have no embedding.

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
                logs = _select_unembedded_logs(session, chunk_size)
                if not logs:
                    return result

                result.scanned += len(logs)
                texts = [log.message for log in logs]

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
                for log, vector in zip(logs, embed_result.embeddings):
                    log.embedding = vector
                    log.embedding_model = embed_result.model
                    log.embedding_dim = embed_result.dim
                    log.embedded_at = now

                result.embedded += len(logs)

            remaining -= len(logs)
            if len(logs) < chunk_size:
                # Nothing left to embed.
                break
    finally:
        if owns_client:
            embedding_client.close()

    return result


def embed_log_by_id(
    log_id: int, client: Optional[EmbeddingClient] = None
) -> tuple[bool, Optional[str]]:
    """Embed a single log by id.

    Returns a ``(embedded, error)`` tuple:

    * ``embedded`` is True when the log was (re)embedded.
    * ``error`` carries a human-readable reason when embedding was not
      performed, so callers (e.g. the ``POST /logs`` handler) can
      surface transient failures to the UI. ``error`` is ``None`` on
      success, and also ``None`` when the log id simply does not exist.

    Useful when a new log is submitted via ``POST /logs``: ingestion can
    be triggered for just that row instead of scanning the full table.
    """
    settings = get_settings()
    owns_client = client is None
    embedding_client = client or EmbeddingClient()
    try:
        with session_scope() as session:
            log = session.get(FlightLog, log_id)
            if log is None:
                return False, None
            try:
                embed_result = embedding_client.embed([log.message])
            except EmbeddingServiceError as exc:
                logger.warning("Failed to embed log %s: %s", log_id, exc)
                return False, f"embedding service unavailable: {exc}"
            if embed_result.dim != settings.embedding_dim:
                logger.error(
                    "Refusing to embed log %s: dim %s != configured %s",
                    log_id,
                    embed_result.dim,
                    settings.embedding_dim,
                )
                return False, (
                    f"embedding dimension mismatch ({embed_result.dim} != "
                    f"{settings.embedding_dim})"
                )
            log.embedding = embed_result.embeddings[0]
            log.embedding_model = embed_result.model
            log.embedding_dim = embed_result.dim
            log.embedded_at = datetime.now(timezone.utc)
            return True, None
    finally:
        if owns_client:
            embedding_client.close()

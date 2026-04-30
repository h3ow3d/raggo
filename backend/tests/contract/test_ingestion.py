"""Contract: ingestion produces ≥ 1 embedding per pack and is idempotent."""

from __future__ import annotations

from app.core.ingestion import ingest_all_for_domain
from sqlalchemy import func, select


def _embedded_count(session, resource) -> int:
    embedding_col = getattr(resource.model, resource.embedding_column)
    return (
        session.scalar(
            select(func.count()).select_from(resource.model).where(embedding_col.is_not(None))
        )
        or 0
    )


def test_ingestion_produces_embeddings(
    domain_pack, db_session, postgres_engine, patch_embedding_client
):
    # Run ingestion (uses the patched stub embedder).
    result = ingest_all_for_domain(domain_pack, limit=20)
    assert result.embedded > 0, f"Pack {domain_pack.name} produced 0 embeddings: {result.errors}"
    assert not result.errors, f"Ingestion errors for {domain_pack.name}: {result.errors}"

    # The embeddings are persisted on the resource's model.
    db_session.expire_all()
    for resource in domain_pack.embeddable_resources:
        assert _embedded_count(db_session, resource) > 0


def test_ingestion_is_idempotent(domain_pack, db_session, postgres_engine, patch_embedding_client):
    """Once every row is embedded, additional runs are no-ops."""
    # Drive ingestion to steady state (when embedded count stops growing).
    last = -1
    iters = 0
    while iters < 30:
        ingest_all_for_domain(domain_pack, limit=200)
        db_session.expire_all()
        cur = sum(_embedded_count(db_session, r) for r in domain_pack.embeddable_resources)
        if cur == last:
            break
        last = cur
        iters += 1
    assert iters > 0, "ingestion did not embed anything"

    # At steady state, another run must not embed any more rows.
    extra = ingest_all_for_domain(domain_pack, limit=200)
    db_session.expire_all()
    final = sum(_embedded_count(db_session, r) for r in domain_pack.embeddable_resources)
    assert final == last, f"steady-state drift: {last} -> {final}"
    assert extra.embedded == 0, (
        f"Pack {domain_pack.name}: ingestion not idempotent at steady state — "
        f"extra run embedded={extra.embedded}"
    )

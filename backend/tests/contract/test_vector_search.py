"""Contract: vector search returns expected hits per pack.

The deterministic stub embedder maps identical input text to identical
vectors. We exploit this to assert that searching with the *exact text*
of a known seeded row returns that row at top-1 (cosine distance ≈ 0).
This proves the full pipeline (text → embed → pgvector index → cosine
search → ORM hydration → evidence projection) is wired correctly per
pack, without relying on the semantic quality of the stub embedder.
"""

from __future__ import annotations

from app.core.ingestion import ingest_all_for_domain
from app.core.vector_search import search
from sqlalchemy import select


def _pick_known_text(session, resource):
    """Return (id, text) of an embedded row whose text is unique in the
    table — so the deterministic stub embedder can put it at top-1."""
    embedding_col = getattr(resource.model, resource.embedding_column)
    rows = session.execute(select(resource.model).where(embedding_col.is_not(None))).scalars().all()
    seen: dict[str, list[int]] = {}
    for row in rows:
        seen.setdefault(getattr(row, resource.text_column), []).append(row.id)
    for text_value, ids in seen.items():
        if len(ids) == 1:
            return ids[0], text_value
    # Fall back to any row, with multi-id list so callers can be lenient.
    row = rows[0]
    return row.id, getattr(row, resource.text_column)


def test_vector_search_returns_results(domain_pack, db_session, patch_embedding_client):
    ingest_all_for_domain(domain_pack, limit=50)
    db_session.expire_all()

    for resource in domain_pack.embeddable_resources:
        results = search(
            db_session,
            resource=resource,
            query_text="some arbitrary query about issues",
            top_k=5,
            client=patch_embedding_client,
        )
        # Even with a hash-based embedder we should get up to top_k hits
        # because every embedded row contributes a candidate.
        assert len(results) > 0, f"{domain_pack.name}.{resource.name}: no results"
        # Every result has the expected shape.
        for r in results:
            assert "id" in r
            assert "score" in r
            assert "distance" in r
            assert "text" in r
            assert "metadata" in r


def test_vector_search_finds_exact_match_at_top_1(domain_pack, db_session, patch_embedding_client):
    """Querying with the exact seeded text returns that row first.

    This is the strongest deterministic invariant we can assert with a
    stub embedder: identical input → identical hash vector → cosine
    distance 0 → top-1 hit.
    """
    ingest_all_for_domain(domain_pack, limit=50)
    db_session.expire_all()

    for resource in domain_pack.embeddable_resources:
        known_id, known_text = _pick_known_text(db_session, resource)
        results = search(
            db_session,
            resource=resource,
            query_text=known_text,
            top_k=5,
            client=patch_embedding_client,
        )
        assert results, "expected ≥1 result"
        top_ids = [r["id"] for r in results]
        # All identical-text rows share an embedding, so any tied
        # top-distance row is correct. Assert known_id is among them.
        top_distance = results[0]["distance"]
        assert top_distance is not None and top_distance < 1e-6, (
            f"{domain_pack.name}.{resource.name}: top distance not ≈0: {top_distance}"
        )
        zero_distance_ids = {
            r["id"] for r in results if r["distance"] is not None and r["distance"] < 1e-6
        }
        assert known_id in zero_distance_ids, (
            f"{domain_pack.name}.{resource.name}: id={known_id} missing from "
            f"zero-distance hits {zero_distance_ids}; top_ids={top_ids}"
        )

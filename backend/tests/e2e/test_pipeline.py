"""End-to-end test: ingest → search → query against the FastAPI app.

Drives the real FastAPI app (with its lifespan handlers running
init.sql + seed + startup ingestion), but with the embedding /
generation clients replaced by deterministic stubs. This catches
regressions in:

* HTTP routing and request/response schemas
* Pydantic serialization of the agent_trace JSON shape
* Pack switching via the DOMAIN_PACK env var
* End-to-end wiring of safe SQL tools, vector search, and the agent

Skips when no Postgres+pgvector backend is reachable.
"""

from __future__ import annotations

import os

# Use small seeds so the lifespan startup is fast for tests.
os.environ.setdefault("SEED_FLIGHT_COUNT", "30")
os.environ.setdefault("SEED_LOG_COUNT", "90")
os.environ.setdefault("SEED_INCIDENT_COUNT", "10")
# Disable lifespan startup ingestion — the test drives it explicitly.
os.environ.setdefault("STARTUP_INGEST_LIMIT", "0")

# ruff: noqa: E402
import re

import pytest
from app.core import database
from app.core.config import get_settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.contract.conftest import (
    PACKS,
    _postgres_available,
    _StubEmbeddingClient,
)


@pytest.fixture(scope="module")
def _engine():
    get_settings.cache_clear()
    settings = get_settings()
    if not _postgres_available(settings.database_url):
        pytest.skip(f"Postgres not reachable at {settings.database_url}; e2e requires pgvector")
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    database.engine = eng
    database.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False, future=True)
    yield eng
    eng.dispose()


def _drop_pack_state(engine, pack_name: str) -> None:
    """Drop tables for ALL packs so the lifespan rebuilds clean.

    We can't only drop the pack we're switching to — the previous test
    left the *other* pack's tables behind, and pgvector's ``vector``
    extension lives in the same schema. Dropping every registered
    pack's tables guarantees the lifespan's ``init.sql`` runs against
    a clean schema for the new pack.
    """
    from app.core.domain import load_domain

    for name in PACKS:
        try:
            domain = load_domain(name)
        except Exception:  # pragma: no cover - defensive
            continue
        with engine.begin() as conn:
            domain.sqlalchemy_base.metadata.drop_all(bind=conn)


def _drop_ivfflat_indexes(engine) -> None:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexdef ILIKE :pat"),
            {"pat": "%ivfflat%"},
        ).fetchall()
        for (name,) in rows:
            conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))


@pytest.fixture
def patched_clients(monkeypatch):
    """Replace EmbeddingClient and GenerationClient everywhere they're
    constructed so the FastAPI handlers run without external services."""
    stub_embed = _StubEmbeddingClient()
    from unittest.mock import MagicMock

    from app.core.model_clients import GenerationResult

    gen = MagicMock()

    def _generate(*, prompt: str, max_new_tokens: int, temperature: float):
        ids = re.findall(
            r"\[(?:log|incident|flight|ticket|message|ticket_message)[: ]+(\d+)\]",
            prompt,
        )
        unique = sorted(set(ids), key=int)
        return GenerationResult(
            text=f"Based on evidence ids {','.join(unique)}.",
            model="stub-generator",
            finish_reason="stop",
        )

    gen.generate.side_effect = _generate
    gen.close = MagicMock()

    # Patch every construction site.
    for path in (
        "app.core.ingestion.EmbeddingClient",
        "app.core.vector_search.EmbeddingClient",
        "app.core.agent.orchestrator.EmbeddingClient",
    ):
        monkeypatch.setattr(path, lambda *a, **kw: stub_embed)
    monkeypatch.setattr("app.core.agent.orchestrator.GenerationClient", lambda *a, **kw: gen)
    return stub_embed, gen


@pytest.mark.parametrize("pack", PACKS)
def test_pipeline_end_to_end(pack, _engine, patched_clients, monkeypatch):
    monkeypatch.setenv("RAGGO_DOMAIN", pack)
    get_settings.cache_clear()

    _drop_pack_state(_engine, pack)

    # Import here so DOMAIN_PACK is read fresh each parametrisation.
    from app.main import app

    with TestClient(app) as client:
        # Lifespan ran init.sql + seed. Drop ivfflat now that tables exist.
        _drop_ivfflat_indexes(_engine)

        # /health
        r = client.get("/health")
        assert r.status_code == 200, r.text

        # /stats
        r = client.get("/stats")
        assert r.status_code == 200, r.text
        stats = r.json()
        assert isinstance(stats, dict) and stats, "stats payload must be non-empty"

        # /ingest — run the ingestion to steady state.
        prev = -1
        for _ in range(30):
            r = client.post("/ingest", json={"limit": 200, "batch_size": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "scanned" in body and "embedded" in body and "errors" in body
            if body["embedded"] == 0 and prev == 0:
                break
            prev = body["embedded"]

        # /search/vector — must return at least one hit and the documented shape.
        r = client.post(
            "/search/vector",
            json={"query": "issue with billing or delays", "top_k": 3},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["query"]
        assert body["top_k"] == 3
        assert isinstance(body["results"], list)
        assert len(body["results"]) > 0
        for hit in body["results"]:
            assert {"id", "score", "distance", "text", "metadata"} <= set(hit.keys())

        # /query — drives the agent end-to-end.
        question = (
            "Were there any critical safety incidents this week?"
            if pack == "flights"
            else "Any critical urgent tickets we should escalate?"
        )
        r = client.post("/query", json={"question": question, "top_k": 5})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "answer" in body
        assert "evidence" in body
        assert "agent_trace" in body
        trace = body["agent_trace"]
        assert trace["strategy"] in {"sql_only", "vector_only", "vector_and_sql"}
        assert "tools_used" in trace
        assert "vector_queries" in trace
        assert "retrieved_ids" in trace
        # The stub generator embeds evidence ids in the answer; assert it
        # does not invent any IDs not in the trace.
        retrieved_ids = set()
        for ids in trace["retrieved_ids"].values():
            retrieved_ids.update(ids)
        answered_ids = set(int(x) for x in re.findall(r"\d+", body["answer"]))
        if answered_ids:
            assert answered_ids <= retrieved_ids

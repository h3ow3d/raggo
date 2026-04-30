"""Contract test suite for raggo domain packs.

Every domain pack is held to the same contract:

1. Seed loads cleanly into a fresh DB with non-zero rows.
2. Ingestion produces a non-zero number of embeddings and is idempotent
   on re-run.
3. Vector search returns expected hits for fixture queries.
4. The agent answers a fixture question with grounded evidence and
   emits a valid trace.

This conftest:

* Overrides seed-count environment variables to small values before
  importing application settings so the suite is fast.
* Builds an engine against the configured Postgres+pgvector instance.
  When no Postgres is reachable the entire contract suite is skipped —
  these tests are meaningful only against a real pgvector backend
  (CI provides one via the `pgvector/pgvector:pg16` service).
* Provides pack-parametrised fixtures: ``domain_pack``, ``db_session``
  (per-test session against a freshly-seeded schema), ``stub_embed``
  (deterministic in-process embedder), ``stub_generate`` (echo-style
  generator that proves the answer was grounded in evidence).
"""

from __future__ import annotations

import json
import os

# Override seed counts BEFORE the app imports. The settings module is
# loaded lazily via get_settings(); environment variables are read at
# Settings() construction. We use setdefault so a CI run can still pin
# different values if it needs to.
os.environ.setdefault("SEED_FLIGHT_COUNT", "40")
os.environ.setdefault("SEED_LOG_COUNT", "120")
os.environ.setdefault("SEED_INCIDENT_COUNT", "15")
os.environ.setdefault("STARTUP_INGEST_LIMIT", "0")

# ruff: noqa: E402 — imports below depend on the env overrides above.
import hashlib
import math
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from app.core import database
from app.core.config import get_settings
from app.core.domain import DomainPack, load_domain
from app.core.model_clients import EmbeddingResult, GenerationResult
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Reset the cached settings so our env overrides take effect even if a
# previous test (e.g. unit tests) already populated the cache.
get_settings.cache_clear()
SETTINGS = get_settings()
EMBED_DIM = SETTINGS.embedding_dim


PACKS = ("flights", "support_tickets")
FIXTURES_ROOT = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Postgres availability (skip suite when unreachable)
# ---------------------------------------------------------------------------


def _postgres_available(url: str) -> bool:
    try:
        eng = create_engine(url, pool_pre_ping=False)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_engine() -> Engine:
    """Engine bound to the configured Postgres+pgvector instance.

    Skips the whole module when the backend is unreachable so the
    contract tests stay green outside CI.
    """
    url = SETTINGS.database_url
    if not _postgres_available(url):
        pytest.skip(f"Postgres not reachable at {url}; contract suite requires pgvector")
    eng = create_engine(url, pool_pre_ping=True, future=True)
    # Make sure pgvector is available; harmless if already installed.
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    # Re-point the application's module-level engine/SessionLocal at the
    # test database so ingestion's session_scope() uses our connection.
    database.engine = eng
    database.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False, future=True)
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Per-pack DB setup: drop everything, run init.sql, seed
# ---------------------------------------------------------------------------


def _drop_all_pack_tables(conn, pack: DomainPack) -> None:
    """Drop all tables managed by this pack so the next init.sql runs clean."""
    base = pack.sqlalchemy_base
    base.metadata.drop_all(bind=conn)


def _run_init_sql(conn, pack: DomainPack) -> None:
    sql = Path(pack.init_sql_path).read_text()
    # init.sql is multi-statement DDL — exec_driver_sql handles that.
    # The script is a vetted file shipped with the repo.
    conn.exec_driver_sql(sql)
    # IVFFlat indexes need a populated table + ANALYZE to return all hits;
    # on the tiny seeded test dataset they would silently miss rows. Drop
    # them so the planner falls back to sequential scan, which is fine
    # for the test sizes used here.
    rows = conn.execute(
        text("SELECT indexname FROM pg_indexes WHERE indexdef ILIKE :pat"),
        {"pat": "%ivfflat%"},
    ).fetchall()
    for (name,) in rows:
        # Index names come from pg_indexes (system catalog), not user
        # input — safe to interpolate; identifiers can't be parameterised.
        conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))


@pytest.fixture(params=PACKS)
def domain_pack(request) -> DomainPack:
    return load_domain(request.param)


@pytest.fixture
def fresh_db(postgres_engine, domain_pack) -> DomainPack:
    """Drop the pack's tables, recreate via init.sql, and seed.

    Returns the seeded ``DomainPack`` so tests can introspect it.
    """
    with postgres_engine.begin() as conn:
        _drop_all_pack_tables(conn, domain_pack)
        _run_init_sql(conn, domain_pack)

    SessionLocal = sessionmaker(bind=postgres_engine, future=True)
    with SessionLocal() as session:
        domain_pack.seed(session, force=True)
        session.commit()
    return domain_pack


@pytest.fixture
def db_session(postgres_engine, fresh_db) -> Session:
    """Per-test SQLAlchemy session against the freshly-seeded DB."""
    SessionLocal = sessionmaker(bind=postgres_engine, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Deterministic stub embedder + generator
# ---------------------------------------------------------------------------


def _hash_embed(text_value: str, dim: int = EMBED_DIM) -> List[float]:
    """Hash ``text`` to a deterministic unit vector of ``dim`` floats.

    Uses SHA-256 streamed across rotating offsets to fill ``dim``
    components, then L2-normalises so cosine similarity is well-defined.
    Two identical inputs always produce the same vector — this is what
    lets fixture-driven vector_search assertions be reproducible.
    """
    raw = text_value.encode("utf-8")
    bs = bytearray()
    counter = 0
    while len(bs) < dim * 2:  # 2 bytes per float for headroom
        bs.extend(hashlib.sha256(raw + counter.to_bytes(4, "big")).digest())
        counter += 1
    floats: List[float] = []
    for i in range(dim):
        b = (bs[2 * i] << 8) | bs[2 * i + 1]  # 0..65535
        floats.append((b / 32767.5) - 1.0)  # roughly [-1, 1]
    norm = math.sqrt(sum(x * x for x in floats)) or 1.0
    return [x / norm for x in floats]


class _StubEmbeddingClient:
    """In-process replacement for ``EmbeddingClient``."""

    def __init__(self, *args, **kwargs) -> None:  # match real signature
        self.calls: List[List[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def close(self) -> None:
        pass

    def embed(self, texts) -> EmbeddingResult:
        texts = list(texts)
        self.calls.append(texts)
        vectors = [_hash_embed(t) for t in texts]
        return EmbeddingResult(embeddings=vectors, model="stub-hash", dim=EMBED_DIM)

    def health(self) -> dict:
        return {"status": "ok", "model": "stub-hash", "dim": EMBED_DIM}


@pytest.fixture
def stub_embed_client():
    return _StubEmbeddingClient()


@pytest.fixture
def patch_embedding_client(monkeypatch, stub_embed_client):
    """Patch ``EmbeddingClient`` so any code path that constructs one
    (ingestion, vector search) gets the deterministic stub."""
    monkeypatch.setattr("app.core.ingestion.EmbeddingClient", lambda *a, **kw: stub_embed_client)
    monkeypatch.setattr(
        "app.core.vector_search.EmbeddingClient", lambda *a, **kw: stub_embed_client
    )
    return stub_embed_client


@pytest.fixture
def stub_generation_client():
    """Generator that echoes back the IDs it was given as evidence.

    The test asserts that the answer references only retrieved IDs,
    which is impossible to fabricate when the generator is constrained
    to do exactly that.
    """
    client = MagicMock()

    def _generate(*, prompt: str, max_new_tokens: int, temperature: float):
        # Pull every "[type:N]" citation tag out of the prompt's evidence.
        # The domain evidence formatters use tags like [log:42] [incident:7]
        # [flight:99] [ticket:3] [ticket_message:8].
        import re

        ids = re.findall(
            r"\[(?:log|incident|flight|ticket|message|ticket_message)[: ]+(\d+)\]",
            prompt,
        )
        unique_ids = sorted(set(ids), key=int)
        return GenerationResult(
            text=f"Based on evidence ids {','.join(unique_ids)}.",
            model="stub-generator",
            finish_reason="stop",
        )

    client.generate.side_effect = _generate
    client.close = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Pack fixture loader
# ---------------------------------------------------------------------------


def load_pack_queries(pack_name: str) -> List[Dict[str, Any]]:
    """Load canned vector-search queries for a pack from fixtures/."""
    path = FIXTURES_ROOT / pack_name / "queries.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("queries", []) if isinstance(data, dict) else data


def load_pack_agent_questions(pack_name: str) -> List[Dict[str, Any]]:
    path = FIXTURES_ROOT / pack_name / "agent_questions.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("agent_questions", []) if isinstance(data, dict) else data

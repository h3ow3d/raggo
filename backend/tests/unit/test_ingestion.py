"""Unit tests for ``app.core.ingestion``.

The ingestion pipeline embeds rows in batches and writes vectors back
through SQLAlchemy. These tests use mocks for the embedding client and
session so we can exercise control flow (limit handling, batch
boundaries, error paths, `should_stop` cooperative cancellation, dim
mismatch) without a real database. The full end-to-end behaviour
(including idempotency against a real Postgres+pgvector) is exercised
by the contract suite.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from app.core.config import get_settings
from app.core.domain import EmbeddableResource, FilterSpec
from app.core.ingestion import (
    IngestionResult,
    embed_item_by_id,
    ingest_unembedded,
)
from app.core.model_clients import EmbeddingResult, EmbeddingServiceError


class _Item:
    """Minimal ORM-row stand-in with attribute access."""

    def __init__(self, id_: int, message: str) -> None:
        self.id = id_
        self.message = message
        self.embedding: list[float] | None = None
        self.embedding_model: str | None = None
        self.embedding_dim: int | None = None
        self.embedded_at = None


class _StubModel:
    """Stand-in for an ORM model. Only ``id`` is used by ``order_by``."""

    id = MagicMock(name="id_column")
    embedding = MagicMock(name="embedding_column")


def _make_resource() -> EmbeddableResource:
    return EmbeddableResource(
        name="items",
        model=_StubModel,
        text_column="message",
        embedding_column="embedding",
        embedding_model_column="embedding_model",
        embedding_dim_column="embedding_dim",
        embedded_at_column="embedded_at",
        filter_spec=FilterSpec(columns={}),
        evidence_projection=lambda x: {"id": x.id},
    )


def _stub_session_scope_returning(items_per_call: list[list[_Item]]):
    """Build a session_scope replacement that yields a fake session whose
    `_select_unembedded` (we patch) returns the next pre-built batch.

    Each call to ``with session_scope() as s`` consumes one entry from
    ``items_per_call``.
    """
    sessions: list[MagicMock] = []

    @contextmanager
    def _session_scope():
        s = MagicMock()
        sessions.append(s)
        yield s

    return _session_scope, sessions


def _embed_result(dim: int, count: int, model: str = "stub") -> EmbeddingResult:
    return EmbeddingResult(
        embeddings=[[float(i)] * dim for i in range(count)],
        model=model,
        dim=dim,
    )


def test_ingestion_result_to_dict_round_trip():
    r = IngestionResult()
    r.scanned = 3
    r.embedded = 2
    r.errors.append("oops")
    assert r.to_dict() == {"scanned": 3, "embedded": 2, "errors": ["oops"]}


def test_ingest_unembedded_zero_limit_short_circuits():
    res = _make_resource()
    client = MagicMock()
    out = ingest_unembedded(res, limit=0, batch_size=10, client=client)
    assert out.scanned == 0
    assert out.embedded == 0
    client.embed.assert_not_called()


def test_ingest_unembedded_rejects_non_positive_batch_size():
    res = _make_resource()
    with pytest.raises(ValueError, match="batch_size must be positive"):
        ingest_unembedded(res, limit=10, batch_size=0, client=MagicMock())


def test_ingest_unembedded_embeds_and_writes_back():
    res = _make_resource()
    settings = get_settings()
    items = [_Item(1, "a"), _Item(2, "b")]

    client = MagicMock()
    client.embed.return_value = _embed_result(settings.embedding_dim, 2, model="m1")

    scope, _ = _stub_session_scope_returning([items])
    with (
        patch("app.core.ingestion.session_scope", scope),
        patch(
            "app.core.ingestion._select_unembedded",
            side_effect=[items, []],
        ),
    ):
        out = ingest_unembedded(res, limit=2, batch_size=5, client=client)

    assert out.scanned == 2
    assert out.embedded == 2
    assert out.errors == []
    # Each item received its embedding + provenance columns.
    for item in items:
        assert item.embedding is not None
        assert item.embedding_model == "m1"
        assert item.embedding_dim == settings.embedding_dim
        assert item.embedded_at is not None


def test_ingest_unembedded_aborts_on_dim_mismatch():
    res = _make_resource()
    settings = get_settings()
    items = [_Item(1, "a")]

    client = MagicMock()
    client.embed.return_value = _embed_result(settings.embedding_dim + 1, 1)

    scope, _ = _stub_session_scope_returning([items])
    with (
        patch("app.core.ingestion.session_scope", scope),
        patch(
            "app.core.ingestion._select_unembedded",
            return_value=items,
        ),
    ):
        out = ingest_unembedded(res, limit=5, batch_size=5, client=client)

    assert out.scanned == 1
    assert out.embedded == 0
    assert any("dim mismatch" in e for e in out.errors)
    # Items must not be partially mutated when the batch was rejected.
    assert items[0].embedding is None


def test_ingest_unembedded_records_embedding_service_error():
    res = _make_resource()
    items = [_Item(1, "a")]

    client = MagicMock()
    client.embed.side_effect = EmbeddingServiceError("down")

    scope, _ = _stub_session_scope_returning([items])
    with (
        patch("app.core.ingestion.session_scope", scope),
        patch(
            "app.core.ingestion._select_unembedded",
            return_value=items,
        ),
    ):
        out = ingest_unembedded(res, limit=5, batch_size=5, client=client)

    assert out.embedded == 0
    assert any("embedding service error" in e for e in out.errors)


def test_ingest_unembedded_respects_should_stop():
    res = _make_resource()
    client = MagicMock()
    # Stop is checked at the top of the loop — we never embed anything.
    out = ingest_unembedded(
        res,
        limit=10,
        batch_size=5,
        client=client,
        should_stop=lambda: True,
    )
    assert out.scanned == 0
    assert out.embedded == 0
    client.embed.assert_not_called()


def test_ingest_unembedded_stops_when_no_more_items():
    """When `_select_unembedded` returns fewer rows than the chunk size,
    ingestion exits cleanly without scheduling another batch."""
    res = _make_resource()
    settings = get_settings()
    items = [_Item(1, "a")]

    client = MagicMock()
    client.embed.return_value = _embed_result(settings.embedding_dim, 1)

    scope, _ = _stub_session_scope_returning([items, []])
    with (
        patch("app.core.ingestion.session_scope", scope),
        patch(
            "app.core.ingestion._select_unembedded",
            side_effect=[items, []],  # second call wouldn't happen; defensive
        ),
    ):
        out = ingest_unembedded(res, limit=10, batch_size=5, client=client)

    # Only one batch of one item, then early-exit because items < chunk_size.
    assert out.scanned == 1
    assert out.embedded == 1
    assert client.embed.call_count == 1


def test_embed_item_by_id_returns_no_error_for_missing_id():
    res = _make_resource()

    @contextmanager
    def _scope():
        s = MagicMock()
        s.get.return_value = None
        yield s

    client = MagicMock()
    with patch("app.core.ingestion.session_scope", _scope):
        embedded, err = embed_item_by_id(res, 999, client=client)

    assert embedded is False
    assert err is None
    client.embed.assert_not_called()


def test_embed_item_by_id_handles_service_error():
    res = _make_resource()
    item = _Item(1, "hi")

    @contextmanager
    def _scope():
        s = MagicMock()
        s.get.return_value = item
        yield s

    client = MagicMock()
    client.embed.side_effect = EmbeddingServiceError("kaput")

    with patch("app.core.ingestion.session_scope", _scope):
        embedded, err = embed_item_by_id(res, 1, client=client)

    assert embedded is False
    assert err is not None
    assert "embedding service unavailable" in err


def test_embed_item_by_id_writes_back_on_success():
    res = _make_resource()
    settings = get_settings()
    item = _Item(1, "hi")

    @contextmanager
    def _scope():
        s = MagicMock()
        s.get.return_value = item
        yield s

    client = MagicMock()
    client.embed.return_value = _embed_result(settings.embedding_dim, 1, model="m1")

    with patch("app.core.ingestion.session_scope", _scope):
        embedded, err = embed_item_by_id(res, 1, client=client)

    assert embedded is True
    assert err is None
    assert item.embedding is not None
    assert item.embedding_model == "m1"
    assert item.embedding_dim == settings.embedding_dim
    assert item.embedded_at is not None

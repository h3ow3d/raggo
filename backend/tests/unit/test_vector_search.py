"""Unit tests for ``app.core.vector_search``.

Cover input validation, dimension-mismatch handling, and dependency-error
mapping. The actual pgvector query is exercised by the contract / E2E
suites against a real Postgres+pgvector service container.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.core.config import get_settings
from app.core.domain import EmbeddableResource, FilterSpec
from app.core.model_clients import EmbeddingResult, EmbeddingServiceError
from app.core.vector_search import (
    VectorSearchDependencyError,
    VectorSearchError,
    _build_filter_clauses,
    search,
)


class _StubModel:
    """Minimal stand-in for an ORM model with a few attributes."""

    severity = MagicMock(name="severity_column")
    flight_id = MagicMock(name="flight_id_column")


class _StubModelMissingColumn:
    severity = None  # column declared in filter_spec but missing on model


def _make_resource(
    columns: dict[str, str] | None = None,
    model: type = _StubModel,
) -> EmbeddableResource:
    return EmbeddableResource(
        name="dummy",
        model=model,
        text_column="message",
        embedding_column="embedding",
        embedding_model_column=None,
        embedding_dim_column=None,
        embedded_at_column=None,
        filter_spec=FilterSpec(columns=columns or {}),
        evidence_projection=lambda obj: {"id": getattr(obj, "id", None)},
    )


def test_build_filter_clauses_skips_none_values():
    res = _make_resource({"severity": "severity"})
    clauses = _build_filter_clauses(res, {"severity": None})
    assert clauses == []


def test_build_filter_clauses_unknown_key_raises():
    res = _make_resource({"severity": "severity"})
    with pytest.raises(VectorSearchError, match="unsupported filter"):
        _build_filter_clauses(res, {"airline": "BA"})


def test_build_filter_clauses_empty_input_returns_empty():
    res = _make_resource({"severity": "severity"})
    assert _build_filter_clauses(res, None) == []
    assert _build_filter_clauses(res, {}) == []


def test_build_filter_clauses_column_missing_on_model_raises():
    res = _make_resource({"severity": "severity"}, model=_StubModelMissingColumn)
    with pytest.raises(VectorSearchError, match="not found"):
        _build_filter_clauses(res, {"severity": "critical"})


def test_search_rejects_empty_query():
    res = _make_resource()
    with pytest.raises(VectorSearchError, match="must not be empty"):
        search(MagicMock(), resource=res, query_text="   ", top_k=5)


def test_search_rejects_non_positive_top_k():
    res = _make_resource()
    client = MagicMock()
    with pytest.raises(VectorSearchError, match="top_k must be positive"):
        search(MagicMock(), resource=res, query_text="hi", top_k=0, client=client)
    client.embed.assert_not_called()


def test_search_maps_embedding_service_error_to_dependency_error():
    res = _make_resource()
    client = MagicMock()
    client.embed.side_effect = EmbeddingServiceError("nope")

    with pytest.raises(VectorSearchDependencyError, match="failed to embed query"):
        search(MagicMock(), resource=res, query_text="hi", top_k=5, client=client)


def test_search_rejects_dim_mismatch():
    res = _make_resource()
    settings = get_settings()
    client = MagicMock()
    # Return a vector of the wrong dimension.
    client.embed.return_value = EmbeddingResult(
        embeddings=[[0.0] * (settings.embedding_dim + 1)],
        model="stub",
        dim=settings.embedding_dim + 1,
    )

    with pytest.raises(VectorSearchError, match="embedding dim mismatch"):
        search(MagicMock(), resource=res, query_text="hi", top_k=5, client=client)

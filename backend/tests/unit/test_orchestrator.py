"""Unit tests for ``app.core.agent.orchestrator``.

The orchestrator is the integration point between intent classification,
safe SQL tools, vector search, and the generation client. We use a
hand-rolled DomainPack with stub tools/resources and mock the embedding
and generation clients so we can drive every code path without touching
the database or model services.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from app.core.agent import orchestrator
from app.core.agent.intent import IntentPlan
from app.core.domain import (
    DisplayMetadata,
    DomainPack,
    EmbeddableResource,
    FilterSpec,
    IntentRule,
    SafeSqlTool,
)
from app.core.model_clients import GenerationServiceError
from pydantic import BaseModel


class _ToolArgs(BaseModel):
    severity: str | None = None
    limit: int | None = None


def _make_tool(name: str, rows: List[Dict[str, Any]]) -> SafeSqlTool:
    def _func(session, *, severity: str | None = None, limit: int | None = None):
        return rows

    def _evidence(row):
        return {"type": "incident", "id": row["id"], "message": row["message"]}

    return SafeSqlTool(name=name, func=_func, args_model=_ToolArgs, evidence_builder=_evidence)


class _Model:  # stand-in ORM type
    pass


def _make_resource() -> EmbeddableResource:
    return EmbeddableResource(
        name="logs",
        model=_Model,
        text_column="message",
        embedding_column="embedding",
        embedding_model_column=None,
        embedding_dim_column=None,
        embedded_at_column=None,
        filter_spec=FilterSpec(columns={}),
        evidence_projection=lambda obj: {"id": getattr(obj, "id", None)},
    )


def _make_domain(
    plan_for_question: IntentPlan,
    tool_rows: List[Dict[str, Any]] | None = None,
) -> DomainPack:
    """Build a DomainPack whose single intent rule always returns the given plan."""
    tool = _make_tool("get_incidents_by_severity", tool_rows or [])
    rules = (IntentRule(matches=lambda q: True, plan=lambda q: plan_for_question),)
    return DomainPack(
        name="test",
        sqlalchemy_base=type("Base", (), {}),
        init_sql_path="/nonexistent.sql",
        embeddable_resources=(_make_resource(),),
        sql_tools=(tool,),
        intent_rules=rules,
        seed=lambda s: {"skipped": True},
        has_existing_data=lambda s: True,
        stats=lambda s: {},
        domain_context="test ops",
        evidence_formatter=lambda items: "\n".join(
            f"[id:{i.get('id')}] {i.get('message') or i.get('text', '')}" for i in items
        ),
        display=DisplayMetadata(
            domain_name="test",
            title="t",
            record_label_singular="x",
            record_label_plural="xs",
            dashboard_stats=[],
        ),
    )


@pytest.fixture
def gen_client():
    """Mock generation client returning a deterministic answer."""
    client = MagicMock()
    res = MagicMock()
    res.text = " grounded answer based on evidence "
    res.finish_reason = "stop"
    res.model = "stub-llm"
    client.generate.return_value = res
    return client


# --- happy path ------------------------------------------------------------


def test_run_rejects_empty_question(gen_client):
    domain = _make_domain(IntentPlan(strategy="sql_only", sql_calls=[]))
    with pytest.raises(ValueError, match="non-empty"):
        orchestrator.run(MagicMock(), domain=domain, question="   ", generation_client=gen_client)


def test_run_sql_only_populates_trace_and_evidence(gen_client):
    rows = [
        {"id": 1, "message": "engine fire"},
        {"id": 2, "message": "bird strike"},
    ]
    plan = IntentPlan(
        strategy="sql_only",
        sql_calls=[("get_incidents_by_severity", {"severity": "critical"})],
        notes="fixture plan",
    )
    domain = _make_domain(plan, tool_rows=rows)

    result = orchestrator.run(
        MagicMock(),
        domain=domain,
        question="What critical incidents are there?",
        generation_client=gen_client,
    )

    assert result.answer == "grounded answer based on evidence"
    assert len(result.evidence) == 2
    trace = result.agent_trace
    assert trace["strategy"] == "sql_only"
    assert trace["notes"] == "fixture plan"
    assert trace["tools_used"] == ["get_incidents_by_severity"]
    assert trace["vector_queries"] == []
    assert trace["evidence_count"] == 2
    assert trace["retrieved_ids"] == {"incident": [1, 2]}
    assert trace["generation"]["model"] == "stub-llm"
    assert trace["generation"]["finish_reason"] == "stop"
    assert trace["errors"] == []


def test_run_vector_only_invokes_vector_search(gen_client):
    plan = IntentPlan(
        strategy="vector_only",
        vector_query="engine smoke",
        notes="vector path",
    )
    domain = _make_domain(plan)

    fake_hits = [
        {"id": 11, "score": 0.9, "distance": 0.1, "text": "engine smoke", "metadata": {}},
        {"id": 12, "score": 0.85, "distance": 0.15, "text": "smoke detected", "metadata": {}},
    ]

    with patch(
        "app.core.agent.orchestrator.vector_search",
        return_value=fake_hits,
    ) as vs:
        result = orchestrator.run(
            MagicMock(),
            domain=domain,
            question="any smoke incidents?",
            generation_client=gen_client,
        )

    vs.assert_called_once()
    assert result.agent_trace["strategy"] == "vector_only"
    assert result.agent_trace["tools_used"] == ["vector_search"]
    assert result.agent_trace["vector_queries"] == ["engine smoke"]
    assert result.agent_trace["evidence_count"] == 2
    # retrieved_ids keys come from the resource name (`logs`).
    assert result.agent_trace["retrieved_ids"] == {"logs": [11, 12]}


def test_run_hybrid_uses_both_paths(gen_client):
    plan = IntentPlan(
        strategy="vector_and_sql",
        sql_calls=[("get_incidents_by_severity", {"severity": "critical"})],
        vector_query="engine smoke",
    )
    domain = _make_domain(plan, tool_rows=[{"id": 1, "message": "engine fire"}])

    fake_hits = [
        {"id": 7, "score": 0.7, "distance": 0.3, "text": "smoke", "metadata": {}},
    ]

    with patch("app.core.agent.orchestrator.vector_search", return_value=fake_hits):
        result = orchestrator.run(
            MagicMock(),
            domain=domain,
            question="anything bad happen?",
            generation_client=gen_client,
        )

    trace = result.agent_trace
    assert trace["strategy"] == "vector_and_sql"
    assert "get_incidents_by_severity" in trace["tools_used"]
    assert "vector_search" in trace["tools_used"]
    assert trace["evidence_count"] == 2  # 1 SQL + 1 vector
    assert set(trace["retrieved_ids"].keys()) == {"incident", "logs"}


# --- failure-mode behaviour ------------------------------------------------


def test_run_falls_back_to_evidence_only_when_generation_fails():
    """When the LLM is unavailable but evidence was retrieved, the
    orchestrator must return a deterministic fallback answer rather
    than fabricating one.
    """
    rows = [{"id": 1, "message": "engine fire"}]
    plan = IntentPlan(
        strategy="sql_only",
        sql_calls=[("get_incidents_by_severity", {"severity": "critical"})],
    )
    domain = _make_domain(plan, tool_rows=rows)

    bad_client = MagicMock()
    bad_client.generate.side_effect = GenerationServiceError("model down")

    result = orchestrator.run(
        MagicMock(),
        domain=domain,
        question="incidents?",
        generation_client=bad_client,
    )
    # Fallback message — must not invent data.
    assert "Generation model unavailable" in result.answer
    assert "evidence" in result.answer.lower()
    assert any("generation" in e for e in result.agent_trace["errors"])
    # Evidence still surfaces so the UI can show it.
    assert len(result.evidence) == 1


def test_run_no_evidence_no_generation_returns_refusal():
    """No retrieved evidence + LLM down → explicit refusal, not invention."""
    plan = IntentPlan(
        strategy="vector_only",
        vector_query="anything",
    )
    domain = _make_domain(plan)

    bad_client = MagicMock()
    bad_client.generate.side_effect = GenerationServiceError("model down")

    with patch("app.core.agent.orchestrator.vector_search", return_value=[]):
        result = orchestrator.run(
            MagicMock(),
            domain=domain,
            question="anything?",
            generation_client=bad_client,
        )

    assert "Generation model unavailable" in result.answer
    assert "no supporting evidence" in result.answer.lower()
    assert result.evidence == []


def test_run_clamps_top_k_to_settings_cap(gen_client):
    """top_k passed to run() is clamped by ``agent_max_vector_results``."""
    plan = IntentPlan(
        strategy="vector_only",
        vector_query="hi",
    )
    domain = _make_domain(plan)

    captured: Dict[str, Any] = {}

    def _fake_search(*args, **kwargs):
        captured["top_k"] = kwargs["top_k"]
        return []

    with patch("app.core.agent.orchestrator.vector_search", side_effect=_fake_search):
        orchestrator.run(
            MagicMock(),
            domain=domain,
            question="anything",
            top_k=10_000,
            generation_client=gen_client,
        )

    # Cap is settings.agent_max_vector_results (default 8 in config).
    assert captured["top_k"] <= 50  # below the safety cap declared in config


def test_run_records_sql_tool_failure_without_aborting(gen_client):
    """A failing safe-SQL tool should be recorded in trace.errors and
    the run should continue (no exception)."""
    bad_tool = SafeSqlTool(
        name="boom",
        func=lambda session, **kw: (_ for _ in ()).throw(RuntimeError("kaboom")),
        args_model=_ToolArgs,
        evidence_builder=lambda r: r,
    )
    rules = (
        IntentRule(
            matches=lambda q: True,
            plan=lambda q: IntentPlan(
                strategy="sql_only",
                sql_calls=[("boom", {"severity": "critical"})],
            ),
        ),
    )
    domain = DomainPack(
        name="test",
        sqlalchemy_base=type("Base", (), {}),
        init_sql_path="/nonexistent.sql",
        embeddable_resources=(_make_resource(),),
        sql_tools=(bad_tool,),
        intent_rules=rules,
        seed=lambda s: {"skipped": True},
        has_existing_data=lambda s: True,
        stats=lambda s: {},
        domain_context="test ops",
        evidence_formatter=lambda items: "",
        display=DisplayMetadata(
            domain_name="test",
            title="t",
            record_label_singular="x",
            record_label_plural="xs",
            dashboard_stats=[],
        ),
    )

    result = orchestrator.run(
        MagicMock(),
        domain=domain,
        question="anything",
        generation_client=gen_client,
    )

    assert any("boom" in e for e in result.agent_trace["errors"])
    assert result.evidence == []

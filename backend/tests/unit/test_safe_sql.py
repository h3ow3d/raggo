"""Unit tests for ``app.core.safe_sql``.

These tests use synthetic SafeSqlTool instances and a stub session so we
can exercise the registry, validation, and limit-clamping logic without
a real database.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from app.core.domain import SafeSqlTool
from app.core.safe_sql import MAX_LIMIT, SafeSqlRegistry, run_tool
from pydantic import BaseModel, Field


class _DummyArgs(BaseModel):
    name: str = Field(..., min_length=1)
    limit: int | None = Field(default=None, ge=1)


def _make_tool(name: str = "dummy", recorded: list | None = None) -> SafeSqlTool:
    """Build a SafeSqlTool that records its kwargs and returns canned rows."""
    recorded = recorded if recorded is not None else []

    def _func(session, *, name: str, limit: int | None = None) -> List[Dict[str, Any]]:
        recorded.append({"name": name, "limit": limit})
        # Return one row per requested limit to mimic real behaviour.
        n = limit if limit is not None else 3
        return [{"id": i, "name": name} for i in range(n)]

    def _builder(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "dummy", "id": row["id"], "name": row["name"]}

    return SafeSqlTool(
        name=name,
        func=_func,
        args_model=_DummyArgs,
        evidence_builder=_builder,
        description="dummy tool for tests",
    )


def test_registry_indexes_tools_by_name():
    t1 = _make_tool("a")
    t2 = _make_tool("b")
    reg = SafeSqlRegistry((t1, t2))

    assert reg.get("a") is t1
    assert reg.get("b") is t2
    assert reg.get("missing") is None
    assert sorted(reg.names()) == ["a", "b"]
    assert set(reg.all()) == {t1, t2}


def test_run_tool_unknown_name_raises_value_error():
    reg = SafeSqlRegistry((_make_tool("a"),))
    with pytest.raises(ValueError, match="Unknown safe SQL tool"):
        run_tool(MagicMock(), reg, "missing", {"name": "x"})


def test_run_tool_invalid_args_raises_value_error():
    reg = SafeSqlRegistry((_make_tool("a"),))
    # `name` must be non-empty per the Pydantic model.
    with pytest.raises(ValueError, match="Invalid arguments"):
        run_tool(MagicMock(), reg, "a", {"name": ""})


def test_run_tool_clamps_limit_to_max():
    recorded: list = []
    tool = _make_tool("a", recorded=recorded)
    reg = SafeSqlRegistry((tool,))

    rows, evidence = run_tool(
        MagicMock(),
        reg,
        "a",
        {"name": "hi", "limit": MAX_LIMIT * 4},
    )

    assert recorded == [{"name": "hi", "limit": MAX_LIMIT}]
    assert len(rows) == MAX_LIMIT
    assert len(evidence) == MAX_LIMIT
    # Evidence is built via the tool's evidence_builder.
    assert all(item["type"] == "dummy" for item in evidence)


def test_run_tool_passes_through_when_limit_below_cap():
    recorded: list = []
    tool = _make_tool("a", recorded=recorded)
    reg = SafeSqlRegistry((tool,))

    rows, evidence = run_tool(
        MagicMock(),
        reg,
        "a",
        {"name": "hi", "limit": 3},
    )

    assert recorded == [{"name": "hi", "limit": 3}]
    assert len(rows) == 3
    assert len(evidence) == 3


def test_run_tool_wraps_runtime_errors_as_value_error():
    def _explode(session, *, name: str, limit: int | None = None):
        raise RuntimeError("kaboom")

    tool = SafeSqlTool(
        name="boom",
        func=_explode,
        args_model=_DummyArgs,
        evidence_builder=lambda row: row,
    )
    reg = SafeSqlRegistry((tool,))

    with pytest.raises(ValueError, match="Tool boom execution failed"):
        run_tool(MagicMock(), reg, "boom", {"name": "x"})


def test_run_tool_omits_limit_when_not_provided():
    recorded: list = []
    tool = _make_tool("a", recorded=recorded)
    reg = SafeSqlRegistry((tool,))

    run_tool(MagicMock(), reg, "a", {"name": "hi"})

    # `limit` default is None, so it should not be clamped — the tool
    # decides its own default limit downstream.
    assert recorded == [{"name": "hi", "limit": None}]

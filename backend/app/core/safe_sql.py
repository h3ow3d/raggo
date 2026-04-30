"""Generic safe SQL tool registry and execution.

The agent never executes arbitrary model-generated SQL. Instead it
invokes tools from a SafeSqlRegistry populated by the active DomainPack.
Each tool:
- has a Pydantic args model for validation
- returns plain dicts
- applies sensible limits
- builds queries through SQLAlchemy (parameterised)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.domain import SafeSqlTool

logger = logging.getLogger(__name__)

# Hard cap applied to every safe-SQL tool, regardless of caller input.
# Keeps query work bounded even if the agent asks for too much.
MAX_LIMIT = 50


class SafeSqlRegistry:
    """Registry of safe SQL tools for a domain."""

    def __init__(self, tools: Tuple[SafeSqlTool, ...]):
        self._tools: Dict[str, SafeSqlTool] = {tool.name: tool for tool in tools}

    def get(self, name: str) -> SafeSqlTool | None:
        """Get a tool by name, or None if not found."""
        return self._tools.get(name)

    def names(self) -> List[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def all(self) -> Tuple[SafeSqlTool, ...]:
        """Return all registered tools."""
        return tuple(self._tools.values())


def run_tool(
    session: Session,
    registry: SafeSqlRegistry,
    name: str,
    kwargs: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Execute a safe SQL tool and return (rows, evidence_items).

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session.
    registry : SafeSqlRegistry
        The domain's tool registry.
    name : str
        Tool name to invoke.
    kwargs : dict
        Arguments to pass to the tool. Will be validated against the
        tool's args_model.

    Returns
    -------
    (rows, evidence_items) : tuple[list[dict], list[dict]]
        rows: raw result dicts from the tool function
        evidence_items: evidence dicts built by tool.evidence_builder

    Raises
    ------
    ValueError
        If the tool is not found or validation fails.
    """
    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"Unknown safe SQL tool: {name!r}. Available: {registry.names()}")

    # Validate kwargs with the tool's Pydantic model
    try:
        validated = tool.args_model(**kwargs)
    except ValidationError as exc:
        raise ValueError(f"Invalid arguments for {name}: {exc}") from exc

    # Clamp any 'limit' field to MAX_LIMIT
    validated_dict = validated.model_dump()
    if "limit" in validated_dict and validated_dict["limit"] is not None:
        validated_dict["limit"] = min(int(validated_dict["limit"]), MAX_LIMIT)

    # Call the tool function
    try:
        rows = tool.func(session, **validated_dict)
    except Exception as exc:
        logger.warning("Safe SQL tool %s failed: %s", name, exc)
        raise ValueError(f"Tool {name} execution failed: {exc}") from exc

    # Build evidence items
    evidence_items = [tool.evidence_builder(row) for row in rows]

    return rows, evidence_items

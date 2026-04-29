"""Basic, extensible RAG agent for rag-flight-lab.

Pipeline (Phase 5, intentionally simple):

1. Receive a user question.
2. Classify intent using deterministic keyword rules.
3. Pick a strategy: ``sql_only``, ``vector_only``, or ``vector_and_sql``.
4. Run safe SQL tools and/or pgvector similarity search to gather
   evidence. The agent **never** executes raw model-generated SQL —
   it only calls functions in :mod:`safe_sql_tools`.
5. Build a grounded prompt that lists the evidence with stable IDs and
   instructs the local generation model to answer **only** from that
   evidence, to admit when evidence is insufficient, and to cite log /
   incident IDs.
6. Return ``answer``, ``evidence`` (as structured items the UI can show),
   and ``agent_trace`` (strategy, tools used, vector queries, sql
   filters, retrieved IDs) for transparency and auditing.

The module is intentionally small and dependency-free of any agent
framework so the pipeline is easy to read and extend later (memory,
query rewriting, multi-step planning, audit trails, approval gates).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .config import get_settings
from .model_clients import (
    EmbeddingClient,
    GenerationClient,
    GenerationServiceError,
)
from .safe_sql_tools import (
    SAFE_TOOLS,
    get_delayed_flights,
    get_incidents_by_severity,
    get_flights_by_airport,
    get_top_delay_airports,
)
from .vector_search import (
    VectorSearchDependencyError,
    VectorSearchError,
    search_logs,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent classification (deterministic)
# ---------------------------------------------------------------------------


@dataclass
class Intent:
    """The agent's deterministic plan for a question.

    Attributes
    ----------
    strategy:
        ``"sql_only"``, ``"vector_only"``, or ``"vector_and_sql"``. Drives
        which retrieval steps run.
    sql_calls:
        Ordered list of ``(tool_name, kwargs)`` to invoke from
        :data:`safe_sql_tools.SAFE_TOOLS`. Tool names are validated
        against that allowlist before execution.
    vector_query:
        Free-text query to send through pgvector similarity search, or
        ``None`` to skip vector retrieval. Defaults to the original
        question when the intent does not rewrite it.
    vector_filters:
        Optional structured filters to narrow vector search.
    notes:
        Human-readable hint explaining why this plan was chosen — useful
        for the trace and for debugging.
    """

    strategy: str
    sql_calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    vector_query: Optional[str] = None
    vector_filters: Optional[Dict[str, Any]] = None
    notes: str = ""


# Compiled keyword groups. Kept simple on purpose so the routing remains
# predictable; the LLM is never asked to pick a tool.
_DELAY_RE = re.compile(r"\bdelay(s|ed|ing)?\b", re.IGNORECASE)
_SAFETY_RE = re.compile(r"\bsafety\b|\bsafety issue|\bincident", re.IGNORECASE)
_SEVERE_RE = re.compile(
    r"\bsevere|\bcritical|\bserious|\bmajor\b", re.IGNORECASE
)
_AIRPORT_LIST_RE = re.compile(
    r"\bwhich airport|airports? (are )?most|airports? .* delays?",
    re.IGNORECASE,
)
_RECENT_RE = re.compile(
    r"\brecent(ly)?\b|\bthis week\b|\blast (\d+\s*)?(day|days|week|weeks)",
    re.IGNORECASE,
)


def _recent_window(question: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Return a ``(start, end)`` window when the question is time-scoped.

    Recognises "this week", "recent(ly)", and "last N day(s)/week(s)".
    Otherwise returns ``(None, None)`` so callers fall back to the
    default behaviour of the SQL tool.
    """
    if not _RECENT_RE.search(question):
        return None, None

    now = datetime.now(timezone.utc)
    # "last N day(s)/week(s)"
    m = re.search(
        r"last\s+(\d+)?\s*(day|days|week|weeks)", question, re.IGNORECASE
    )
    if m:
        n = int(m.group(1) or 1)
        unit = m.group(2).lower()
        delta = timedelta(days=n if unit.startswith("day") else 7 * n)
        return now - delta, now

    if re.search(r"this week", question, re.IGNORECASE):
        return now - timedelta(days=7), now

    # "recent" / "recently": 7-day window is a reasonable default.
    return now - timedelta(days=7), now


def classify_intent(question: str) -> Intent:
    """Map a question to a retrieval plan with deterministic rules.

    The rules are intentionally simple and can be extended without
    touching the rest of the agent. The LLM is never asked to pick the
    plan or any of its parameters.
    """
    q = question.strip()
    start, end = _recent_window(q)

    # --- "Which airports are most associated with delays?" ---------------
    if _AIRPORT_LIST_RE.search(q) and _DELAY_RE.search(q):
        return Intent(
            strategy="sql_only",
            sql_calls=[
                (
                    "get_top_delay_airports",
                    {"start_time": start, "end_time": end, "limit": 10},
                )
            ],
            notes="airport-delay aggregation",
        )

    # --- "Show me severe/critical incidents..." --------------------------
    if _SEVERE_RE.search(q) and (
        _SAFETY_RE.search(q) or "incident" in q.lower()
    ):
        return Intent(
            strategy="vector_and_sql",
            sql_calls=[
                (
                    "get_incidents_by_severity",
                    {
                        "severity": ["critical"],
                        "start_time": start,
                        "end_time": end,
                        "limit": 10,
                    },
                )
            ],
            vector_query=q,
            notes="severe-incident lookup with corroborating logs",
        )

    # --- "Were there any safety issues recently?" ------------------------
    if _SAFETY_RE.search(q):
        return Intent(
            strategy="vector_and_sql",
            sql_calls=[
                (
                    "get_incidents_by_severity",
                    {
                        "severity": ["warning", "critical"],
                        "start_time": start,
                        "end_time": end,
                        "limit": 10,
                    },
                )
            ],
            vector_query=q,
            vector_filters={"log_type": "safety"},
            notes="recent safety overview",
        )

    # --- "Why are flights delayed this week?" ----------------------------
    if _DELAY_RE.search(q):
        return Intent(
            strategy="vector_and_sql",
            sql_calls=[
                (
                    "get_delayed_flights",
                    {"start_time": start, "end_time": end, "limit": 10},
                )
            ],
            vector_query=q,
            notes="delay explanation: SQL summary + vector evidence",
        )

    # --- Default: vector-only RAG ----------------------------------------
    # Covers questions like "engine-related problems", "recurring
    # hydraulic issues", "logs similar to ...". The LLM still only sees
    # the supplied evidence.
    return Intent(
        strategy="vector_only",
        vector_query=q,
        notes="open-ended question; vector RAG over flight logs",
    )


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        # Drop sub-second precision; readability matters more than
        # precision for the LLM context.
        return value.replace(microsecond=0).isoformat()
    return str(value) if value is not None else ""


def _evidence_from_log(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "flight_log",
        "id": int(row["log_id"] if "log_id" in row else row["id"]),
        "flight_id": row.get("flight_id"),
        "flight_number": row.get("flight_number"),
        "origin": row.get("origin"),
        "destination": row.get("destination"),
        "log_time": _format_dt(row.get("log_time")),
        "log_type": row.get("log_type"),
        "source_system": row.get("source_system"),
        "severity": row.get("severity"),
        "message": row.get("message"),
        "similarity": row.get("similarity"),
    }


def _evidence_from_incident(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "incident",
        "id": int(row["id"]),
        "flight_id": row.get("flight_id"),
        "flight_number": row.get("flight_number"),
        "origin": row.get("origin"),
        "destination": row.get("destination"),
        "incident_time": _format_dt(row.get("incident_time")),
        "severity": row.get("severity"),
        "category": row.get("category"),
        "resolution_status": row.get("resolution_status"),
        "message": row.get("description"),
    }


def _evidence_from_flight(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "flight",
        "id": int(row["id"]),
        "flight_number": row.get("flight_number"),
        "airline": row.get("airline"),
        "origin": row.get("origin"),
        "destination": row.get("destination"),
        "scheduled_departure": _format_dt(row.get("scheduled_departure")),
        "actual_departure": _format_dt(row.get("actual_departure")),
        "scheduled_arrival": _format_dt(row.get("scheduled_arrival")),
        "actual_arrival": _format_dt(row.get("actual_arrival")),
        "status": row.get("status"),
    }


def _evidence_from_airport_count(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "airport_delay_count",
        "airport": row.get("airport"),
        "delay_count": row.get("delay_count"),
    }


# Maps a SQL tool name to the function that converts each result row
# into an evidence item. Centralising this keeps :func:`run` simple and
# avoids ad-hoc shape assumptions further down.
_EVIDENCE_BUILDERS: Dict[str, Any] = {
    "get_delayed_flights": _evidence_from_flight,
    "get_incidents_by_severity": _evidence_from_incident,
    "get_flights_by_airport": _evidence_from_flight,
    "get_logs_by_flight": _evidence_from_log,
    "get_top_delay_airports": _evidence_from_airport_count,
}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_SYSTEM_INSTRUCTIONS = (
    "You are a flight operations analyst. Answer the user's question "
    "using ONLY the evidence provided below. Do not invent facts. If "
    "the evidence is insufficient to answer, say so clearly. Cite "
    "specific items by their ID using the format [log:<id>], "
    "[incident:<id>], or [flight:<id>] when relevant. Keep the answer "
    "concise (no more than ~6 sentences)."
)


def _format_evidence_for_prompt(evidence: List[Dict[str, Any]]) -> str:
    """Render evidence as a compact, ID-tagged block for the LLM."""
    if not evidence:
        return "(no evidence retrieved)"

    lines: List[str] = []
    for item in evidence:
        kind = item["type"]
        if kind == "flight_log":
            lines.append(
                f"[log:{item['id']}] flight={item.get('flight_number') or item.get('flight_id')} "
                f"{item.get('origin','?')}->{item.get('destination','?')} "
                f"time={item.get('log_time','')} type={item.get('log_type','')} "
                f"sev={item.get('severity','')} src={item.get('source_system','')} "
                f"msg={item.get('message','')}"
            )
        elif kind == "incident":
            lines.append(
                f"[incident:{item['id']}] flight={item.get('flight_number') or item.get('flight_id')} "
                f"{item.get('origin','?')}->{item.get('destination','?')} "
                f"time={item.get('incident_time','')} sev={item.get('severity','')} "
                f"cat={item.get('category','')} status={item.get('resolution_status','')} "
                f"msg={item.get('message','')}"
            )
        elif kind == "flight":
            lines.append(
                f"[flight:{item['id']}] {item.get('flight_number','')} "
                f"{item.get('origin','?')}->{item.get('destination','?')} "
                f"sched_dep={item.get('scheduled_departure','')} "
                f"actual_dep={item.get('actual_departure','')} "
                f"status={item.get('status','')}"
            )
        elif kind == "airport_delay_count":
            lines.append(
                f"[airport:{item.get('airport')}] delayed_flights={item.get('delay_count')}"
            )
        else:  # pragma: no cover - defensive
            lines.append(f"[{kind}] {item}")
    return "\n".join(lines)


def build_prompt(question: str, evidence: List[Dict[str, Any]]) -> str:
    """Assemble the grounded prompt sent to the generation model."""
    return (
        f"{_SYSTEM_INSTRUCTIONS}\n\n"
        f"EVIDENCE:\n{_format_evidence_for_prompt(evidence)}\n\n"
        f"QUESTION: {question.strip()}\n\n"
        f"ANSWER:"
    )


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Final output of one agent run."""

    answer: str
    evidence: List[Dict[str, Any]]
    agent_trace: Dict[str, Any]


def _run_sql_calls(
    session: Session,
    calls: List[Tuple[str, Dict[str, Any]]],
    trace_sql: List[Dict[str, Any]],
    errors: List[str],
) -> List[Dict[str, Any]]:
    """Execute the planned safe SQL tools and collect evidence rows.

    Tool names are checked against :data:`safe_sql_tools.SAFE_TOOLS` so
    a planning bug or future extension cannot smuggle in arbitrary
    callables.
    """
    out: List[Dict[str, Any]] = []
    for name, kwargs in calls:
        tool_fn = SAFE_TOOLS.get(name)
        builder = _EVIDENCE_BUILDERS.get(name)
        if tool_fn is None or builder is None:
            errors.append(f"unknown safe SQL tool: {name}")
            continue
        try:
            rows = tool_fn(session, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("safe SQL tool %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue
        # Record the call in the trace with sanitised kwargs (datetimes
        # serialised, no SQL strings — there are no SQL strings to leak).
        sanitised_args = {
            k: _format_dt(v) if isinstance(v, datetime) else v
            for k, v in kwargs.items()
        }
        trace_sql.append(
            {
                "tool": name,
                "args": sanitised_args,
                "result_count": len(rows),
            }
        )
        out.extend(builder(row) for row in rows)
    return out


def _run_vector(
    session: Session,
    query_text: str,
    top_k: int,
    filters: Optional[Dict[str, Any]],
    trace_vector: List[Dict[str, Any]],
    errors: List[str],
    embedding_client: Optional[EmbeddingClient] = None,
) -> List[Dict[str, Any]]:
    try:
        rows = search_logs(
            session=session,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            client=embedding_client,
        )
    except VectorSearchError as exc:
        errors.append(f"vector_search: {exc}")
        return []
    except VectorSearchDependencyError as exc:
        errors.append(f"vector_search dependency: {exc}")
        return []
    trace_vector.append(
        {
            "query": query_text,
            "top_k": top_k,
            "filters": filters or {},
            "result_count": len(rows),
        }
    )
    return [_evidence_from_log(row) for row in rows]


def run(
    session: Session,
    question: str,
    top_k: Optional[int] = None,
    embedding_client: Optional[EmbeddingClient] = None,
    generation_client: Optional[GenerationClient] = None,
) -> AgentResult:
    """Run the agent end-to-end for a single user question.

    Parameters
    ----------
    session:
        Open SQLAlchemy session. The agent only reads through it.
    question:
        Free-text user question.
    top_k:
        Override for vector-search ``top_k``. Falls back to
        ``Settings.agent_max_vector_results``. Capped by the same
        setting so a caller cannot request unbounded retrieval.
    embedding_client / generation_client:
        Injected for tests; created lazily otherwise.
    """
    if not question or not question.strip():
        raise ValueError("question must be non-empty")

    settings = get_settings()
    eff_top_k = min(
        top_k if top_k is not None else settings.agent_max_vector_results,
        settings.agent_max_vector_results,
    )

    intent = classify_intent(question)

    trace_sql: List[Dict[str, Any]] = []
    trace_vector: List[Dict[str, Any]] = []
    errors: List[str] = []
    evidence: List[Dict[str, Any]] = []

    if intent.strategy in {"sql_only", "vector_and_sql"}:
        evidence.extend(_run_sql_calls(session, intent.sql_calls, trace_sql, errors))

    if intent.strategy in {"vector_only", "vector_and_sql"} and intent.vector_query:
        evidence.extend(
            _run_vector(
                session=session,
                query_text=intent.vector_query,
                top_k=eff_top_k,
                filters=intent.vector_filters,
                trace_vector=trace_vector,
                errors=errors,
                embedding_client=embedding_client,
            )
        )

    # Build the grounded prompt and call the local generation service.
    prompt = build_prompt(question, evidence)

    owns_gen = generation_client is None
    gen = generation_client or GenerationClient()
    answer: str
    finish_reason: str = "n/a"
    model_name: str = settings.generation_model_name
    try:
        try:
            result = gen.generate(
                prompt=prompt,
                max_new_tokens=settings.agent_max_new_tokens,
                temperature=0.2,
            )
            answer = result.text.strip()
            finish_reason = result.finish_reason
            model_name = result.model
        except GenerationServiceError as exc:
            logger.warning("generation service failed: %s", exc)
            errors.append(f"generation: {exc}")
            # Fall back to a simple "evidence-only" answer so the API
            # contract still holds even when the LLM is unavailable.
            if evidence:
                answer = (
                    "Generation model unavailable. Returning retrieved "
                    f"evidence only ({len(evidence)} item(s))."
                )
            else:
                answer = (
                    "Generation model unavailable and no supporting "
                    "evidence was retrieved."
                )
    finally:
        if owns_gen:
            gen.close()

    # Build a stable, readable trace. The IDs let the UI link back to
    # specific records, and the strategy/tools fields satisfy the
    # spec's transparency requirement.
    tools_used: List[str] = []
    if trace_sql:
        tools_used.extend(call["tool"] for call in trace_sql)
    if trace_vector:
        tools_used.append("vector_search")

    retrieved_log_ids = sorted(
        {int(item["id"]) for item in evidence if item["type"] == "flight_log"}
    )
    retrieved_incident_ids = sorted(
        {int(item["id"]) for item in evidence if item["type"] == "incident"}
    )
    retrieved_flight_ids = sorted(
        {int(item["id"]) for item in evidence if item["type"] == "flight"}
    )

    agent_trace: Dict[str, Any] = {
        "strategy": intent.strategy,
        "notes": intent.notes,
        "tools_used": tools_used,
        "vector_queries": [v["query"] for v in trace_vector],
        "vector_calls": trace_vector,
        "sql_calls": trace_sql,
        "retrieved_log_ids": retrieved_log_ids,
        "retrieved_incident_ids": retrieved_incident_ids,
        "retrieved_flight_ids": retrieved_flight_ids,
        "evidence_count": len(evidence),
        "generation": {
            "model": model_name,
            "finish_reason": finish_reason,
        },
        "errors": errors,
    }

    return AgentResult(answer=answer, evidence=evidence, agent_trace=agent_trace)


__all__ = [
    "AgentResult",
    "Intent",
    "build_prompt",
    "classify_intent",
    "run",
]

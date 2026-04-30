"""Generic agent orchestrator for raggo.

Runs the complete RAG pipeline:
1. Classify intent (deterministic rules from domain)
2. Retrieve evidence (safe SQL tools and/or vector search)
3. Build grounded prompt
4. Call generation model
5. Return answer + evidence + trace

The orchestrator is domain-agnostic; all domain specifics come from
the DomainPack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.core.agent.intent import IntentPlan, RuleBasedIntentClassifier
from app.core.agent.prompt import build_prompt
from app.core.config import get_settings
from app.core.domain import DomainPack
from app.core.model_clients import (
    EmbeddingClient,
    GenerationClient,
    GenerationServiceError,
)
from app.core.safe_sql import SafeSqlRegistry, run_tool
from app.core.vector_search import (
    VectorSearchDependencyError,
    VectorSearchError,
)
from app.core.vector_search import (
    search as vector_search,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Final output of one agent run."""

    answer: str
    evidence: List[Dict[str, Any]]
    agent_trace: Dict[str, Any]


def _run_sql_calls(
    session: Session,
    registry: SafeSqlRegistry,
    calls: List[tuple[str, Dict[str, Any]]],
    trace_sql: List[Dict[str, Any]],
    errors: List[str],
) -> List[Dict[str, Any]]:
    """Execute the planned safe SQL tools and collect evidence items.

    Tool names are checked against the registry so a planning bug or
    future extension cannot smuggle in arbitrary callables.
    """
    out: List[Dict[str, Any]] = []
    for name, kwargs in calls:
        try:
            rows, evidence = run_tool(session, registry, name, kwargs)
        except ValueError as exc:
            logger.warning("Safe SQL tool %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Safe SQL tool %s unexpected error: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue

        # Record the call in the trace
        trace_sql.append(
            {
                "tool": name,
                "args": kwargs,
                "result_count": len(rows),
            }
        )
        out.extend(evidence)
    return out


def _run_vector(
    session: Session,
    domain: DomainPack,
    query_text: str,
    resource_name: str | None,
    top_k: int,
    filters: Dict[str, Any] | None,
    trace_vector: List[Dict[str, Any]],
    errors: List[str],
    embedding_client: EmbeddingClient | None = None,
) -> List[Dict[str, Any]]:
    """Run vector search and return evidence items."""
    # Pick the resource
    if resource_name is None:
        if not domain.embeddable_resources:
            errors.append("No embeddable resources in domain")
            return []
        resource = domain.embeddable_resources[0]
    else:
        resource = None
        for r in domain.embeddable_resources:
            if r.name == resource_name:
                resource = r
                break
        if resource is None:
            errors.append(f"Unknown resource: {resource_name}")
            return []

    try:
        rows = vector_search(
            session=session,
            resource=resource,
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
            "resource": resource.name,
            "query": query_text,
            "top_k": top_k,
            "filters": filters or {},
            "result_count": len(rows),
        }
    )

    # Convert vector hits to evidence items
    # The metadata dict already contains the projected fields
    evidence = []
    for hit in rows:
        # The hit dict from vector_search has: id, score, distance, text, metadata
        # We merge metadata with a type field
        item = {
            "type": resource.name,  # or extract from metadata if present
            "id": hit["id"],
            **hit["metadata"],
        }
        if "similarity" not in item and hit.get("score") is not None:
            item["similarity"] = hit["score"]
        evidence.append(item)

    return evidence


def run(
    session: Session,
    *,
    domain: DomainPack,
    question: str,
    top_k: int | None = None,
    embedding_client: EmbeddingClient | None = None,
    generation_client: GenerationClient | None = None,
) -> AgentResult:
    """Run the agent end-to-end for a single user question.

    Parameters
    ----------
    session:
        Open SQLAlchemy session. The agent only reads through it.
    domain:
        The active DomainPack providing tools, rules, and prompts.
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

    # Build the classifier and classify the intent. If the domain supplies a
    # default intent plan, prefer it over the generic vector-only fallback so
    # per-domain conventions (e.g. a specific `vector_resource`) are honoured.
    domain_default = getattr(domain, "default_intent_plan", None)
    if domain_default is None:

        def domain_default(q: str) -> IntentPlan:
            return IntentPlan(
                strategy="vector_only",
                vector_query=q,
                notes="default: vector-only RAG",
            )

    classifier = RuleBasedIntentClassifier(
        rules=domain.intent_rules,
        default_plan=domain_default,
    )
    intent = classifier.classify(question)

    # Build the SQL registry
    sql_registry = SafeSqlRegistry(domain.sql_tools)

    trace_sql: List[Dict[str, Any]] = []
    trace_vector: List[Dict[str, Any]] = []
    errors: List[str] = []
    evidence: List[Dict[str, Any]] = []

    if intent.strategy in {"sql_only", "vector_and_sql"}:
        # Clamp every planned SQL call's `limit` to the configured cap
        clamped_calls: List[tuple[str, Dict[str, Any]]] = []
        for name, kwargs in intent.sql_calls:
            kwargs = dict(kwargs)
            existing = kwargs.get("limit")
            if existing is None:
                kwargs["limit"] = settings.agent_max_sql_results
            else:
                kwargs["limit"] = min(int(existing), settings.agent_max_sql_results)
            clamped_calls.append((name, kwargs))
        evidence.extend(_run_sql_calls(session, sql_registry, clamped_calls, trace_sql, errors))

    if intent.strategy in {"vector_only", "vector_and_sql"} and intent.vector_query:
        evidence.extend(
            _run_vector(
                session=session,
                domain=domain,
                query_text=intent.vector_query,
                resource_name=intent.vector_resource,
                top_k=eff_top_k,
                filters=intent.vector_filters,
                trace_vector=trace_vector,
                errors=errors,
                embedding_client=embedding_client,
            )
        )

    # Build the grounded prompt and call the local generation service.
    prompt = build_prompt(
        question,
        evidence,
        domain_context=domain.domain_context,
        evidence_formatter=domain.evidence_formatter,
    )

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
                answer = "Generation model unavailable and no supporting evidence was retrieved."
    finally:
        if owns_gen:
            gen.close()

    # Build a stable, readable trace. The IDs let the UI link back to
    # specific records, and the strategy/tools fields satisfy the
    # transparency requirement.
    tools_used: List[str] = []
    if trace_sql:
        tools_used.extend(call["tool"] for call in trace_sql)
    if trace_vector:
        tools_used.append("vector_search")

    # Build retrieved_ids dict keyed by evidence type
    retrieved_ids: Dict[str, List[int]] = {}
    for item in evidence:
        item_type = item.get("type", "unknown")
        item_id = item.get("id")
        if item_id is not None:
            if item_type not in retrieved_ids:
                retrieved_ids[item_type] = []
            retrieved_ids[item_type].append(int(item_id))
    # Sort and deduplicate
    for key in retrieved_ids:
        retrieved_ids[key] = sorted(set(retrieved_ids[key]))

    agent_trace: Dict[str, Any] = {
        "strategy": intent.strategy,
        "notes": intent.notes,
        "tools_used": tools_used,
        "vector_queries": [v["query"] for v in trace_vector],
        "vector_calls": trace_vector,
        "sql_calls": trace_sql,
        "retrieved_ids": retrieved_ids,
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
    "run",
]

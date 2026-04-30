"""Contract: agent /query end-to-end against a real DB + stub models.

Each pack must produce a valid agent_trace with:
  * strategy ∈ {sql_only, vector_only, vector_and_sql}
  * tools_used and/or vector_queries listed
  * non-empty retrieved_ids (when evidence was retrieved)
  * answer text built only from retrieved IDs (the stub generator
    echoes back exactly the IDs it received in the prompt, so the
    answer's IDs must be a subset of trace.retrieved_ids).
"""

from __future__ import annotations

import re

import pytest
from app.core.agent.orchestrator import run as agent_run
from app.core.ingestion import ingest_all_for_domain

from .conftest import load_pack_agent_questions

PACKS_AND_QUESTIONS = [
    (pack, q) for pack in ("flights", "support_tickets") for q in load_pack_agent_questions(pack)
]


def _all_evidence_ids(trace) -> set[int]:
    out: set[int] = set()
    for ids in trace["retrieved_ids"].values():
        out.update(ids)
    return out


@pytest.mark.parametrize(
    "pack_name,fixture",
    PACKS_AND_QUESTIONS,
    ids=lambda v: v["question"][:40] if isinstance(v, dict) else v,
)
def test_agent_query_contract(
    pack_name,
    fixture,
    postgres_engine,
    patch_embedding_client,
    stub_generation_client,
):
    from app.core.domain import load_domain

    domain = load_domain(pack_name)

    # Reset, seed, ingest for this single pack.
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=postgres_engine, future=True)
    with postgres_engine.begin() as conn:
        domain.sqlalchemy_base.metadata.drop_all(bind=conn)
        from pathlib import Path

        conn.exec_driver_sql(Path(domain.init_sql_path).read_text())
    with SessionLocal() as s:
        domain.seed(s, force=True)
        s.commit()
    ingest_all_for_domain(domain, limit=50)

    with SessionLocal() as session:
        result = agent_run(
            session,
            domain=domain,
            question=fixture["question"],
            top_k=5,
            embedding_client=patch_embedding_client,
            generation_client=stub_generation_client,
        )

    trace = result.agent_trace

    # Strategy must match one of the fixture's allowed values.
    assert trace["strategy"] in fixture["expected_strategies"], (
        f"strategy={trace['strategy']} not in {fixture['expected_strategies']}"
    )
    assert trace["strategy"] in {"sql_only", "vector_only", "vector_and_sql"}

    # If the fixture pins required tools, they must show up.
    for required_tool in fixture.get("expected_tools_any", []):
        assert required_tool in trace["tools_used"], (
            f"required tool {required_tool} not in tools_used={trace['tools_used']}"
        )

    # Vector path must report queries when the strategy uses one.
    if trace["strategy"] in {"vector_only", "vector_and_sql"}:
        assert trace["vector_queries"], "vector strategy but no vector_queries recorded"

    # Trace must always carry generation provenance.
    assert trace["generation"]["model"] == "stub-generator"
    assert trace["generation"]["finish_reason"] == "stop"

    # The answer must reference only IDs that were actually retrieved.
    answered_ids = set(int(x) for x in re.findall(r"\d+", result.answer))
    retrieved_ids = _all_evidence_ids(trace)
    if answered_ids:
        # Subset check — no fabricated IDs allowed.
        assert answered_ids <= retrieved_ids, (
            f"answer mentions IDs not in retrieved_ids: "
            f"{answered_ids - retrieved_ids} (retrieved={retrieved_ids})"
        )

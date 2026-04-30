"""Unit tests for ``app.core.agent.prompt``."""

from __future__ import annotations

from app.core.agent.prompt import build_prompt


def _formatter(items):
    return "\n".join(f"[id:{x['id']}] {x['msg']}" for x in items)


def test_build_prompt_includes_question_evidence_and_grounding_instructions():
    evidence = [{"id": 1, "msg": "engine warning"}, {"id": 2, "msg": "ATC delay"}]
    prompt = build_prompt(
        "What went wrong?",
        evidence,
        domain_context="flight operations",
        evidence_formatter=_formatter,
    )

    # System instructions ground the model on evidence-only.
    assert "flight operations analyst" in prompt
    assert "ONLY the evidence" in prompt
    assert "Do not invent facts" in prompt

    # Evidence is rendered through the domain formatter.
    assert "[id:1] engine warning" in prompt
    assert "[id:2] ATC delay" in prompt

    # The user question is preserved verbatim.
    assert "QUESTION: What went wrong?" in prompt


def test_build_prompt_handles_empty_evidence():
    prompt = build_prompt(
        "Anything?",
        [],
        domain_context="flight operations",
        evidence_formatter=_formatter,
    )
    # Even with no evidence, the grounding contract still appears.
    assert "ONLY the evidence" in prompt
    assert "EVIDENCE:" in prompt
    assert "QUESTION: Anything?" in prompt


def test_build_prompt_strips_question_whitespace():
    prompt = build_prompt(
        "   spaced-out question   ",
        [],
        domain_context="ops",
        evidence_formatter=_formatter,
    )
    assert "QUESTION: spaced-out question" in prompt

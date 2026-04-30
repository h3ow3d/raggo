"""Generic grounded prompt builder for the raggo agent.

Assembles a prompt from system instructions, evidence, and the user's
question. The domain provides context and evidence formatting.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List


def build_prompt(
    question: str,
    evidence: List[Dict[str, Any]],
    *,
    domain_context: str,
    evidence_formatter: Callable[[List[Dict[str, Any]]], str],
) -> str:
    """Assemble the grounded prompt sent to the generation model.

    Parameters
    ----------
    question : str
        The user's question.
    evidence : list[dict]
        Retrieved evidence items (from vector search and/or SQL tools).
    domain_context : str
        A short phrase describing the domain (e.g. "flight operations").
        Inserted into the system instructions.
    evidence_formatter : Callable
        Domain-specific callable that formats the evidence list into a
        prompt-ready string with citation tags.

    Returns
    -------
    str
        The complete prompt for the generation model.
    """
    system_instructions = (
        f"You are a {domain_context} analyst. Answer the user's question "
        f"using ONLY the evidence provided below. Do not invent facts. If "
        f"the evidence is insufficient to answer, say so clearly. Cite "
        f"specific items by their ID using the format the evidence formatter "
        f"provides. Keep the answer concise (no more than ~6 sentences)."
    )

    formatted_evidence = evidence_formatter(evidence)

    return (
        f"{system_instructions}\n\n"
        f"EVIDENCE:\n{formatted_evidence}\n\n"
        f"QUESTION: {question.strip()}\n\n"
        f"ANSWER:"
    )


__all__ = [
    "build_prompt",
]

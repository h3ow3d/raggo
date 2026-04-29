"""Generic intent classification for the raggo agent.

Provides a dataclass for IntentPlan and a RuleBasedIntentClassifier
that applies a sequence of IntentRule patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.domain import IntentRule


@dataclass
class IntentPlan:
    """The agent's deterministic plan for a question.

    Attributes
    ----------
    strategy:
        ``"sql_only"``, ``"vector_only"``, or ``"vector_and_sql"``. Drives
        which retrieval steps run.
    sql_calls:
        Ordered list of ``(tool_name, kwargs)`` to invoke from the domain's
        safe SQL tools. Tool names are validated against the registry before
        execution.
    vector_query:
        Free-text query to send through pgvector similarity search, or
        ``None`` to skip vector retrieval. Defaults to the original
        question when the intent does not rewrite it.
    vector_filters:
        Optional structured filters to narrow vector search.
    vector_resource:
        Name of the embeddable resource to search. Defaults to the first
        resource in the domain if None.
    notes:
        Human-readable hint explaining why this plan was chosen — useful
        for the trace and for debugging.
    """

    strategy: str
    sql_calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    vector_query: Optional[str] = None
    vector_filters: Optional[Dict[str, Any]] = None
    vector_resource: Optional[str] = None
    notes: str = ""


class RuleBasedIntentClassifier:
    """Deterministic intent classifier using keyword/regex rules.
    
    Applies rules in order; the first matching rule's plan is returned.
    If no rule matches, the default_plan is used.
    """

    def __init__(
        self,
        rules: Tuple[IntentRule, ...],
        default_plan: Callable[[str], IntentPlan],
    ):
        """Initialize the classifier.
        
        Parameters
        ----------
        rules : tuple[IntentRule, ...]
            Ordered sequence of rules. First match wins.
        default_plan : Callable[[str], IntentPlan]
            Fallback plan builder when no rule matches.
        """
        self.rules = rules
        self.default_plan = default_plan

    def classify(self, question: str) -> IntentPlan:
        """Classify a question and return an IntentPlan.
        
        Parameters
        ----------
        question : str
            User question.
        
        Returns
        -------
        IntentPlan
            The plan for this question.
        """
        q = question.strip()
        for rule in self.rules:
            if rule.matches(q):
                return rule.plan(q)
        return self.default_plan(q)


__all__ = [
    "IntentPlan",
    "RuleBasedIntentClassifier",
]

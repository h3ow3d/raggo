"""Prompt fragments for the flights domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


# Domain context inserted into the system prompt
DOMAIN_CONTEXT = "flight operations"


def _format_dt(value: Any) -> str:
    """Format a datetime for the prompt."""
    if isinstance(value, datetime):
        # Drop sub-second precision; readability matters more than
        # precision for the LLM context.
        return value.replace(microsecond=0).isoformat()
    return str(value) if value is not None else ""


def format_evidence(evidence: List[Dict[str, Any]]) -> str:
    """Render evidence as a compact, ID-tagged block for the LLM.
    
    Defines citation tag conventions: [log:<id>], [incident:<id>], [flight:<id>], [airport:<code>]
    """
    if not evidence:
        return "(no evidence retrieved)"

    lines: List[str] = []
    for item in evidence:
        kind = item.get("type")
        if kind == "flight_log" or kind == "flight_logs":
            lines.append(
                f"[log:{item['id']}] flight={item.get('flight_number') or item.get('flight_id')} "
                f"{item.get('origin','?')}->{item.get('destination','?')} "
                f"time={item.get('log_time','')} type={item.get('log_type','')} "
                f"sev={item.get('severity','')} src={item.get('source_system','')} "
                f"msg={item.get('message','')}"
            )
        elif kind == "incident" or kind == "incidents":
            lines.append(
                f"[incident:{item['id']}] flight={item.get('flight_number') or item.get('flight_id')} "
                f"{item.get('origin','?')}->{item.get('destination','?')} "
                f"time={item.get('incident_time','')} sev={item.get('severity','')} "
                f"cat={item.get('category','')} status={item.get('resolution_status','')} "
                f"msg={item.get('message','')}"
            )
        elif kind == "flight" or kind == "flights":
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


__all__ = [
    "DOMAIN_CONTEXT",
    "format_evidence",
]

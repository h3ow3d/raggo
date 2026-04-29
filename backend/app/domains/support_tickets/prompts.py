"""Prompt templates and evidence formatting for the support_tickets domain."""

from typing import List, Dict, Any


DOMAIN_CONTEXT: str = """This is a customer support ticket management system.
You have access to support tickets, messages, and related metadata.
Tickets have priorities (low, medium, high, critical) and statuses (open, in_progress, resolved, closed).
Always cite tickets and messages by ID. Be helpful and precise."""


def format_evidence(evidence: List[Dict[str, Any]]) -> str:
    """Format evidence as a readable text block with inline citations.
    
    Uses notation like [ticket:42] and [message:123].
    """
    if not evidence:
        return "(No supporting evidence)"
    
    lines: List[str] = []
    for item in evidence:
        etype = item.get("type", "unknown")
        
        if etype == "ticket":
            tid = item.get("id")
            subject = item.get("subject", "")
            priority = item.get("priority", "")
            status = item.get("status", "")
            customer = item.get("customer", "")
            opened_at = item.get("opened_at", "")
            closed_at = item.get("closed_at", "")
            
            lines.append(
                f"[ticket:{tid}] {subject} | "
                f"priority={priority}, status={status}, customer={customer}, "
                f"opened={opened_at}"
                + (f", closed={closed_at}" if closed_at else "")
            )
        
        elif etype == "ticket_message":
            mid = item.get("id")
            ticket_id = item.get("ticket_id")
            ticket_subject = item.get("ticket_subject", "")
            ticket_priority = item.get("ticket_priority", "")
            ticket_status = item.get("ticket_status", "")
            author = item.get("author", "")
            message = item.get("message", "")
            
            lines.append(
                f"[message:{mid}] ticket:{ticket_id} ({ticket_subject} | "
                f"priority={ticket_priority}, status={ticket_status}) | "
                f"author={author}: {message}"
            )
        
        elif etype == "priority_count":
            priority = item.get("priority", "")
            count = item.get("count", 0)
            lines.append(f"[priority_count] {priority}={count}")
        
        else:
            lines.append(f"[{etype}] {item}")
    
    return "\n".join(lines)


__all__ = [
    "DOMAIN_CONTEXT",
    "format_evidence",
]

"""Safety tools — screen clinical requests, escalate, audit (PRD 2.6).

Used by the Safety agent early in the LangGraph pipeline (before routing/booking).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.repositories import AuditRepository, EscalationRepository, WorkflowRepository
from safety.classifier import llm_screen_request
from safety.keywords import screen_keywords
from tools._scope import ToolScopeError

_SAFE_ALTERNATIVE = (
    "I can help with appointments, documents, and department routing only. "
    "For medical advice, please contact your clinician or emergency services if urgent."
)


def screen_request(
    state: GraphState,
    db: Session | None = None,  # reserved for future audit hooks
    *,
    raw_request: str | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Screen a patient request for clinical / prescription / emergency language.

    1. Keyword rules (always)
    2. Optional LLM if keywords say safe but use_llm=True (deeper check)

    Returns structured result — never a fixed success string.
    """
    text = (raw_request or state.get("raw_request") or "").strip()
    if not text:
        return {
            "safe": True,
            "flags": [],
            "category": None,
            "safe_alternative": None,
            "reason": "Empty request",
            "stage": "keywords",
        }

    result = screen_keywords(text)
    if not result["safe"]:
        return {
            "safe": False,
            "flags": result["flags"],
            "category": result["category"],
            "matched": result.get("matched"),
            "safe_alternative": _SAFE_ALTERNATIVE,
            "reason": f"Blocked by keyword rules ({result['category']})",
            "stage": "keywords",
        }

    if use_llm:
        llm_result = llm_screen_request(text)
        if llm_result is not None and not llm_result.get("safe", True):
            return {
                "safe": False,
                "flags": llm_result.get("flags") or ["clinical"],
                "category": llm_result.get("category"),
                "safe_alternative": llm_result.get("safe_alternative") or _SAFE_ALTERNATIVE,
                "reason": "Blocked by LLM clinical classifier",
                "stage": "llm",
            }

    return {
        "safe": True,
        "flags": [],
        "category": None,
        "safe_alternative": None,
        "reason": "Administrative request — allowed",
        "stage": "keywords" if not use_llm else "keywords+llm",
    }


def create_escalation(
    state: GraphState,
    db: Session,
    *,
    reason: str,
) -> dict[str, Any]:
    """
    Persist an Escalation for staff HITL review.

    Requires state.workflow_run_id. Does not commit — caller commits.
    """
    workflow_run_id = state.get("workflow_run_id")
    if not workflow_run_id:
        raise ToolScopeError("GraphState missing workflow_run_id for escalation")

    workflow = WorkflowRepository(db).get_by_id(workflow_run_id)
    if workflow is None:
        return {"ok": False, "error": "workflow_not_found", "workflow_run_id": workflow_run_id}

    esc = EscalationRepository(db).create(
        workflow_run_id=workflow_run_id,
        reason=reason,
    )
    return {
        "ok": True,
        "escalation_id": esc.id,
        "workflow_run_id": workflow_run_id,
        "reason": esc.reason,
        "status": esc.status,
    }


def write_audit_event(
    state: GraphState,
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    event_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Append an AuditEvent. actor_id / role always taken from GraphState (PRD §7.1).
    """
    actor_id = state.get("actor_user_id")
    actor_role = state.get("actor_role")
    if not actor_id:
        raise ToolScopeError("GraphState missing actor_user_id for audit")

    meta = dict(event_metadata or {})
    meta.setdefault("role", actor_role)
    if state.get("workflow_run_id"):
        meta.setdefault("workflow_run_id", state["workflow_run_id"])
    if state.get("patient_id"):
        meta.setdefault("patient_id", state["patient_id"])

    event = AuditRepository(db).create(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        event_metadata=meta,
    )
    return {
        "ok": True,
        "audit_event_id": event.id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "actor_id": event.actor_id,
    }


def block_unsafe_action(
    state: GraphState,
    db: Session,
    *,
    screen_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convenience: if request is unsafe, create escalation + audit event.

    Returns combined payload for the safety node.
    """
    screen = screen_result or screen_request(state, db)
    if screen.get("safe", True):
        return {"blocked": False, "screen": screen}

    reason = screen.get("reason") or "Clinical / unsafe request blocked"
    esc = create_escalation(state, db, reason=reason)
    audit = write_audit_event(
        state,
        db,
        action="safety.block",
        entity_type="Escalation",
        entity_id=esc.get("escalation_id"),
        event_metadata={"screen": screen},
    )
    return {
        "blocked": True,
        "screen": screen,
        "escalation": esc,
        "audit": audit,
        "message": screen.get("safe_alternative") or _SAFE_ALTERNATIVE,
    }

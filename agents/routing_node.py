"""Routing agent node — intent + department mapping (Phase 3.4).

Deterministic node: classify via tools, escalate on low confidence.
Also exposes LangChain StructuredTools for optional LLM binding later.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import GraphState, RoutingResult
from tools.routing_tools import classify_intent, lookup_departments
from tools.safety_tools import create_escalation, write_audit_event

ROUTING_PROMPT = load_prompt("routing")


def routing_node(state: GraphState, db: Session) -> GraphState:
    """
    Map ``raw_request`` to administrative intents + department.

    If prior ``safety_result.safe`` is False, refuse to route.
    If classification needs staff review, create an escalation and set HITL flags.

    Does not commit — caller commits the session.
    """
    safety = state.get("safety_result") or {}
    if safety.get("safe") is False:
        return {
            "current_step": "routing",
            "error": "Cannot route after failed safety screen",
            "hitl_required": True,
            "hitl_reason": safety.get("reason") or "Blocked by safety",
        }

    classified = classify_intent(state, db)
    routing_result: RoutingResult = {
        "intents": list(classified.get("intents") or []),
        "department_id": classified.get("department_id"),
        "department_name": classified.get("department_name"),
        "confidence": float(classified.get("confidence") or 0.0),
        "reason": classified.get("reason") or "",
        "needs_staff_review": bool(classified.get("needs_staff_review")),
        "raw_request": classified.get("raw_request") or state.get("raw_request") or "",
    }

    update: GraphState = {
        "current_step": "routing",
        "routing_result": routing_result,
        "administrative_intents": list(routing_result.get("intents") or []),
        "hitl_required": False,
        "hitl_reason": None,
    }

    escalation_id: str | None = None
    if routing_result.get("needs_staff_review"):
        reason = (
            f"Low-confidence routing: {routing_result.get('reason') or 'unclear department'}"
        )
        esc: dict[str, Any] = {"ok": False}
        if state.get("workflow_run_id"):
            esc = create_escalation(state, db, reason=reason)
        update["hitl_required"] = True
        update["hitl_reason"] = reason
        if esc.get("ok") and esc.get("escalation_id"):
            escalation_id = esc["escalation_id"]
            # Keep routing_result intact; HITL carries escalation via hitl_reason
            # and workflow_run_id. Optional: stash id on reason metadata later.
            update["hitl_reason"] = f"{reason} (escalation_id={escalation_id})"

    if state.get("actor_user_id"):
        action = (
            "routing.escalate" if update.get("hitl_required") else "routing.classify"
        )
        write_audit_event(
            state,
            db,
            action=action,
            entity_type="WorkflowRun",
            entity_id=state.get("workflow_run_id"),
            event_metadata={
                "department_name": routing_result.get("department_name"),
                "confidence": routing_result.get("confidence"),
                "intents": routing_result.get("intents"),
                "escalation_id": escalation_id,
            },
        )

    return update


def get_routing_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """Bind routing tools to the current workflow state + DB session."""

    def _lookup() -> list[dict[str, Any]]:
        return lookup_departments(db)

    def _classify(raw_request: str = "") -> dict[str, Any]:
        return classify_intent(
            state,
            db,
            raw_request=raw_request or None,
        )

    def _escalate(reason: str) -> dict[str, Any]:
        return create_escalation(state, db, reason=reason)

    return [
        StructuredTool.from_function(
            func=_lookup,
            name="lookup_departments",
            description="List active departments from the hospital database.",
        ),
        StructuredTool.from_function(
            func=_classify,
            name="classify_intent",
            description=(
                "Classify administrative intent and map to a department using "
                "raw_request (or state.raw_request)."
            ),
        ),
        StructuredTool.from_function(
            func=_escalate,
            name="create_escalation",
            description="Escalate unclear routing for staff department assignment.",
        ),
    ]

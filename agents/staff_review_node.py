"""Staff HITL review node — LangGraph interrupt / resume (Phase 4.2)."""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt
from sqlalchemy.orm import Session

from agents.coordinator_node import _serializable_state
from core.graph_state import GraphState
from db.models import WorkflowStatus
from db.repositories import WorkflowRepository
from tools.safety_tools import write_audit_event


def infer_hitl_source(state: GraphState) -> str:
    """Why the graph paused for staff review."""
    safety = state.get("safety_result") or {}
    if safety.get("safe") is False or safety.get("blocked"):
        return "safety"
    if state.get("appointment_result") is not None:
        return "appointment"
    return "routing"


def staff_review_node(state: GraphState, db: Session) -> GraphState:
    """
    Pause the graph for human review; resume value comes from Command(resume=...).

    Resume payload:
      { "decision": "approve" | "reject", "department_id"?: str,
        "department_name"?: str, "note"?: str }
    """
    source = infer_hitl_source(state)
    workflow_run_id = state.get("workflow_run_id")

    payload = {
        "workflow_run_id": workflow_run_id,
        "patient_id": state.get("patient_id"),
        "source": source,
        "reason": state.get("hitl_reason") or "Staff review required",
        "raw_request": state.get("raw_request") or "",
        "safety_result": state.get("safety_result"),
        "routing_result": state.get("routing_result"),
        "appointment_result": state.get("appointment_result"),
        "administrative_intents": state.get("administrative_intents")
        or (state.get("routing_result") or {}).get("intents"),
        "uploaded_files": [
            {
                "filename": f.get("filename"),
                "mime_type": f.get("mime_type"),
                "size": f.get("size"),
            }
            for f in (state.get("uploaded_files") or [])
            if isinstance(f, dict)
        ],
    }

    if workflow_run_id:
        run = WorkflowRepository(db).get_by_id(workflow_run_id)
        if run is not None:
            # Persist full context so staff UI can review without the checkpointer.
            WorkflowRepository(db).update_state(
                run,
                current_step="staff_review",
                status=WorkflowStatus.WAITING_HITL.value,
                state=_serializable_state(
                    {
                        **state,
                        "hitl_source": source,
                        "hitl_interrupt": payload,
                    }
                ),
            )
            db.flush()

    # First visit: raises interrupt. After Command(resume=...), returns the resume value.
    decision_raw = interrupt(payload)
    decision = _normalize_decision(decision_raw)

    # Prefer staff actor from resume payload (API injects it); fall back to state.
    audit_state: GraphState = dict(state)
    if decision.get("actor_user_id"):
        audit_state["actor_user_id"] = decision["actor_user_id"]
    if decision.get("actor_role"):
        audit_state["actor_role"] = decision["actor_role"]

    update: GraphState = {
        "current_step": "staff_review",
        "hitl_source": source,
        "staff_decision": decision,
        "hitl_required": False,
        "hitl_reason": None,
    }

    if source == "safety":
        # Admin pipeline never books clinical traps — finalize either way.
        note = decision.get("note") or decision.get("decision")
        update["error"] = f"Safety escalation closed by staff ({note})"
        _audit_staff_review(audit_state, db, source=source, decision=decision)
        return update

    if decision.get("decision") == "reject":
        update["error"] = decision.get("note") or "Staff rejected the workflow continuation"
        _audit_staff_review(audit_state, db, source=source, decision=decision)
        return update

    # approve
    if source == "routing":
        routing = dict(state.get("routing_result") or {})
        if decision.get("department_id"):
            routing["department_id"] = decision["department_id"]
        if decision.get("department_name"):
            routing["department_name"] = decision["department_name"]
        routing["needs_staff_review"] = False
        routing["reason"] = (
            routing.get("reason") or ""
        ) + f" | Staff approved routing ({decision.get('note') or 'ok'})"
        update["routing_result"] = routing
        if routing.get("department_id") or routing.get("department_name"):
            # Ensure appointment path has a department
            pass
        elif not routing.get("department_id"):
            update["error"] = "Staff approved routing but did not provide department_id"
            update["staff_decision"] = {**decision, "decision": "reject"}
    elif source == "appointment":
        # Staff allows pipeline to continue without a successful book
        update["appointment_result"] = {
            **(state.get("appointment_result") or {}),
            "ok": True,
            "message": decision.get("note") or "Staff approved continuation without rebook",
            "error": None,
        }

    _audit_staff_review(audit_state, db, source=source, decision=decision)
    return update


def _audit_staff_review(
    state: GraphState,
    db: Session,
    *,
    source: str,
    decision: dict[str, Any],
) -> None:
    if not state.get("actor_user_id"):
        return
    write_audit_event(
        state,
        db,
        action="staff_review.decision",
        entity_type="WorkflowRun",
        entity_id=state.get("workflow_run_id"),
        event_metadata={
            "source": source,
            "decision": decision.get("decision"),
            "note": decision.get("note"),
            "department_id": decision.get("department_id"),
        },
    )


def _normalize_decision(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        decision = dict(raw)
        decision.setdefault("decision", "reject")
        return decision
    if isinstance(raw, str):
        return {"decision": raw}
    return {"decision": "reject", "note": "Invalid resume payload"}

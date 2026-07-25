"""Coordinator agent node — init + finalize (Phase 3.8).

Bookends the pipeline: open the case (patient + WorkflowRun), then close it
with a confirmation assembled from GraphState facts (never invented).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import ConfirmationResult, GraphState
from db.models import WorkflowStatus
from db.repositories import WorkflowRepository
from tools.patient_tools import get_or_create_patient
from tools.safety_tools import write_audit_event

COORDINATOR_PROMPT = load_prompt("coordinator")


def _serializable_state(state: GraphState) -> dict[str, Any]:
    """JSON-safe snapshot for WorkflowRun.state (drop file bytes)."""
    out: dict[str, Any] = {}
    for key, value in dict(state).items():
        if key == "uploaded_files" and isinstance(value, list):
            out[key] = [
                {
                    "filename": f.get("filename"),
                    "mime_type": f.get("mime_type"),
                    "size": f.get("size") or (
                        len(f["content"]) if isinstance(f.get("content"), (bytes, bytearray)) else None
                    ),
                }
                for f in value
                if isinstance(f, dict)
            ]
        else:
            out[key] = value
    return out


def coordinator_init(state: GraphState, db: Session) -> GraphState:
    """
    Ensure PatientProfile + WorkflowRun exist; audit workflow start.

    Expects ``actor_user_id`` and ``actor_role`` (from API / caller).
    Does not commit — caller commits the session.
    """
    if not state.get("actor_user_id") or not state.get("actor_role"):
        return {
            "current_step": "coordinator_init",
            "error": "GraphState missing actor_user_id or actor_role",
            "confirmation": {"ok": False, "summary": "Cannot start workflow without actor identity"},
        }

    patient = get_or_create_patient(state, db)
    patient_id = patient["patient_id"]
    merged: GraphState = {**state, "patient_id": patient_id}

    workflows = WorkflowRepository(db)
    workflow_run_id = state.get("workflow_run_id")
    if workflow_run_id:
        run = workflows.get_by_id(workflow_run_id)
        if run is None:
            run = workflows.create(
                patient_id=patient_id,
                current_step="coordinator_init",
                state=_serializable_state(merged),
                status=WorkflowStatus.RUNNING.value,
            )
            workflow_run_id = run.id
        else:
            workflows.update_state(
                run,
                current_step="coordinator_init",
                status=WorkflowStatus.RUNNING.value,
            )
    else:
        run = workflows.create(
            patient_id=patient_id,
            current_step="coordinator_init",
            state=_serializable_state(merged),
            status=WorkflowStatus.RUNNING.value,
        )
        workflow_run_id = run.id

    merged["workflow_run_id"] = workflow_run_id
    write_audit_event(
        merged,
        db,
        action="workflow.start",
        entity_type="WorkflowRun",
        entity_id=workflow_run_id,
        event_metadata={"raw_request": (state.get("raw_request") or "")[:500]},
    )

    return {
        "patient_id": patient_id,
        "workflow_run_id": workflow_run_id,
        "current_step": "coordinator_init",
        "error": None,
    }


def _build_confirmation(state: GraphState) -> ConfirmationResult:
    """Assemble patient-facing summary from state facts only."""
    workflow_run_id = state.get("workflow_run_id")
    safety = state.get("safety_result") or {}
    routing = state.get("routing_result") or {}
    appointment = state.get("appointment_result") or {}
    documents = state.get("document_result") or {}
    followup = state.get("followup_result") or {}

    if safety.get("safe") is False or safety.get("blocked"):
        msg = safety.get("message") or safety.get("safe_alternative") or (
            "Your request was escalated for staff review. "
            "PulseDesk only handles administrative tasks."
        )
        return {
            "ok": False,
            "summary": msg,
            "workflow_run_id": workflow_run_id,
            "appointment_id": None,
            "department_name": None,
            "doctor_name": None,
            "start_time": None,
            "documents_stored": len(documents.get("stored") or []),
            "reminders_scheduled": len(followup.get("reminder_ids") or []),
        }

    parts: list[str] = []
    dept = routing.get("department_name")
    if dept:
        parts.append(f"Department: {dept}.")

    if appointment.get("ok") and appointment.get("appointment_id"):
        doctor = appointment.get("doctor_name") or "your clinician"
        when = appointment.get("start_time") or "the scheduled time"
        parts.append(f"Appointment booked with {doctor} at {when}.")
    elif appointment.get("error"):
        parts.append(
            f"Appointment could not be completed ({appointment.get('error')})."
        )

    stored_n = len(documents.get("stored") or [])
    if stored_n:
        parts.append(f"Documents stored: {stored_n}.")
    missing = documents.get("missing") or []
    if missing:
        parts.append("Still needed: " + ", ".join(missing) + ".")

    rem_n = len(followup.get("reminder_ids") or [])
    if rem_n:
        parts.append(f"Reminders/follow-ups scheduled: {rem_n}.")
    if followup.get("notification_status"):
        parts.append(f"Confirmation email status: {followup['notification_status']}.")

    if state.get("hitl_required"):
        parts.append(
            "Staff review is required before this request can fully complete."
        )

    if not parts:
        parts.append(
            "Administrative request recorded. No appointment or documents were processed."
        )

    return {
        "ok": True,
        "summary": " ".join(parts),
        "workflow_run_id": workflow_run_id,
        "appointment_id": appointment.get("appointment_id"),
        "department_name": dept,
        "doctor_name": appointment.get("doctor_name"),
        "start_time": appointment.get("start_time"),
        "documents_stored": stored_n,
        "reminders_scheduled": rem_n,
    }


def coordinator_finalize(state: GraphState, db: Session) -> GraphState:
    """
    Build confirmation from state, persist WorkflowRun snapshot, audit finalize.

    Does not commit — caller commits the session.
    """
    confirmation = _build_confirmation(state)

    safety = state.get("safety_result") or {}
    if safety.get("safe") is False or safety.get("blocked"):
        status = WorkflowStatus.BLOCKED_SAFETY.value
    elif state.get("hitl_required"):
        status = WorkflowStatus.WAITING_HITL.value
    elif state.get("error") and not confirmation.get("ok"):
        status = WorkflowStatus.FAILED.value
    else:
        status = WorkflowStatus.COMPLETED.value

    snapshot = _serializable_state({**state, "confirmation": confirmation})
    workflow_run_id = state.get("workflow_run_id")
    if workflow_run_id:
        run = WorkflowRepository(db).get_by_id(workflow_run_id)
        if run is not None:
            WorkflowRepository(db).update_state(
                run,
                current_step="coordinator_finalize",
                state=snapshot,
                status=status,
            )

    if state.get("actor_user_id"):
        write_audit_event(
            state,
            db,
            action="workflow.finalize",
            entity_type="WorkflowRun",
            entity_id=workflow_run_id,
            event_metadata={"status": status, "ok": confirmation.get("ok")},
        )

    return {
        "current_step": "coordinator_finalize",
        "confirmation": confirmation,
    }


def get_coordinator_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """Bind coordinator helpers for optional LLM use."""

    def _patient() -> dict[str, Any]:
        return get_or_create_patient(state, db)

    def _audit(action: str, entity_type: str, entity_id: str = "") -> dict[str, Any]:
        return write_audit_event(
            state,
            db,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id or None,
        )

    return [
        StructuredTool.from_function(
            func=_patient,
            name="get_or_create_patient",
            description="Ensure a PatientProfile exists for the workflow actor/patient.",
        ),
        StructuredTool.from_function(
            func=_audit,
            name="write_audit_event",
            description="Append an audit event for workflow lifecycle actions.",
        ),
    ]

"""Workflow orchestration service — start / resume LangGraph runs (Phase 4.4).

Injects ``actor_user_id`` + ``actor_role`` into initial GraphState (authoritative;
callers should not rely on client-supplied identity). Uses durable SqliteSaver
by default; tests may pass MemorySaver.

Phase 7.6: unhandled graph/LLM errors mark ``WorkflowRun.status = FAILED`` and
preserve state for inspection (instead of leaving a zombie RUNNING row).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session

from core.graph_state import GraphState, UploadedFile
from core.pipeline import compile_workflow, get_checkpointer
from core.tracing import build_run_config, configure_langsmith
from db.models import WorkflowStatus
from db.repositories import WorkflowRepository
from services.workflow_events import emit_workflow_event
from tools.patient_tools import get_or_create_patient
from tools.safety_tools import write_audit_event

logger = logging.getLogger("agentcare.workflow")

# Tests inject MemorySaver here so start + resume share one checkpointer.
_checkpointer_override: Any | None = None


def set_checkpointer_override(checkpointer: Any | None) -> None:
    """Test helper — inject MemorySaver for isolated HTTP / service tests."""
    global _checkpointer_override
    _checkpointer_override = checkpointer


def get_checkpointer_override() -> Any | None:
    return _checkpointer_override


def start_workflow(
    db: Session,
    *,
    actor_user_id: str,
    actor_role: str,
    raw_request: str,
    uploaded_files: list[UploadedFile] | list[dict[str, Any]] | None = None,
    patient_id: str | None = None,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """
    Start an AgentCare workflow for the authenticated actor.

    Always sets ``actor_user_id`` / ``actor_role`` from arguments (JWT later).
    ``thread_id`` for the checkpointer equals ``workflow_run_id``.
    Attaches LangSmith tags/metadata when tracing is configured (Phase 4.5).

    Returns a package: ``status`` (completed|interrupted|failed), ids, state,
    interrupt payload. Does not commit — caller commits the session.
    """
    if not actor_user_id or not actor_role:
        raise ValueError("actor_user_id and actor_role are required")

    configure_langsmith()

    initial: GraphState = {
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "raw_request": raw_request or "",
        "uploaded_files": list(uploaded_files or []),
    }
    if patient_id:
        initial["patient_id"] = patient_id

    # Patient must exist before WorkflowRun (FK)
    patient = get_or_create_patient(initial, db)
    initial["patient_id"] = patient["patient_id"]

    workflow_run_id = str(uuid.uuid4())
    WorkflowRepository(db).create(
        id=workflow_run_id,
        patient_id=initial["patient_id"],
        current_step="coordinator_init",
        state={"thread_id": workflow_run_id},
        status=WorkflowStatus.RUNNING.value,
    )
    initial["workflow_run_id"] = workflow_run_id
    db.flush()

    emit_workflow_event(
        workflow_run_id,
        event_type="started",
        current_step="coordinator_init",
        status=WorkflowStatus.RUNNING.value,
    )

    config = build_run_config(
        thread_id=workflow_run_id,
        workflow_run_id=workflow_run_id,
        patient_id=initial["patient_id"],
        actor_role=actor_role,
        actor_user_id=actor_user_id,
    )
    if checkpointer is None:
        checkpointer = _checkpointer_override

    try:
        result = _invoke(db, initial, config, checkpointer=checkpointer)
    except Exception as exc:
        if _is_graph_interrupt(exc):
            raise
        logger.exception("Workflow %s failed during start: %s", workflow_run_id, exc)
        return _package_failed(
            db,
            workflow_run_id=workflow_run_id,
            patient_id=initial["patient_id"],
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            error=exc,
            current_step="coordinator_init",
        )

    return _package(result, workflow_run_id=workflow_run_id)


def resume_workflow(
    db: Session,
    *,
    workflow_run_id: str,
    decision: str,
    department_id: str | None = None,
    department_name: str | None = None,
    note: str | None = None,
    checkpointer: Any | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
) -> dict[str, Any]:
    """
    Resume a paused HITL workflow with a staff decision.

    ``thread_id`` is ``workflow_run_id`` (set at start_workflow).
    Does not commit — caller commits the session.
    """
    configure_langsmith()

    run = WorkflowRepository(db).get_by_id(workflow_run_id)
    if run is None:
        raise ValueError(f"WorkflowRun not found: {workflow_run_id}")

    payload: dict[str, Any] = {"decision": decision}
    if department_id:
        payload["department_id"] = department_id
    if department_name:
        payload["department_name"] = department_name
    if note:
        payload["note"] = note
    # So staff_review can attribute AuditEvent.actor_id to the reviewing staff.
    if actor_user_id:
        payload["actor_user_id"] = actor_user_id
    if actor_role:
        payload["actor_role"] = actor_role

    config = build_run_config(
        thread_id=workflow_run_id,
        workflow_run_id=workflow_run_id,
        patient_id=run.patient_id,
        actor_role=actor_role,
        actor_user_id=actor_user_id,
        extra_tags=["hitl_resume"],
        extra_metadata={"hitl_decision": decision},
    )
    if checkpointer is None:
        checkpointer = _checkpointer_override

    try:
        result = _invoke(
            db,
            Command(resume=payload),
            config,
            checkpointer=checkpointer,
        )
    except Exception as exc:
        if _is_graph_interrupt(exc):
            raise
        logger.exception("Workflow %s failed during resume: %s", workflow_run_id, exc)
        return _package_failed(
            db,
            workflow_run_id=workflow_run_id,
            patient_id=run.patient_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            error=exc,
            current_step=run.current_step or "staff_review",
        )

    return _package(result, workflow_run_id=workflow_run_id)


def _invoke(
    db: Session,
    input_value: Any,
    config: dict[str, Any],
    *,
    checkpointer: Any | None,
) -> dict[str, Any]:
    if checkpointer is not None:
        graph = compile_workflow(db, checkpointer=checkpointer)
        return graph.invoke(input_value, config)

    with get_checkpointer() as saver:
        graph = compile_workflow(db, checkpointer=saver)
        return graph.invoke(input_value, config)


def _is_graph_interrupt(exc: BaseException) -> bool:
    """LangGraph HITL interrupts must not be treated as workflow failures."""
    name = type(exc).__name__
    if name in {"GraphInterrupt", "GraphBubbleUp"}:
        return True
    try:
        from langgraph.errors import GraphInterrupt

        return isinstance(exc, GraphInterrupt)
    except Exception:
        return False


def mark_workflow_failed(
    db: Session,
    *,
    workflow_run_id: str,
    error: BaseException | str,
    current_step: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    """
    Persist ``FAILED`` + error details on the WorkflowRun (PRD §12 / 7.6).

    State snapshot is preserved/merged so staff can inspect what happened.
    Does not commit — caller commits.
    """
    run = WorkflowRepository(db).get_by_id(workflow_run_id)
    if run is None:
        return

    err_text = str(error)
    err_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
    prev = dict(run.state or {})
    prev["error"] = err_text
    prev["error_type"] = err_type

    WorkflowRepository(db).update_state(
        run,
        current_step=current_step or run.current_step or "failed",
        state=prev,
        status=WorkflowStatus.FAILED.value,
    )

    if actor_user_id:
        try:
            write_audit_event(
                {
                    "actor_user_id": actor_user_id,
                    "actor_role": actor_role,
                    "workflow_run_id": workflow_run_id,
                    "patient_id": run.patient_id,
                },
                db,
                action="workflow.fail",
                entity_type="WorkflowRun",
                entity_id=workflow_run_id,
                event_metadata={"error": err_text, "error_type": err_type},
            )
        except Exception as audit_exc:  # noqa: BLE001 — never mask original failure
            logger.warning("Could not write workflow.fail audit: %s", audit_exc)


def _package_failed(
    db: Session,
    *,
    workflow_run_id: str,
    patient_id: str | None,
    actor_user_id: str | None,
    actor_role: str | None,
    error: BaseException,
    current_step: str | None,
) -> dict[str, Any]:
    mark_workflow_failed(
        db,
        workflow_run_id=workflow_run_id,
        error=error,
        current_step=current_step,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    packaged = {
        "status": "failed",
        "workflow_run_id": workflow_run_id,
        "patient_id": patient_id,
        "current_step": current_step,
        "state": {"error": str(error), "error_type": type(error).__name__},
        "interrupt": None,
        "confirmation": None,
        "hitl_required": False,
        "error": str(error),
    }
    emit_workflow_event(
        workflow_run_id,
        event_type="failed",
        current_step=current_step,
        status=WorkflowStatus.FAILED.value,
        confirmation=None,
        interrupt=None,
        extra={"error": str(error)},
    )
    return packaged


def _package(result: dict[str, Any], *, workflow_run_id: str) -> dict[str, Any]:
    interrupts = result.get("__interrupt__") or ()
    interrupted = bool(interrupts)
    interrupt_payload = None
    if interrupted:
        first = interrupts[0]
        interrupt_payload = getattr(first, "value", first)

    packaged = {
        "status": "interrupted" if interrupted else "completed",
        "workflow_run_id": result.get("workflow_run_id") or workflow_run_id,
        "patient_id": result.get("patient_id"),
        "current_step": result.get("current_step"),
        "state": result,
        "interrupt": interrupt_payload,
        "confirmation": result.get("confirmation"),
        "hitl_required": bool(result.get("hitl_required")) if interrupted else False,
    }
    emit_workflow_event(
        packaged["workflow_run_id"],
        event_type=packaged["status"],
        current_step=packaged.get("current_step"),
        status=packaged["status"],
        confirmation=packaged.get("confirmation"),
        interrupt=interrupt_payload if isinstance(interrupt_payload, dict) else None,
        extra={"hitl_required": packaged["hitl_required"]},
    )
    return packaged

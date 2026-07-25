"""Staff API — request queue, escalations, HITL resume (Phase 5.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.schemas import (
    AuditEventOut,
    EscalationDetail,
    EscalationOut,
    ResolveEscalationBody,
    ResumeWorkflowBody,
    ResumeWorkflowResponse,
    WorkflowSummary,
)
from auth.dependencies import require_role
from db.models import EscalationStatus, User
from db.repositories import (
    AuditRepository,
    EscalationRepository,
    PatientRepository,
    UserRepository,
    WorkflowRepository,
)
from db.session import get_db
from services.workflow_service import resume_workflow

router = APIRouter(tags=["staff"])


def _patient_contact(db: Session, patient_id: str | None) -> tuple[str | None, str | None]:
    if not patient_id:
        return None, None
    profile = PatientRepository(db).get_by_id(patient_id)
    if profile is None:
        return None, None
    user = UserRepository(db).get_by_id(profile.user_id)
    if user is None:
        return None, None
    return user.name, user.email


def _workflow_to_summary(workflow) -> WorkflowSummary:
    state = workflow.state or {}
    return WorkflowSummary(
        id=workflow.id,
        patient_id=workflow.patient_id,
        status=workflow.status,
        current_step=workflow.current_step,
        raw_request=state.get("raw_request"),
        confirmation=state.get("confirmation"),
        safety_result=state.get("safety_result"),
        routing_result=state.get("routing_result"),
        appointment_result=state.get("appointment_result"),
        document_result=state.get("document_result"),
        followup_result=state.get("followup_result"),
        hitl_required=state.get("hitl_required"),
        hitl_reason=state.get("hitl_reason"),
        hitl_source=state.get("hitl_source"),
        error=state.get("error"),
    )


def _escalation_out(esc, db: Session) -> EscalationOut:
    run = WorkflowRepository(db).get_by_id(esc.workflow_run_id)
    state = (run.state if run else None) or {}
    interrupt = state.get("hitl_interrupt") or {}
    raw = state.get("raw_request") or interrupt.get("raw_request") or ""
    preview = (raw[:160] + "…") if len(raw) > 160 else (raw or None)
    name, _email = _patient_contact(db, run.patient_id if run else None)
    return EscalationOut(
        id=esc.id,
        workflow_run_id=esc.workflow_run_id,
        reason=esc.reason,
        status=esc.status,
        reviewed_by=esc.reviewed_by,
        created_at=esc.created_at.isoformat() if esc.created_at else None,
        patient_id=run.patient_id if run else None,
        patient_name=name,
        raw_request_preview=preview,
        hitl_source=state.get("hitl_source") or interrupt.get("source"),
    )


def _escalation_detail(esc, db: Session) -> EscalationDetail:
    base = _escalation_out(esc, db)
    run = WorkflowRepository(db).get_by_id(esc.workflow_run_id)
    state = (run.state if run else None) or {}
    interrupt = state.get("hitl_interrupt") or {}
    name, email = _patient_contact(db, run.patient_id if run else None)
    raw = state.get("raw_request") or interrupt.get("raw_request") or ""
    data = base.model_dump()
    data.update(
        {
            "patient_name": name or base.patient_name,
            "patient_email": email,
            "workflow_status": run.status if run else None,
            "current_step": run.current_step if run else None,
            "raw_request": raw or None,
            "hitl_reason": state.get("hitl_reason")
            or interrupt.get("reason")
            or esc.reason,
            "administrative_intents": state.get("administrative_intents")
            or interrupt.get("administrative_intents"),
            "safety_result": state.get("safety_result") or interrupt.get("safety_result"),
            "routing_result": state.get("routing_result")
            or interrupt.get("routing_result"),
            "appointment_result": state.get("appointment_result")
            or interrupt.get("appointment_result"),
            "uploaded_files": state.get("uploaded_files")
            or interrupt.get("uploaded_files"),
        }
    )
    return EscalationDetail(**data)


def _package_resume(out: dict, *, escalation_id: str | None = None, escalation_status: str | None = None) -> ResumeWorkflowResponse:
    return ResumeWorkflowResponse(
        status=out["status"],
        workflow_run_id=out["workflow_run_id"],
        patient_id=out.get("patient_id"),
        current_step=out.get("current_step"),
        confirmation=out.get("confirmation"),
        interrupt=out.get("interrupt"),
        hitl_required=bool(out.get("hitl_required")),
        escalation_id=escalation_id,
        escalation_status=escalation_status,
    )


@router.get("/staff/requests", response_model=list[WorkflowSummary])
def list_staff_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> list[WorkflowSummary]:
    """All workflow runs (optional status filter). STAFF / ADMIN."""
    rows = WorkflowRepository(db).list_all(status=status_filter, limit=limit)
    return [_workflow_to_summary(r) for r in rows]


@router.get("/staff/escalations", response_model=list[EscalationOut])
def list_escalations(
    pending_only: bool = Query(default=True),
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> list[EscalationOut]:
    """Pending escalations by default; set pending_only=false for all."""
    repo = EscalationRepository(db)
    rows = repo.list_pending() if pending_only else repo.list_all()
    return [_escalation_out(e, db) for e in rows]


@router.get("/staff/escalations/{escalation_id}", response_model=EscalationDetail)
def get_escalation(
    escalation_id: str,
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> EscalationDetail:
    """Escalation + patient request / routing / safety context for HITL review."""
    esc = EscalationRepository(db).get_by_id(escalation_id)
    if esc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation not found")
    return _escalation_detail(esc, db)


@router.post(
    "/staff/escalations/{escalation_id}/resolve",
    response_model=ResumeWorkflowResponse,
)
def resolve_escalation(
    escalation_id: str,
    body: ResolveEscalationBody,
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> ResumeWorkflowResponse:
    """Approve/reject escalation, mark row, and resume the paused graph."""
    esc = EscalationRepository(db).get_by_id(escalation_id)
    if esc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation not found")
    if esc.status != EscalationStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Escalation is not PENDING (status={esc.status})",
        )

    new_status = (
        EscalationStatus.APPROVED.value
        if body.decision == "approve"
        else EscalationStatus.REJECTED.value
    )
    EscalationRepository(db).resolve(
        esc,
        status=new_status,
        reviewed_by=user.id,
    )
    AuditRepository(db).create(
        actor_id=user.id,
        action=f"escalation_{body.decision}",
        entity_type="escalation",
        entity_id=esc.id,
        event_metadata={
            "workflow_run_id": esc.workflow_run_id,
            "note": body.note,
            "department_id": body.department_id,
        },
    )

    try:
        out = resume_workflow(
            db,
            workflow_run_id=esc.workflow_run_id,
            decision=body.decision,
            department_id=body.department_id,
            department_name=body.department_name,
            note=body.note,
            actor_user_id=user.id,
            actor_role=user.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not resume workflow: {exc}",
        ) from exc

    db.commit()
    return _package_resume(out, escalation_id=esc.id, escalation_status=new_status)


@router.post("/workflows/{workflow_id}/resume", response_model=ResumeWorkflowResponse)
def resume_workflow_route(
    workflow_id: str,
    body: ResumeWorkflowBody,
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> ResumeWorkflowResponse:
    """LangGraph Command(resume=…) for a WAITING_HITL run (with or without Escalation row)."""
    run = WorkflowRepository(db).get_by_id(workflow_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    AuditRepository(db).create(
        actor_id=user.id,
        action=f"workflow_resume_{body.decision}",
        entity_type="workflow_run",
        entity_id=workflow_id,
        event_metadata={
            "note": body.note,
            "department_id": body.department_id,
        },
    )

    try:
        out = resume_workflow(
            db,
            workflow_run_id=workflow_id,
            decision=body.decision,
            department_id=body.department_id,
            department_name=body.department_name,
            note=body.note,
            actor_user_id=user.id,
            actor_role=user.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not resume workflow: {exc}",
        ) from exc

    db.commit()
    return _package_resume(out)


@router.get("/staff/audit", response_model=list[AuditEventOut])
def list_audit_events(
    actor_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> list[AuditEventOut]:
    """Read-only audit trail with optional filters (newest first)."""
    rows = AuditRepository(db).list_filtered(
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        limit=limit,
    )
    return [
        AuditEventOut(
            id=e.id,
            actor_id=e.actor_id,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            metadata=e.event_metadata or {},
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in rows
    ]

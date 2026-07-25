"""Patient API — requests, appointments, documents, reminders (Phase 5.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api.schemas import (
    AppointmentOut,
    DocumentOut,
    ReminderOut,
    SubmitRequestResponse,
    WorkflowSummary,
)
from auth.dependencies import get_current_user, require_role
from auth.ownership import (
    assert_patient_owns_appointment,
    assert_patient_owns_workflow,
)
from core.graph_state import GraphState
from db.models import Appointment, User
from db.repositories import (
    AppointmentRepository,
    DocumentRepository,
    DoctorRepository,
    PatientRepository,
    ReminderRepository,
    SlotRepository,
    WorkflowRepository,
)
from db.session import get_db
from services.workflow_service import start_workflow
from tools.appointment_tools import cancel_appointment
from tools.document_tools import store_document

router = APIRouter(tags=["patient"])


def _appointment_out(db: Session, appt: Appointment) -> AppointmentOut:
    doctor = DoctorRepository(db).get_by_id(appt.doctor_id)
    slot = SlotRepository(db).get_by_id(appt.slot_id)
    return AppointmentOut(
        id=appt.id,
        patient_id=appt.patient_id,
        doctor_id=appt.doctor_id,
        slot_id=appt.slot_id,
        status=appt.status,
        reason=appt.reason,
        doctor_name=doctor.name if doctor else None,
        start_time=slot.start_time if slot else None,
        end_time=slot.end_time if slot else None,
    )


def _patient_id(user: User, db: Session) -> str:
    profile = PatientRepository(db).get_by_user_id(user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No patient profile for this user",
        )
    return profile.id


def _workflow_to_summary(workflow) -> WorkflowSummary:
    state = workflow.state or {}
    return WorkflowSummary(
        id=workflow.id,
        patient_id=workflow.patient_id,
        status=workflow.status,
        current_step=workflow.current_step,
        confirmation=state.get("confirmation"),
        safety_result=state.get("safety_result"),
        routing_result=state.get("routing_result"),
        appointment_result=state.get("appointment_result"),
        document_result=state.get("document_result"),
        followup_result=state.get("followup_result"),
        hitl_required=state.get("hitl_required"),
        hitl_reason=state.get("hitl_reason"),
        error=state.get("error"),
    )


@router.post("/requests", response_model=SubmitRequestResponse)
async def submit_request(
    raw_request: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> SubmitRequestResponse:
    """Start an admin workflow for the logged-in patient (multipart)."""
    uploaded: list[dict[str, Any]] = []
    for f in files or []:
        content = await f.read()
        uploaded.append(
            {
                "filename": f.filename or "upload.bin",
                "content": content,
                "mime_type": f.content_type or "application/octet-stream",
            }
        )

    out = start_workflow(
        db,
        actor_user_id=user.id,
        actor_role=user.role,
        raw_request=raw_request,
        uploaded_files=uploaded,
    )
    db.commit()
    return SubmitRequestResponse(
        status=out["status"],
        workflow_run_id=out["workflow_run_id"],
        patient_id=out.get("patient_id"),
        current_step=out.get("current_step"),
        confirmation=out.get("confirmation"),
        interrupt=out.get("interrupt"),
        hitl_required=bool(out.get("hitl_required")),
        error=out.get("error"),
    )


@router.get("/requests/{workflow_id}", response_model=WorkflowSummary)
def get_request(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkflowSummary:
    """Patient owns own run; STAFF/ADMIN may read any (ownership helper)."""
    workflow = WorkflowRepository(db).get_by_id(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    assert_patient_owns_workflow(user, workflow, db)
    return _workflow_to_summary(workflow)


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> list[AppointmentOut]:
    patient_id = _patient_id(user, db)
    rows = AppointmentRepository(db).list_for_patient(patient_id)
    return [_appointment_out(db, r) for r in rows]


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_own_appointment(
    appointment_id: str,
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    appt = AppointmentRepository(db).get_by_id(appointment_id)
    if appt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    assert_patient_owns_appointment(user, appt, db)

    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": user.role,
        "patient_id": appt.patient_id,
    }
    result = cancel_appointment(state, db, appointment_id=appointment_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message") or result.get("error") or "Cancel failed",
        )
    db.commit()
    refreshed = AppointmentRepository(db).get_by_id(appointment_id)
    assert refreshed is not None
    return _appointment_out(db, refreshed)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    patient_id = _patient_id(user, db)
    rows = DocumentRepository(db).list_for_patient(patient_id)
    return [DocumentOut.model_validate(r) for r in rows]


@router.post("/documents/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> DocumentOut:
    patient_id = _patient_id(user, db)
    content = await file.read()
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": user.role,
        "patient_id": patient_id,
    }
    result = store_document(
        state,
        db,
        filename=file.filename or "upload.bin",
        content=content,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message") or result.get("error") or "Upload failed",
        )
    db.commit()
    doc = DocumentRepository(db).get_by_id(result["document_id"])
    assert doc is not None
    return DocumentOut.model_validate(doc)


@router.get("/reminders", response_model=list[ReminderOut])
def list_reminders(
    user: User = Depends(require_role("PATIENT")),
    db: Session = Depends(get_db),
) -> list[ReminderOut]:
    patient_id = _patient_id(user, db)
    rows = ReminderRepository(db).list_for_patient(patient_id)
    out: list[ReminderOut] = []
    for r in rows:
        out.append(
            ReminderOut(
                id=r.id,
                patient_id=r.patient_id,
                appointment_id=r.appointment_id,
                reminder_type=r.reminder_type,
                scheduled_at=r.scheduled_at.isoformat() if r.scheduled_at else None,
                status=r.status,
            )
        )
    return out

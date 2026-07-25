"""Pydantic schemas for patient + staff APIs (Phase 5.3 / 5.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubmitRequestBody(BaseModel):
    """JSON fallback when no multipart files are sent."""

    raw_request: str = Field(min_length=1, max_length=8000)


class WorkflowSummary(BaseModel):
    id: str
    patient_id: str
    status: str
    current_step: str | None = None
    raw_request: str | None = None
    confirmation: dict | None = None
    safety_result: dict | None = None
    routing_result: dict | None = None
    appointment_result: dict | None = None
    document_result: dict | None = None
    followup_result: dict | None = None
    hitl_required: bool | None = None
    hitl_reason: str | None = None
    hitl_source: str | None = None
    error: str | None = None


class SubmitRequestResponse(BaseModel):
    status: str  # completed | interrupted | failed
    workflow_run_id: str
    patient_id: str | None = None
    current_step: str | None = None
    confirmation: dict | None = None
    interrupt: dict | None = None
    hitl_required: bool = False
    error: str | None = None


class AppointmentOut(BaseModel):
    id: str
    patient_id: str
    doctor_id: str
    slot_id: str
    status: str
    reason: str | None = None
    doctor_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: str
    patient_id: str
    document_type: str
    file_path: str
    checksum: str

    model_config = {"from_attributes": True}


class ReminderOut(BaseModel):
    id: str
    patient_id: str
    appointment_id: str | None = None
    reminder_type: str
    scheduled_at: str | None = None
    status: str

    model_config = {"from_attributes": True}


# --- Staff (Phase 5.4) ---


class EscalationOut(BaseModel):
    id: str
    workflow_run_id: str
    reason: str
    status: str
    reviewed_by: str | None = None
    created_at: str | None = None
    # Optional context for staff queue (populated when workflow snapshot exists)
    patient_id: str | None = None
    patient_name: str | None = None
    raw_request_preview: str | None = None
    hitl_source: str | None = None

    model_config = {"from_attributes": True}


class EscalationDetail(EscalationOut):
    """Full HITL review package for staff action."""

    patient_email: str | None = None
    workflow_status: str | None = None
    current_step: str | None = None
    raw_request: str | None = None
    hitl_reason: str | None = None
    administrative_intents: list[str] | None = None
    safety_result: dict | None = None
    routing_result: dict | None = None
    appointment_result: dict | None = None
    uploaded_files: list[dict] | None = None


class ResumeWorkflowBody(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = None
    department_id: str | None = None
    department_name: str | None = None


class ResolveEscalationBody(ResumeWorkflowBody):
    """Same payload as resume — also marks the Escalation row."""


class ResumeWorkflowResponse(BaseModel):
    status: str
    workflow_run_id: str
    patient_id: str | None = None
    current_step: str | None = None
    confirmation: dict | None = None
    interrupt: dict | None = None
    hitl_required: bool = False
    escalation_id: str | None = None
    escalation_status: str | None = None


class AuditEventOut(BaseModel):
    id: str
    actor_id: str
    action: str
    entity_type: str
    entity_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str | None = None


# --- Admin reference data (Phase 5.5) ---


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    active: bool = True


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    active: bool | None = None


class DepartmentOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    active: bool

    model_config = {"from_attributes": True}


class DoctorCreate(BaseModel):
    department_id: str
    name: str = Field(min_length=1, max_length=200)
    active: bool = True


class DoctorUpdate(BaseModel):
    department_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class DoctorOut(BaseModel):
    id: str
    department_id: str
    name: str
    active: bool

    model_config = {"from_attributes": True}


class SlotCreate(BaseModel):
    doctor_id: str
    start_time: datetime
    end_time: datetime
    status: str = "AVAILABLE"


class SlotUpdate(BaseModel):
    doctor_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str | None = None


class SlotOut(BaseModel):
    id: str
    doctor_id: str
    start_time: datetime
    end_time: datetime
    status: str

    model_config = {"from_attributes": True}

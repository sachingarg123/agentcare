"""SQLAlchemy 2.0 ORM models — AgentCare domain (PRD §8).

Phase 1.1 only: table definitions + relationships.
Session / Alembic / repositories come in later 1.x tasks.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Shared declarative base for all AgentCare tables."""


# ---------------------------------------------------------------------------
# Enums (stored as strings in SQLite for simple Alembic + readability)
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    PATIENT = "PATIENT"
    STAFF = "STAFF"
    ADMIN = "ADMIN"


class SlotStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    BOOKED = "BOOKED"
    BLOCKED = "BLOCKED"


class AppointmentStatus(str, enum.Enum):
    BOOKED = "BOOKED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class WorkflowStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_HITL = "WAITING_HITL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


class ReminderStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EscalationStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"


class DocumentType(str, enum.Enum):
    """Aligned with PRD §14.3 document classification types."""

    ECG = "ECG"
    BLOOD_REPORT = "BLOOD_REPORT"
    RADIOLOGY = "RADIOLOGY"
    REFERRAL_LETTER = "REFERRAL_LETTER"
    ID_PROOF = "ID_PROOF"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Identity & patients
# ---------------------------------------------------------------------------


class User(Base):
    """Login identity. Role drives route-level RBAC."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.PATIENT.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient_profile: Mapped[Optional["PatientProfile"]] = relationship(
        back_populates="user", uselist=False
    )


class PatientProfile(Base):
    """Clinical-admin demographics; 1:1 with a PATIENT user."""

    __tablename__ = "patient_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(40), default="en", nullable=False)
    emergency_contact: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship(back_populates="patient_profile")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")
    documents: Mapped[list["PatientDocument"]] = relationship(back_populates="patient")
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="patient")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="patient")


# ---------------------------------------------------------------------------
# Hospital reference data
# ---------------------------------------------------------------------------


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    doctors: Mapped[list["Doctor"]] = relationship(back_populates="department")
    document_requirements: Mapped[list["DepartmentDocumentRequirement"]] = relationship(
        back_populates="department"
    )


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    department_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("departments.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    department: Mapped["Department"] = relationship(back_populates="doctors")
    slots: Mapped[list["AppointmentSlot"]] = relationship(back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")


class AppointmentSlot(Base):
    """Bookable time window for a doctor. Status flips AVAILABLE → BOOKED on booking."""

    __tablename__ = "appointment_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doctor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("doctors.id"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SlotStatus.AVAILABLE.value, index=True
    )

    doctor: Mapped["Doctor"] = relationship(back_populates="slots")
    appointment: Mapped[Optional["Appointment"]] = relationship(
        back_populates="slot", uselist=False
    )


class DepartmentDocumentRequirement(Base):
    """Which document types a department expects before / with a visit."""

    __tablename__ = "department_document_requirements"
    __table_args__ = (
        UniqueConstraint("department_id", "document_type", name="uq_dept_doc_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("departments.id"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    department: Mapped["Department"] = relationship(back_populates="document_requirements")


# ---------------------------------------------------------------------------
# Appointments, documents, workflow
# ---------------------------------------------------------------------------


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient_profiles.id"), nullable=False, index=True
    )
    doctor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("doctors.id"), nullable=False, index=True
    )
    slot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("appointment_slots.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AppointmentStatus.BOOKED.value
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped["PatientProfile"] = relationship(back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship(back_populates="appointments")
    slot: Mapped["AppointmentSlot"] = relationship(back_populates="appointment")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="appointment")


class PatientDocument(Base):
    """Uploaded file metadata. Bytes live under data/uploads/; checksum for dedupe."""

    __tablename__ = "patient_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient_profiles.id"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=DocumentType.UNKNOWN.value
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped["PatientProfile"] = relationship(back_populates="documents")


class WorkflowRun(Base):
    """One agentic admin journey for a patient. `state` holds GraphState snapshots."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient_profiles.id"), nullable=False, index=True
    )
    current_step: Mapped[str] = mapped_column(String(80), nullable=False, default="coordinator_init")
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WorkflowStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped["PatientProfile"] = relationship(back_populates="workflow_runs")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="workflow_run")


class Reminder(Base):
    """Appointment reminder or post-visit follow-up task (see reminder_type)."""

    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient_profiles.id"), nullable=False, index=True
    )
    appointment_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("appointments.id"), nullable=True, index=True
    )
    reminder_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReminderStatus.SCHEDULED.value
    )

    patient: Mapped["PatientProfile"] = relationship(back_populates="reminders")
    appointment: Mapped[Optional["Appointment"]] = relationship(back_populates="reminders")


# ---------------------------------------------------------------------------
# Safety / HITL / audit
# ---------------------------------------------------------------------------


class Escalation(Base):
    """Human-in-the-loop record when safety/routing needs staff decision."""

    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EscalationStatus.PENDING.value, index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun"] = relationship(back_populates="escalations")
    reviewer: Mapped[Optional["User"]] = relationship(foreign_keys=[reviewed_by])


class AuditEvent(Base):
    """Append-only trail of agent / staff / admin actions."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Column name "metadata" — Python attr cannot be `metadata` (reserved by Declarative API)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped["User"] = relationship(foreign_keys=[actor_id])


class NotificationStatus(str, enum.Enum):
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Notification(Base):
    """Outbound email attempt log (PRD §14.2)."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    patient_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("patient_profiles.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""Phase 2.5 — follow-up reminders / schedule / notification (SMTP stub)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, UserRole
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    ReminderRepository,
    SlotRepository,
    UserRepository,
)
from tools.followup_tools import (
    REMINDER_APPOINTMENT,
    REMINDER_FOLLOWUP,
    create_reminder,
    schedule_followup,
    send_notification,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session):
    users = UserRepository(db)
    patients = PatientRepository(db)
    user = users.create(
        name="Asha",
        email="asha-fu@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = patients.create(user_id=user.id)
    dept = DepartmentRepository(db).create(name="Cardiology")
    doctor = DoctorRepository(db).create(department_id=dept.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    slot = SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    appt = AppointmentRepository(db).book(
        patient_id=profile.id,
        doctor_id=doctor.id,
        slot=slot,
        reason="Follow-up",
    )
    db.commit()
    return user, profile, appt, start


def test_create_reminder_24h_before_slot():
    db = _session()
    user, profile, appt, start = _seed(db)
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    result = create_reminder(state, db, appointment_id=appt.id, hours_before=24)
    db.commit()
    assert result["ok"] is True
    assert result["reminder"]["reminder_type"] == REMINDER_APPOINTMENT
    scheduled = datetime.fromisoformat(result["reminder"]["scheduled_at"])
    assert scheduled == start - timedelta(hours=24)


def test_schedule_followup_seven_days_after():
    db = _session()
    user, profile, appt, start = _seed(db)
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    result = schedule_followup(state, db, appointment_id=appt.id, days_after=7)
    db.commit()
    assert result["ok"] is True
    assert result["followup"]["reminder_type"] == REMINDER_FOLLOWUP
    scheduled = datetime.fromisoformat(result["followup"]["scheduled_at"])
    assert scheduled == start + timedelta(days=7)
    assert len(ReminderRepository(db).list_for_patient(profile.id)) == 1


def test_send_notification_skipped_when_smtp_disabled(monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    db = _session()
    user, profile, appt, _ = _seed(db)
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    result = send_notification(
        state,
        db,
        email_type="APPOINTMENT_CONFIRMATION",
        subject="Your appointment is confirmed",
        body_text="Admin notice only — no clinical advice.",
    )
    assert result["ok"] is True
    assert result["delivery"]["status"] == "SKIPPED"
    assert result["to"] == "asha-fu@ex.com"
    get_settings.cache_clear()

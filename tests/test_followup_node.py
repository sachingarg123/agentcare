"""Phase 3.7 — followup_node: reminder, follow-up task, confirmation email."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.followup_node import FOLLOWUP_PROMPT, followup_node, get_followup_tools
from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, UserRole
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    ReminderRepository,
    SlotRepository,
    UserRepository,
)
from tools.followup_tools import REMINDER_APPOINTMENT, REMINDER_FOLLOWUP


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session) -> tuple[GraphState, dict]:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-fu-node@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
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
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "appointment_result": {
            "ok": True,
            "appointment_id": appt.id,
            "slot_id": slot.id,
            "doctor_name": "Dr. Mehta",
            "start_time": start.isoformat(),
            "status": "BOOKED",
        },
    }
    return state, {"user": user, "profile": profile, "appt": appt, "start": start}


def test_followup_prompt_loaded():
    assert "Follow-up" in FOLLOWUP_PROMPT
    assert "create_reminder" in FOLLOWUP_PROMPT


def test_followup_node_schedules_reminder_and_task(monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    db = _session()
    state, seeded = _seed(db)
    update = followup_node(state, db)
    db.commit()

    assert update["current_step"] == "followup"
    result = update["followup_result"]
    assert result["ok"] is True
    assert len(result["reminder_ids"]) == 2
    assert result["followup_task_id"]
    assert result["notification_status"] == "SKIPPED"

    rows = ReminderRepository(db).list_for_patient(seeded["profile"].id)
    types = {r.reminder_type for r in rows}
    assert REMINDER_APPOINTMENT in types
    assert REMINDER_FOLLOWUP in types

    get_settings.cache_clear()


def test_followup_node_skips_without_appointment():
    db = _session()
    user = UserRepository(db).create(
        name="Ravi",
        email="ravi-fu-node@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    db.commit()
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "appointment_result": {"ok": False, "error": "no_slots"},
    }
    update = followup_node(state, db)
    assert update["followup_result"]["ok"] is False
    assert update["followup_result"]["error"] == "no_appointment"


def test_followup_node_document_request_when_missing(monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    db = _session()
    state, _ = _seed(db)
    state["document_result"] = {"missing": [DocumentType.ECG.value]}
    update = followup_node(state, db)
    db.commit()

    assert update["followup_result"]["ok"] is True
    get_settings.cache_clear()


def test_get_followup_tools_binds_three():
    db = _session()
    state, _ = _seed(db)
    tools = get_followup_tools(state, db)
    assert {t.name for t in tools} == {
        "create_reminder",
        "schedule_followup",
        "send_notification",
    }

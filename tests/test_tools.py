"""Phase 2.7 — consolidated tool tests against real SQLite (no DB mocks).

Exercises the administrative happy path and key failure modes:
patient → safety → routing → slots → book → document → reminder → audit.
Also: slot conflict, duplicate checksum, clinical block + escalation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, SlotStatus, UserRole
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    DocumentRepository,
    EscalationRepository,
    ReminderRepository,
    SlotRepository,
    UserRepository,
    WorkflowRepository,
)
from tools.appointment_tools import book_appointment, get_available_slots
from tools.document_tools import store_document
from tools.followup_tools import create_reminder, schedule_followup, send_notification
from tools.patient_tools import get_or_create_patient
from tools.routing_tools import classify_intent, lookup_departments
from tools.safety_tools import block_unsafe_action, screen_request, write_audit_event


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def hospital(db: Session):
    """Seed reference data + one patient user (real inserts)."""
    users = UserRepository(db)
    depts = DepartmentRepository(db)
    doctors = DoctorRepository(db)
    slots = SlotRepository(db)

    user = users.create(
        name="Asha Patient",
        email="asha.tools@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    cardio = depts.create(name="Cardiology", description="Heart care")
    depts.add_document_requirement(
        department_id=cardio.id, document_type=DocumentType.ECG.value, required=True
    )
    doctor = doctors.create(department_id=cardio.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    slot_a = slots.create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    slot_b = slots.create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db.commit()
    return {
        "user": user,
        "cardio": cardio,
        "doctor": doctor,
        "slot_a": slot_a,
        "slot_b": slot_b,
        "start": start,
    }


def test_happy_path_tools_read_and_write_sqlite(db: Session, hospital, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    user = hospital["user"]
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": (
            "I need a cardiology follow-up next week and want to attach my old ECG."
        ),
    }

    # 2.1 patient
    patient = get_or_create_patient(state, db, phone="+91-90000")
    db.commit()
    assert patient["created"] is True
    state["patient_id"] = patient["patient_id"]

    wf = WorkflowRepository(db).create(patient_id=state["patient_id"])
    db.commit()
    state["workflow_run_id"] = wf.id

    # 2.6 safety — admin request allowed
    safety = screen_request(state)
    assert safety["safe"] is True

    # 2.2 routing — departments from DB
    depts = lookup_departments(db)
    assert any(d["name"] == "Cardiology" for d in depts)
    routing = classify_intent(state, db)
    assert routing["department_name"] == "Cardiology"
    assert routing["department_id"] == hospital["cardio"].id
    assert routing["needs_staff_review"] is False

    # 2.3 appointment
    available = get_available_slots(db, department_id=routing["department_id"])
    assert available["count"] >= 2
    booked = book_appointment(
        state, db, slot_id=available["slots"][0]["slot_id"], reason="Follow-up"
    )
    db.commit()
    assert booked["ok"] is True
    appt_id = booked["appointment"]["appointment_id"]
    assert AppointmentRepository(db).get_by_id(appt_id) is not None

    # Slot conflict — same slot again
    conflict = book_appointment(state, db, slot_id=available["slots"][0]["slot_id"])
    assert conflict["ok"] is False
    assert conflict["error"] == "slot_unavailable"

    # 2.4 documents — store + duplicate
    content = b"%PDF-ECG-BYTES-UNIQUE-001"
    stored = store_document(state, db, filename="old_ecg.pdf", content=content)
    db.commit()
    assert stored["ok"] is True
    assert stored["document_type"] == DocumentType.ECG.value
    dup = store_document(state, db, filename="old_ecg_copy.pdf", content=content)
    assert dup["ok"] is False
    assert dup["error"] == "duplicate"
    assert DocumentRepository(db).find_by_checksum(state["patient_id"], stored["checksum"])

    # 2.5 follow-up + notification (SMTP skipped)
    reminder = create_reminder(state, db, appointment_id=appt_id, hours_before=24)
    followup = schedule_followup(state, db, appointment_id=appt_id, days_after=7)
    db.commit()
    assert reminder["ok"] and followup["ok"]
    assert len(ReminderRepository(db).list_for_patient(state["patient_id"])) == 2

    notify = send_notification(
        state,
        db,
        email_type="APPOINTMENT_CONFIRMATION",
        subject="Confirmed",
        body_text="Your appointment is confirmed.",
    )
    db.commit()
    assert notify["ok"] is True
    assert notify["delivery"]["status"] == "SKIPPED"

    # Audit trail
    audit = write_audit_event(
        state,
        db,
        action="workflow.happy_path",
        entity_type="WorkflowRun",
        entity_id=state["workflow_run_id"],
    )
    db.commit()
    assert audit["ok"] is True

    get_settings.cache_clear()


def test_safety_block_creates_escalation_no_appointment(db: Session, hospital):
    user = hospital["user"]
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "What medicine should I take for this?",
    }
    patient = get_or_create_patient(state, db)
    db.commit()
    state["patient_id"] = patient["patient_id"]
    wf = WorkflowRepository(db).create(patient_id=state["patient_id"])
    db.commit()
    state["workflow_run_id"] = wf.id

    out = block_unsafe_action(state, db)
    db.commit()

    assert out["blocked"] is True
    assert out["escalation"]["ok"] is True
    esc = EscalationRepository(db).get_by_id(out["escalation"]["escalation_id"])
    assert esc is not None
    assert AppointmentRepository(db).list_for_patient(state["patient_id"]) == []


def test_book_marks_slot_status_in_db(db: Session, hospital):
    user = hospital["user"]
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    patient = get_or_create_patient(state, db)
    db.commit()
    state["patient_id"] = patient["patient_id"]

    slot_id = hospital["slot_a"].id
    result = book_appointment(state, db, slot_id=slot_id)
    db.commit()
    assert result["ok"] is True

    refreshed = SlotRepository(db).get_by_id(slot_id)
    assert refreshed is not None
    assert refreshed.status == SlotStatus.BOOKED.value

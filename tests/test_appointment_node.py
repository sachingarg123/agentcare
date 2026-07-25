"""Phase 3.5 — appointment_node: book with slot retry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.appointment_node import (
    APPOINTMENT_PROMPT,
    appointment_node,
    get_appointment_tools,
)
from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, SlotStatus, UserRole
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)
from tools.routing_tools import INTENT_BOOK, INTENT_CANCEL


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session, *, slots: int = 2) -> tuple[GraphState, dict]:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-appt-node@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    cardio = DepartmentRepository(db).create(name="Cardiology")
    doctor = DoctorRepository(db).create(department_id=cardio.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    created_slots = []
    for i in range(slots):
        s = SlotRepository(db).create(
            doctor_id=doctor.id,
            start_time=start + timedelta(hours=i),
            end_time=start + timedelta(hours=i, minutes=30),
        )
        created_slots.append(s)
    db.commit()
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "Book cardiology follow-up",
        "administrative_intents": [INTENT_BOOK],
        "routing_result": {
            "department_id": cardio.id,
            "department_name": "Cardiology",
            "intents": [INTENT_BOOK],
            "confidence": 0.9,
            "needs_staff_review": False,
        },
    }
    return state, {
        "user": user,
        "profile": profile,
        "cardio": cardio,
        "doctor": doctor,
        "slots": created_slots,
    }


def test_appointment_prompt_loaded():
    assert "Appointment" in APPOINTMENT_PROMPT
    assert "book_appointment" in APPOINTMENT_PROMPT


def test_appointment_node_books_first_available_slot():
    db = _session()
    state, hospital = _seed(db)
    update = appointment_node(state, db)
    db.commit()

    assert update["current_step"] == "appointment"
    assert update["hitl_required"] is False
    result = update["appointment_result"]
    assert result["ok"] is True
    assert result["appointment_id"]
    assert result["slot_id"] == hospital["slots"][0].id
    assert result["doctor_name"] == "Dr. Mehta"
    assert AppointmentRepository(db).get_by_id(result["appointment_id"]) is not None
    refreshed = SlotRepository(db).get_by_id(result["slot_id"])
    assert refreshed is not None
    assert refreshed.status == SlotStatus.BOOKED.value


def test_appointment_node_retries_when_first_slot_taken():
    db = _session()
    state, hospital = _seed(db, slots=2)
    # Mark first slot booked without going through node
    first = hospital["slots"][0]
    first.status = SlotStatus.BOOKED.value
    db.commit()

    update = appointment_node(state, db)
    db.commit()

    assert update["appointment_result"]["ok"] is True
    assert update["appointment_result"]["slot_id"] == hospital["slots"][1].id


def test_appointment_node_no_slots_sets_hitl():
    db = _session()
    state, _hospital = _seed(db, slots=0)
    # department exists but zero slots
    update = appointment_node(state, db)

    assert update["appointment_result"]["ok"] is False
    assert update["appointment_result"]["error"] == "no_slots"
    assert update["hitl_required"] is True


def test_appointment_node_cancel_path():
    db = _session()
    state, hospital = _seed(db)
    booked = appointment_node(state, db)
    db.commit()
    appt_id = booked["appointment_result"]["appointment_id"]

    cancel_state: GraphState = {
        **state,
        "administrative_intents": [INTENT_CANCEL],
        "appointment_result": {"appointment_id": appt_id},
    }
    update = appointment_node(cancel_state, db)
    db.commit()

    assert update["appointment_result"]["ok"] is True
    appt = AppointmentRepository(db).get_by_id(appt_id)
    assert appt is not None
    assert appt.status == "CANCELLED"
    slot = SlotRepository(db).get_by_id(hospital["slots"][0].id)
    assert slot is not None
    assert slot.status == SlotStatus.AVAILABLE.value


def test_get_appointment_tools_binds_four():
    db = _session()
    state, _ = _seed(db)
    tools = get_appointment_tools(state, db)
    assert {t.name for t in tools} == {
        "get_available_slots",
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
    }
    out = tools[0].invoke({"limit": 5})
    assert out["count"] >= 1

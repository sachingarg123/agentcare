"""Phase 2.3 — appointment tools with real slot booking / conflicts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, SlotStatus, UserRole
from db.repositories import (
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)
from tools._scope import ToolScopeError
from tools.appointment_tools import (
    book_appointment,
    cancel_appointment,
    get_available_slots,
    reschedule_appointment,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_hospital(db: Session):
    users = UserRepository(db)
    patients = PatientRepository(db)
    depts = DepartmentRepository(db)
    doctors = DoctorRepository(db)
    slots = SlotRepository(db)

    asha = users.create(
        name="Asha",
        email="asha@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    ravi = users.create(
        name="Ravi",
        email="ravi@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    asha_p = patients.create(user_id=asha.id)
    ravi_p = patients.create(user_id=ravi.id)

    cardio = depts.create(name="Cardiology")
    doctor = doctors.create(department_id=cardio.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    slot_a = slots.create(
        doctor_id=doctor.id, start_time=start, end_time=start + timedelta(minutes=30)
    )
    slot_b = slots.create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db.commit()
    return {
        "asha": asha,
        "asha_p": asha_p,
        "ravi_p": ravi_p,
        "dept": cardio,
        "doctor": doctor,
        "slot_a": slot_a,
        "slot_b": slot_b,
    }


def _state(patient_id: str, user_id: str) -> GraphState:
    return {
        "patient_id": patient_id,
        "actor_user_id": user_id,
        "actor_role": UserRole.PATIENT.value,
    }


def test_get_available_slots_filters_by_department():
    db = _session()
    data = _seed_hospital(db)
    result = get_available_slots(db, department_id=data["dept"].id)
    assert result["count"] == 2
    assert all(s["status"] == SlotStatus.AVAILABLE.value for s in result["slots"])
    assert result["slots"][0]["doctor_name"] == "Dr. Mehta"


def test_book_appointment_marks_slot_booked():
    db = _session()
    data = _seed_hospital(db)
    state = _state(data["asha_p"].id, data["asha"].id)

    booked = book_appointment(state, db, slot_id=data["slot_a"].id, reason="Follow-up")
    db.commit()

    assert booked["ok"] is True
    assert booked["appointment"]["status"] == "BOOKED"
    assert booked["appointment"]["patient_id"] == data["asha_p"].id

    # Slot no longer available
    again = get_available_slots(db, department_id=data["dept"].id)
    assert again["count"] == 1
    assert again["slots"][0]["slot_id"] == data["slot_b"].id

    # Second book on same slot fails
    conflict = book_appointment(state, db, slot_id=data["slot_a"].id)
    assert conflict["ok"] is False
    assert conflict["error"] == "slot_unavailable"


def test_cancel_frees_slot():
    db = _session()
    data = _seed_hospital(db)
    state = _state(data["asha_p"].id, data["asha"].id)
    booked = book_appointment(state, db, slot_id=data["slot_a"].id)
    db.commit()

    cancelled = cancel_appointment(
        state, db, appointment_id=booked["appointment"]["appointment_id"]
    )
    db.commit()
    assert cancelled["ok"] is True
    assert cancelled["appointment"]["status"] == "CANCELLED"

    available = get_available_slots(db, department_id=data["dept"].id)
    assert available["count"] == 2


def test_reschedule_moves_slot():
    db = _session()
    data = _seed_hospital(db)
    state = _state(data["asha_p"].id, data["asha"].id)
    booked = book_appointment(state, db, slot_id=data["slot_a"].id)
    db.commit()

    moved = reschedule_appointment(
        state,
        db,
        appointment_id=booked["appointment"]["appointment_id"],
        new_slot_id=data["slot_b"].id,
    )
    db.commit()
    assert moved["ok"] is True
    assert moved["appointment"]["slot_id"] == data["slot_b"].id
    assert moved["appointment"]["status"] == "RESCHEDULED"

    # old free, new booked → 1 available (slot_a)
    available = get_available_slots(db, department_id=data["dept"].id)
    assert available["count"] == 1
    assert available["slots"][0]["slot_id"] == data["slot_a"].id


def test_cannot_book_for_other_patient_via_scope():
    db = _session()
    data = _seed_hospital(db)
    # Asha actor but Ravi's patient_id in state → scope should fail
    bad_state: GraphState = {
        "patient_id": data["ravi_p"].id,
        "actor_user_id": data["asha"].id,
        "actor_role": UserRole.PATIENT.value,
    }
    with pytest.raises(ToolScopeError):
        book_appointment(bad_state, db, slot_id=data["slot_a"].id)

"""Phase 1.3 — repository smoke tests against a temporary SQLite DB."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, SlotStatus, UserRole
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_user_and_patient_create():
    db = _session()
    users = UserRepository(db)
    patients = PatientRepository(db)

    user = users.create(
        name="Ada Patient",
        email="ada@example.com",
        password_hash="hashed",
        role=UserRole.PATIENT.value,
    )
    profile = patients.create(user_id=user.id, phone="+91-99999")

    assert users.get_by_email("ada@example.com").id == user.id
    assert patients.get_by_user_id(user.id).id == profile.id
    db.close()


def test_book_appointment_marks_slot():
    db = _session()
    users = UserRepository(db)
    patients = PatientRepository(db)
    depts = DepartmentRepository(db)
    doctors = DoctorRepository(db)
    slots = SlotRepository(db)
    appointments = AppointmentRepository(db)

    user = users.create(name="Bob", email="bob@example.com", password_hash="x")
    patient = patients.create(user_id=user.id)
    cardiology = depts.create(name="Cardiology")
    doctor = doctors.create(department_id=cardiology.id, name="Dr. Heart")

    start = datetime.now(timezone.utc) + timedelta(days=1)
    slot = slots.create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )

    appt = appointments.book(
        patient_id=patient.id,
        doctor_id=doctor.id,
        slot=slot,
        reason="Follow-up",
    )

    assert appt.slot_id == slot.id
    assert slot.status == SlotStatus.BOOKED.value
    assert len(slots.list_available(doctor_id=doctor.id)) == 0
    db.close()

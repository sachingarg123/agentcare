"""Phase 2.1 — get_or_create_patient against real SQLite (in-memory)."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, UserRole
from db.repositories import PatientRepository, UserRepository
from tools._scope import ToolScopeError
from tools.patient_tools import get_or_create_patient


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_creates_profile_for_new_patient_user():
    db = _db()
    user = UserRepository(db).create(
        name="New Patient",
        email="new@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    db.commit()

    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    result = get_or_create_patient(
        state,
        db,
        phone="+91-111",
        date_of_birth=date(1992, 1, 1),
    )
    db.commit()

    assert result["created"] is True
    assert result["patient_id"]
    assert result["phone"] == "+91-111"
    assert result["date_of_birth"] == "1992-01-01"
    assert PatientRepository(db).get_by_user_id(user.id) is not None


def test_returns_existing_profile_without_duplicate():
    db = _db()
    user = UserRepository(db).create(
        name="Asha",
        email="asha@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id, phone="+91-old")
    db.commit()

    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    first = get_or_create_patient(state, db)
    second = get_or_create_patient(state, db, phone="+91-new")
    db.commit()

    assert first["created"] is False
    assert second["created"] is False
    assert first["patient_id"] == second["patient_id"] == profile.id
    assert second["phone"] == "+91-new"
    assert PatientRepository(db).get_by_user_id(user.id).id == profile.id


def test_staff_loads_existing_patient():
    db = _db()
    users = UserRepository(db)
    patients = PatientRepository(db)
    patient_user = users.create(
        name="Asha",
        email="asha2@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    staff = users.create(
        name="Sam",
        email="sam@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.STAFF.value,
    )
    profile = patients.create(user_id=patient_user.id)
    db.commit()

    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": staff.id,
        "actor_role": UserRole.STAFF.value,
    }
    result = get_or_create_patient(state, db)
    assert result["created"] is False
    assert result["patient_id"] == profile.id


def test_mismatch_patient_id_fails_before_update():
    """Wrong state.patient_id must fail without applying phone update."""
    db = _db()
    users = UserRepository(db)
    patients = PatientRepository(db)
    asha = users.create(
        name="Asha",
        email="asha3@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    ravi = users.create(
        name="Ravi",
        email="ravi3@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    asha_p = patients.create(user_id=asha.id, phone="+91-old")
    ravi_p = patients.create(user_id=ravi.id)
    db.commit()

    state: GraphState = {
        "patient_id": ravi_p.id,  # wrong subject
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
    }
    with pytest.raises(ToolScopeError, match="does not match"):
        get_or_create_patient(state, db, phone="+91-should-not-apply")

    db.refresh(asha_p)
    assert asha_p.phone == "+91-old"


def test_staff_without_patient_id_fails():
    db = _db()
    staff = UserRepository(db).create(
        name="Sam",
        email="sam2@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.STAFF.value,
    )
    db.commit()
    state: GraphState = {
        "actor_user_id": staff.id,
        "actor_role": UserRole.STAFF.value,
    }
    with pytest.raises(ToolScopeError, match="patient_id"):
        get_or_create_patient(state, db)


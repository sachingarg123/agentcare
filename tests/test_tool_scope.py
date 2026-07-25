"""Phase 2.0 — tool scope enforcement tests."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, UserRole
from db.repositories import PatientRepository, UserRepository
from tools._scope import ToolScopeError, assert_tool_scope


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_two_patients(db: Session):
    users = UserRepository(db)
    patients = PatientRepository(db)
    pw = hash_password("password123")
    asha = users.create(
        name="Asha", email="asha@ex.com", password_hash=pw, role=UserRole.PATIENT.value
    )
    ravi = users.create(
        name="Ravi", email="ravi@ex.com", password_hash=pw, role=UserRole.PATIENT.value
    )
    staff = users.create(
        name="Sam", email="sam@ex.com", password_hash=pw, role=UserRole.STAFF.value
    )
    asha_p = patients.create(user_id=asha.id)
    ravi_p = patients.create(user_id=ravi.id)
    db.commit()
    return asha, ravi, staff, asha_p, ravi_p


def test_patient_actor_ok_on_own_workflow():
    db = _db()
    asha, _, _, asha_p, _ = _seed_two_patients(db)
    state: GraphState = {
        "patient_id": asha_p.id,
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
    }
    assert_tool_scope(state, asha_p.id, db)  # no raise


def test_patient_cannot_target_other_patient_id():
    db = _db()
    asha, _, _, asha_p, ravi_p = _seed_two_patients(db)
    state: GraphState = {
        "patient_id": asha_p.id,
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
    }
    with pytest.raises(ToolScopeError, match="cannot act on patient"):
        assert_tool_scope(state, ravi_p.id, db)


def test_patient_cannot_act_as_other_workflow_subject():
    """Asha's user id but workflow patient_id is Ravi → blocked."""
    db = _db()
    asha, _, _, _, ravi_p = _seed_two_patients(db)
    state: GraphState = {
        "patient_id": ravi_p.id,
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
    }
    with pytest.raises(ToolScopeError, match="another patient"):
        assert_tool_scope(state, ravi_p.id, db)


def test_staff_can_act_on_patient_workflow():
    db = _db()
    _, _, staff, asha_p, _ = _seed_two_patients(db)
    state: GraphState = {
        "patient_id": asha_p.id,
        "actor_user_id": staff.id,
        "actor_role": UserRole.STAFF.value,
    }
    assert_tool_scope(state, asha_p.id, db)


def test_staff_still_cannot_retarget_other_patient():
    db = _db()
    _, _, staff, asha_p, ravi_p = _seed_two_patients(db)
    state: GraphState = {
        "patient_id": asha_p.id,
        "actor_user_id": staff.id,
        "actor_role": UserRole.STAFF.value,
    }
    with pytest.raises(ToolScopeError, match="cannot act on patient"):
        assert_tool_scope(state, ravi_p.id, db)

"""Phase 3.8 — coordinator_init + coordinator_finalize."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.coordinator_node import (
    COORDINATOR_PROMPT,
    coordinator_finalize,
    coordinator_init,
    get_coordinator_tools,
)
from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, UserRole, WorkflowStatus
from db.repositories import (
    PatientRepository,
    UserRepository,
    WorkflowRepository,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_coordinator_prompt_loaded():
    assert "Coordinator" in COORDINATOR_PROMPT
    assert "finalize" in COORDINATOR_PROMPT.lower() or "confirmation" in COORDINATOR_PROMPT.lower()


def test_coordinator_init_creates_patient_and_workflow():
    db = _session()
    user = UserRepository(db).create(
        name="Asha",
        email="asha-coord@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    db.commit()
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "Book cardiology follow-up",
    }
    update = coordinator_init(state, db)
    db.commit()

    assert update["current_step"] == "coordinator_init"
    assert update["patient_id"]
    assert update["workflow_run_id"]
    assert PatientRepository(db).get_by_id(update["patient_id"]) is not None
    run = WorkflowRepository(db).get_by_id(update["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.RUNNING.value
    assert run.patient_id == update["patient_id"]


def test_coordinator_finalize_happy_path_confirmation():
    db = _session()
    user = UserRepository(db).create(
        name="Asha",
        email="asha-coord2@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    db.commit()
    init = coordinator_init(
        {
            "actor_user_id": user.id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology",
        },
        db,
    )
    db.commit()

    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "patient_id": init["patient_id"],
        "workflow_run_id": init["workflow_run_id"],
        "raw_request": "Book cardiology",
        "safety_result": {"safe": True, "blocked": False},
        "routing_result": {"department_name": "Cardiology", "confidence": 0.9},
        "appointment_result": {
            "ok": True,
            "appointment_id": "appt-1",
            "doctor_name": "Dr. Mehta",
            "start_time": "2026-07-26T09:00:00+00:00",
        },
        "document_result": {"stored": [{"document_id": "d1"}], "missing": []},
        "followup_result": {
            "ok": True,
            "reminder_ids": ["r1", "r2"],
            "notification_status": "SKIPPED",
        },
    }
    update = coordinator_finalize(state, db)
    db.commit()

    assert update["current_step"] == "coordinator_finalize"
    conf = update["confirmation"]
    assert conf["ok"] is True
    assert "Dr. Mehta" in conf["summary"]
    assert "Cardiology" in conf["summary"]
    assert conf["documents_stored"] == 1
    assert conf["reminders_scheduled"] == 2

    run = WorkflowRepository(db).get_by_id(init["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value
    assert run.current_step == "coordinator_finalize"


def test_coordinator_finalize_safety_block():
    db = _session()
    user = UserRepository(db).create(
        name="Asha",
        email="asha-coord3@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    db.commit()
    init = coordinator_init(
        {
            "actor_user_id": user.id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "What medicine should I take?",
        },
        db,
    )
    db.commit()
    state: GraphState = {
        **init,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "safety_result": {
            "safe": False,
            "blocked": True,
            "message": "Administrative help only; contact your clinician.",
        },
        "hitl_required": True,
    }
    update = coordinator_finalize(state, db)
    db.commit()

    assert update["confirmation"]["ok"] is False
    assert "clinician" in update["confirmation"]["summary"].lower() or "administrative" in update[
        "confirmation"
    ]["summary"].lower()
    run = WorkflowRepository(db).get_by_id(init["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.BLOCKED_SAFETY.value


def test_get_coordinator_tools_binds_two():
    db = _session()
    user = UserRepository(db).create(
        name="Asha",
        email="asha-coord4@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    db.commit()
    state: GraphState = {
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    tools = get_coordinator_tools(state, db)
    assert {t.name for t in tools} == {"get_or_create_patient", "write_audit_event"}

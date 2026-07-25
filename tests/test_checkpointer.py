"""Phase 4.3 — SqliteSaver durable HITL resume across graph instances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langgraph.types import Command
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.pipeline import compile_workflow, get_checkpointer
from db.models import Base, UserRole, WorkflowStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    SlotRepository,
    UserRepository,
    WorkflowRepository,
)


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
def hospital(db: Session) -> dict:
    user = UserRepository(db).create(
        name="Asha",
        email="asha.checkpoint@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    cardio = DepartmentRepository(db).create(name="Cardiology", description="Heart")
    for name in ("Radiology", "General Medicine", "Orthopedics", "Dermatology"):
        DepartmentRepository(db).create(name=name, description=name)
    doctor = DoctorRepository(db).create(department_id=cardio.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    db.commit()
    return {"user": user, "cardio": cardio}


def test_get_checkpointer_creates_sqlite_file(tmp_path):
    path = tmp_path / "checkpoints.db"
    assert not path.exists()
    with get_checkpointer(str(path)) as saver:
        assert saver is not None
    assert path.exists()


def test_sqlite_checkpointer_survives_new_graph_instance(db: Session, hospital, tmp_path):
    """Pause with one compiled graph; resume with a fresh graph on the same DB file."""
    path = tmp_path / "hitl_checkpoints.db"
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    with get_checkpointer(str(path)) as saver:
        graph = compile_workflow(db, checkpointer=saver)
        paused = graph.invoke(
            {
                "actor_user_id": hospital["user"].id,
                "actor_role": UserRole.PATIENT.value,
                "raw_request": "What medicine should I take?",
            },
            config,
        )
        db.commit()

    assert "__interrupt__" in paused
    workflow_run_id = paused["workflow_run_id"]
    run = WorkflowRepository(db).get_by_id(workflow_run_id)
    assert run is not None
    assert run.status == WorkflowStatus.WAITING_HITL.value

    # New saver + new compiled graph — same sqlite file + thread_id
    with get_checkpointer(str(path)) as saver2:
        graph2 = compile_workflow(db, checkpointer=saver2)
        final = graph2.invoke(
            Command(resume={"decision": "approve", "note": "Handled after restart"}),
            config,
        )
        db.commit()

    assert final["current_step"] == "coordinator_finalize"
    assert final["confirmation"]["ok"] is False
    assert AppointmentRepository(db).list_for_patient(final["patient_id"]) == []
    run = WorkflowRepository(db).get_by_id(workflow_run_id)
    assert run is not None
    assert run.status == WorkflowStatus.BLOCKED_SAFETY.value

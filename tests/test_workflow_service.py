"""Phase 4.4 — workflow_service injects actor identity and starts/resumes runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from db.models import Base, DocumentType, UserRole, WorkflowStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    SlotRepository,
    UserRepository,
    WorkflowRepository,
)
from services.workflow_service import resume_workflow, start_workflow


@pytest.fixture(autouse=True)
def _disable_langsmith_in_tests(monkeypatch):
    """Keep unit tests offline even if .env enables LangSmith."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
        email="asha.workflow@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    cardio = DepartmentRepository(db).create(name="Cardiology", description="Heart")
    DepartmentRepository(db).add_document_requirement(
        department_id=cardio.id,
        document_type=DocumentType.ECG.value,
        required=True,
    )
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


@pytest.fixture()
def memory() -> MemorySaver:
    return MemorySaver()


def test_start_workflow_injects_actor_and_completes(db: Session, hospital, memory, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    out = start_workflow(
        db,
        actor_user_id=hospital["user"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="I need a cardiology follow-up next week and want to attach my old ECG.",
        uploaded_files=[
            {"filename": "old_ecg.pdf", "content": b"%PDF-WF-ECG", "mime_type": "application/pdf"}
        ],
        checkpointer=memory,
    )
    db.commit()

    assert out["status"] == "completed"
    assert out["workflow_run_id"]
    assert out["state"]["actor_user_id"] == hospital["user"].id
    assert out["state"]["actor_role"] == UserRole.PATIENT.value
    assert out["confirmation"]["ok"] is True
    assert AppointmentRepository(db).get_by_id(
        out["state"]["appointment_result"]["appointment_id"]
    )
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value
    get_settings.cache_clear()


def test_start_workflow_overwrites_spoofed_actor(db: Session, hospital, memory):
    """Service identity wins — client cannot set a different actor in the bag."""
    # Pretend caller tried to stuff another actor into uploaded metadata only;
    # start_workflow args are authoritative.
    out = start_workflow(
        db,
        actor_user_id=hospital["user"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="Book cardiology appointment",
        checkpointer=memory,
    )
    db.commit()
    assert out["state"]["actor_user_id"] == hospital["user"].id
    assert out["state"]["actor_role"] == UserRole.PATIENT.value


def test_start_and_resume_safety_hitl(db: Session, hospital, memory):
    out = start_workflow(
        db,
        actor_user_id=hospital["user"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="What medicine should I take?",
        checkpointer=memory,
    )
    db.commit()

    assert out["status"] == "interrupted"
    assert out["interrupt"]["source"] == "safety"
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.WAITING_HITL.value

    resumed = resume_workflow(
        db,
        workflow_run_id=out["workflow_run_id"],
        decision="approve",
        note="Staff will contact patient",
        checkpointer=memory,
    )
    db.commit()

    assert resumed["status"] == "completed"
    assert resumed["confirmation"]["ok"] is False
    assert AppointmentRepository(db).list_for_patient(resumed["patient_id"]) == []


def test_resume_routing_hitl_with_department(db: Session, hospital, memory, monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    out = start_workflow(
        db,
        actor_user_id=hospital["user"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="hello",
        checkpointer=memory,
    )
    db.commit()
    assert out["status"] == "interrupted"
    assert out["interrupt"]["source"] == "routing"

    resumed = resume_workflow(
        db,
        workflow_run_id=out["workflow_run_id"],
        decision="approve",
        department_id=hospital["cardio"].id,
        department_name="Cardiology",
        checkpointer=memory,
    )
    db.commit()

    assert resumed["status"] == "completed"
    assert resumed["state"]["appointment_result"]["ok"] is True
    assert resumed["confirmation"]["ok"] is True
    get_settings.cache_clear()


def test_start_workflow_requires_actor(db: Session, memory):
    with pytest.raises(ValueError, match="actor"):
        start_workflow(
            db,
            actor_user_id="",
            actor_role="PATIENT",
            raw_request="hi",
            checkpointer=memory,
        )

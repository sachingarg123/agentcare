"""Phase 7.6 — LLM retries + workflow FAILED state (PRD §12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.llm import LLM_MAX_RETRIES, get_llm
from db.models import Base, DocumentType, UserRole, WorkflowStatus
from db.repositories import (
    AuditRepository,
    DepartmentRepository,
    DoctorRepository,
    SlotRepository,
    UserRepository,
    WorkflowRepository,
)
from services import workflow_service
from services.workflow_service import mark_workflow_failed, set_checkpointer_override, start_workflow


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("SMTP_DISABLED", "true")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    set_checkpointer_override(MemorySaver())
    yield
    set_checkpointer_override(None)
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
    asha = UserRepository(db).create(
        name="Asha Patient",
        email="asha.err@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    from db.repositories import PatientRepository

    PatientRepository(db).create(user_id=asha.id)

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
    return {"asha": asha}


def test_llm_max_retries_constant_matches_prd():
    assert LLM_MAX_RETRIES == 3


def test_groq_client_configured_with_retries(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    get_settings.cache_clear()
    llm = get_llm()
    # LangChain ChatGroq exposes max_retries on the runnable
    retries = getattr(llm, "max_retries", None)
    if retries is None:
        retries = getattr(getattr(llm, "kwargs", {}), "get", lambda *_: None)("max_retries")
    assert retries == LLM_MAX_RETRIES


def test_mark_workflow_failed_persists_status_and_error(db: Session, hospital: dict):
    asha = hospital["asha"]
    from db.repositories import PatientRepository

    profile = PatientRepository(db).get_by_user_id(asha.id)
    run = WorkflowRepository(db).create(
        patient_id=profile.id,
        current_step="routing",
        state={"raw_request": "book cardio", "thread_id": "x"},
        status=WorkflowStatus.RUNNING.value,
    )
    db.commit()

    mark_workflow_failed(
        db,
        workflow_run_id=run.id,
        error=RuntimeError("LLM rate limit exceeded"),
        current_step="routing",
        actor_user_id=asha.id,
        actor_role=UserRole.PATIENT.value,
    )
    db.commit()

    refreshed = WorkflowRepository(db).get_by_id(run.id)
    assert refreshed is not None
    assert refreshed.status == WorkflowStatus.FAILED.value
    assert refreshed.state["error"] == "LLM rate limit exceeded"
    assert refreshed.state["error_type"] == "RuntimeError"
    # Prior state preserved
    assert refreshed.state["raw_request"] == "book cardio"

    audits = [
        e
        for e in AuditRepository(db).list_recent(limit=20)
        if e.action == "workflow.fail" and e.entity_id == run.id
    ]
    assert len(audits) == 1
    assert audits[0].actor_id == asha.id


def test_start_workflow_marks_failed_when_graph_raises(db: Session, hospital: dict, monkeypatch):
    asha = hospital["asha"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr(workflow_service, "_invoke", boom)

    out = start_workflow(
        db,
        actor_user_id=asha.id,
        actor_role=UserRole.PATIENT.value,
        raw_request="I need a cardiology follow-up next week",
    )
    db.commit()

    assert out["status"] == "failed"
    assert "simulated provider outage" in (out.get("error") or "")
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.FAILED.value
    assert "simulated provider outage" in (run.state or {}).get("error", "")


def test_graph_interrupt_is_not_marked_failed(db: Session, hospital: dict, monkeypatch):
    """HITL interrupt exceptions must propagate, not become FAILED."""
    asha = hospital["asha"]

    class GraphInterrupt(Exception):
        pass

    def interrupt_boom(*_args, **_kwargs):
        raise GraphInterrupt("pause for staff")

    monkeypatch.setattr(workflow_service, "_invoke", interrupt_boom)

    with pytest.raises(GraphInterrupt):
        start_workflow(
            db,
            actor_user_id=asha.id,
            actor_role=UserRole.PATIENT.value,
            raw_request="unclear request please help",
        )

    # Workflow row should still be RUNNING (not FAILED) — interrupt bubbled
    runs = WorkflowRepository(db).list_all(limit=5)
    assert runs
    assert runs[0].status == WorkflowStatus.RUNNING.value

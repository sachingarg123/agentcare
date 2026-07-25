"""Phase 7.5 — every agent action writes AuditEvent with actor_id.

Happy-path workflow must leave a trail for coordinator / safety / routing /
appointment / document / followup. Safety-block path must audit the block.
Staff resume must attribute staff_review.decision to the staff actor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from auth.passwords import hash_password
from core.config import get_settings
from db.models import Base, DocumentType, UserRole
from db.repositories import (
    AuditRepository,
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)
from services.workflow_service import resume_workflow, set_checkpointer_override, start_workflow
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


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
        email="asha.audit@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    staff = UserRepository(db).create(
        name="Sam Staff",
        email="sam.audit@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.STAFF.value,
    )
    PatientRepository(db).create(user_id=asha.id)

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
    SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db.commit()
    return {"asha": asha, "staff": staff, "cardio": cardio}


def _actions(db: Session, *, workflow_run_id: str | None = None) -> list:
    rows = AuditRepository(db).list_recent(limit=200)
    if workflow_run_id:
        rows = [
            e
            for e in rows
            if (e.event_metadata or {}).get("workflow_run_id") == workflow_run_id
            or e.entity_id == workflow_run_id
        ]
    return rows


def test_happy_path_agent_actions_write_audit_with_actor_id(db: Session, hospital: dict):
    asha = hospital["asha"]
    out = start_workflow(
        db,
        actor_user_id=asha.id,
        actor_role=UserRole.PATIENT.value,
        raw_request=(
            "I need a cardiology follow-up next week and want to attach my old ECG."
        ),
        uploaded_files=[
            {
                "filename": "old_ecg.pdf",
                "content": b"%PDF-AUDIT-ECG",
                "mime_type": "application/pdf",
            }
        ],
    )
    db.commit()
    assert out["status"] == "completed"
    workflow_id = out["workflow_run_id"]

    events = _actions(db, workflow_run_id=workflow_id)
    by_action = {e.action: e for e in events}

    required = {
        "workflow.start",
        "safety.pass",
        "routing.classify",
        "appointment.book",
        "document.process",
        "followup.schedule",
        "workflow.finalize",
    }
    missing = required - set(by_action)
    assert not missing, f"Missing audit actions: {missing}; got={sorted(by_action)}"

    for action in required:
        event = by_action[action]
        assert event.actor_id == asha.id, f"{action} missing/wrong actor_id"
        assert event.actor_id  # non-empty
        assert (event.event_metadata or {}).get("role") == UserRole.PATIENT.value


def test_safety_block_writes_audit_with_actor_id(db: Session, hospital: dict):
    asha = hospital["asha"]
    out = start_workflow(
        db,
        actor_user_id=asha.id,
        actor_role=UserRole.PATIENT.value,
        raw_request="What medicine should I take for chest pain?",
    )
    db.commit()
    assert out["status"] == "interrupted"
    workflow_id = out["workflow_run_id"]

    events = _actions(db, workflow_run_id=workflow_id)
    actions = {e.action for e in events}
    assert "workflow.start" in actions
    assert "safety.block" in actions

    block = next(e for e in events if e.action == "safety.block")
    assert block.actor_id == asha.id
    assert (block.event_metadata or {}).get("role") == UserRole.PATIENT.value


def test_staff_review_decision_audits_staff_actor(db: Session, hospital: dict):
    asha = hospital["asha"]
    staff = hospital["staff"]
    cardio = hospital["cardio"]

    # Low-confidence request with no department keywords → routing HITL
    out = start_workflow(
        db,
        actor_user_id=asha.id,
        actor_role=UserRole.PATIENT.value,
        raw_request="Please help me with something at the hospital tomorrow",
    )
    db.commit()
    assert out["status"] == "interrupted"
    workflow_id = out["workflow_run_id"]

    resumed = resume_workflow(
        db,
        workflow_run_id=workflow_id,
        decision="approve",
        department_id=cardio.id,
        department_name="Cardiology",
        note="Assigned cardiology",
        actor_user_id=staff.id,
        actor_role=UserRole.STAFF.value,
    )
    db.commit()
    assert resumed["status"] == "completed"

    events = AuditRepository(db).list_recent(limit=200)
    review = [
        e
        for e in events
        if e.action == "staff_review.decision"
        and (
            e.entity_id == workflow_id
            or (e.event_metadata or {}).get("workflow_run_id") == workflow_id
        )
    ]
    assert review, "expected staff_review.decision audit event"
    assert review[0].actor_id == staff.id
    assert (review[0].event_metadata or {}).get("role") == UserRole.STAFF.value
    assert (review[0].event_metadata or {}).get("decision") == "approve"

"""Phase 4.6 — graph-level E2E (no HTTP). PRD §13.3.

Cases:
  - Happy path: cardiology + ECG → COMPLETED, appointment + document
  - Safety block: prescription → Escalation, no appointment
  - HITL: low-confidence routing → pause → resume → complete
  - Object scope: wrong patient_id → ToolScopeError (PermissionError)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, EscalationStatus, UserRole, WorkflowStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DocumentRepository,
    DoctorRepository,
    EscalationRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
    WorkflowRepository,
)
from services.workflow_service import resume_workflow, start_workflow
from tools._scope import ToolScopeError
from tools.appointment_tools import book_appointment


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("SMTP_DISABLED", "true")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
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
def memory() -> MemorySaver:
    return MemorySaver()


@pytest.fixture()
def hospital(db: Session) -> dict:
    asha = UserRepository(db).create(
        name="Asha Patient",
        email="asha.e2e@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    ravi = UserRepository(db).create(
        name="Ravi Patient",
        email="ravi.e2e@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.PATIENT.value,
    )
    ravi_profile = PatientRepository(db).create(user_id=ravi.id)

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
    return {
        "asha": asha,
        "ravi": ravi,
        "ravi_profile": ravi_profile,
        "cardio": cardio,
        "doctor": doctor,
    }


def test_e2e_happy_path_cardiology_with_ecg(db: Session, hospital, memory):
    out = start_workflow(
        db,
        actor_user_id=hospital["asha"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request=(
            "I need a cardiology follow-up next week and want to attach my old ECG."
        ),
        uploaded_files=[
            {
                "filename": "old_ecg.pdf",
                "content": b"%PDF-E2E-ECG-001",
                "mime_type": "application/pdf",
            }
        ],
        checkpointer=memory,
    )
    db.commit()

    assert out["status"] == "completed"
    assert out["confirmation"]["ok"] is True

    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value

    appt_id = out["state"]["appointment_result"]["appointment_id"]
    assert AppointmentRepository(db).get_by_id(appt_id) is not None

    stored = out["state"]["document_result"]["stored"]
    assert len(stored) == 1
    assert DocumentRepository(db).get_by_id(stored[0]["document_id"]) is not None
    assert stored[0]["document_type"] == DocumentType.ECG.value


def test_e2e_safety_block_creates_escalation_no_appointment(db: Session, hospital, memory):
    out = start_workflow(
        db,
        actor_user_id=hospital["asha"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="What medicine should I take for chest pain?",
        checkpointer=memory,
    )
    db.commit()

    assert out["status"] == "interrupted"
    assert out["interrupt"]["source"] == "safety"
    esc_id = out["state"]["safety_result"].get("escalation_id")
    assert esc_id
    esc = EscalationRepository(db).get_by_id(esc_id)
    assert esc is not None
    assert esc.status == EscalationStatus.PENDING.value

    # Resume closes HITL without booking
    resumed = resume_workflow(
        db,
        workflow_run_id=out["workflow_run_id"],
        decision="approve",
        note="Staff will call patient",
        checkpointer=memory,
        actor_role=UserRole.STAFF.value,
    )
    db.commit()

    assert resumed["status"] == "completed"
    assert resumed["confirmation"]["ok"] is False
    assert AppointmentRepository(db).list_for_patient(resumed["patient_id"]) == []
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.BLOCKED_SAFETY.value


def test_e2e_hitl_low_confidence_routing_resume_completes(db: Session, hospital, memory):
    out = start_workflow(
        db,
        actor_user_id=hospital["asha"].id,
        actor_role=UserRole.PATIENT.value,
        raw_request="hello",
        checkpointer=memory,
    )
    db.commit()

    assert out["status"] == "interrupted"
    assert out["interrupt"]["source"] == "routing"
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.WAITING_HITL.value

    resumed = resume_workflow(
        db,
        workflow_run_id=out["workflow_run_id"],
        decision="approve",
        department_id=hospital["cardio"].id,
        department_name="Cardiology",
        note="Assign Cardiology",
        checkpointer=memory,
        actor_role=UserRole.STAFF.value,
    )
    db.commit()

    assert resumed["status"] == "completed"
    assert resumed["state"]["appointment_result"]["ok"] is True
    assert resumed["confirmation"]["ok"] is True
    run = WorkflowRepository(db).get_by_id(out["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value


def test_e2e_object_scope_wrong_patient_id_raises(db: Session, hospital):
    """Wrong patient_id in state → tool raises PermissionError (ToolScopeError)."""
    asha = hospital["asha"]
    asha_profile = PatientRepository(db).create(user_id=asha.id)
    db.commit()

    # Actor is Asha, but state.patient_id points at Ravi
    bad_state: GraphState = {
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
        "patient_id": hospital["ravi_profile"].id,
    }

    with pytest.raises(ToolScopeError, match="another patient|cannot"):
        book_appointment(
            bad_state,
            db,
            slot_id="nonexistent-slot",
        )

    # Also: target mismatch vs workflow subject
    mismatched: GraphState = {
        "actor_user_id": asha.id,
        "actor_role": UserRole.PATIENT.value,
        "patient_id": asha_profile.id,
    }
    with pytest.raises(ToolScopeError):
        # assert_tool_scope is called with target; book uses state patient_id
        # Simulate cross-patient by forcing scope check directly
        from tools._scope import assert_tool_scope

        assert_tool_scope(mismatched, hospital["ravi_profile"].id, db)

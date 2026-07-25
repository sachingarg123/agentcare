"""Phase 3.9 — unit tests for each agent node with fixture state.

Exit criteria: each node returns a structured state update; safety blocks
clinical prompts. Also exercises a manual happy-path chain (no LangGraph yet).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.appointment_node import appointment_node
from agents.coordinator_node import coordinator_finalize, coordinator_init
from agents.document_node import document_node
from agents.followup_node import followup_node
from agents.routing_node import routing_node
from agents.safety_node import safety_node
from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, SlotStatus, UserRole, WorkflowStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    EscalationRepository,
    PatientRepository,
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
    """Seed user + Cardiology + doctor + two available slots."""
    user = UserRepository(db).create(
        name="Asha Patient",
        email="asha.agents@example.com",
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
    slot_a = SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    slot_b = SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db.commit()
    return {
        "user": user,
        "cardio": cardio,
        "doctor": doctor,
        "slot_a": slot_a,
        "slot_b": slot_b,
        "start": start,
    }


def _merge(state: GraphState, update: GraphState) -> GraphState:
    return {**state, **update}


# ---------------------------------------------------------------------------
# Per-node structured updates
# ---------------------------------------------------------------------------


def test_coordinator_init_returns_identity(db: Session, hospital):
    state: GraphState = {
        "actor_user_id": hospital["user"].id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "Book cardiology",
    }
    update = coordinator_init(state, db)
    db.commit()
    assert update["current_step"] == "coordinator_init"
    assert update["patient_id"]
    assert update["workflow_run_id"]
    assert PatientRepository(db).get_by_id(update["patient_id"]) is not None


def test_safety_node_blocks_clinical_prompt(db: Session, hospital):
    """PRD exit criteria: safety node blocks clinical prompt in test."""
    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "What medicine should I take for chest pain?",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "What medicine should I take for chest pain?",
        },
        init,
    )
    update = safety_node(state, db)
    db.commit()

    assert update["current_step"] == "safety"
    assert update["safety_result"]["safe"] is False
    assert update["safety_result"]["blocked"] is True
    assert update["hitl_required"] is True
    assert update["safety_result"]["escalation_id"]
    esc = EscalationRepository(db).get_by_id(update["safety_result"]["escalation_id"])
    assert esc is not None


def test_safety_node_allows_admin_request(db: Session, hospital):
    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "I need a cardiology follow-up next week",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "I need a cardiology follow-up next week",
        },
        init,
    )
    update = safety_node(state, db)
    assert update["safety_result"]["safe"] is True
    assert update["hitl_required"] is False


def test_routing_node_maps_department(db: Session, hospital):
    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology appointment and attach ECG",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology appointment and attach ECG",
            "safety_result": {"safe": True},
        },
        init,
    )
    update = routing_node(state, db)
    assert update["current_step"] == "routing"
    assert update["routing_result"]["department_name"] == "Cardiology"
    assert update["routing_result"]["department_id"] == hospital["cardio"].id
    assert update["administrative_intents"]


def test_appointment_node_books_slot(db: Session, hospital):
    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology",
            "routing_result": {
                "department_id": hospital["cardio"].id,
                "department_name": "Cardiology",
                "intents": ["BOOK_APPOINTMENT"],
            },
            "administrative_intents": ["BOOK_APPOINTMENT"],
        },
        init,
    )
    update = appointment_node(state, db)
    db.commit()
    assert update["appointment_result"]["ok"] is True
    assert update["appointment_result"]["appointment_id"]
    slot = SlotRepository(db).get_by_id(update["appointment_result"]["slot_id"])
    assert slot is not None and slot.status == SlotStatus.BOOKED.value


def test_document_node_stores_upload(db: Session, hospital, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Upload ECG",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "routing_result": {
                "department_id": hospital["cardio"].id,
                "department_name": "Cardiology",
            },
            "uploaded_files": [
                {"filename": "old_ecg.pdf", "content": b"%PDF-AGENTS-ECG", "mime_type": "application/pdf"}
            ],
        },
        init,
    )
    update = document_node(state, db)
    db.commit()
    assert update["document_result"]["ok"] is True
    assert len(update["document_result"]["stored"]) == 1
    assert update["document_result"]["missing"] == []
    get_settings.cache_clear()


def test_followup_node_schedules_after_booking(db: Session, hospital, monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology",
        },
        db,
    )
    db.commit()
    state = _merge(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "routing_result": {
                "department_id": hospital["cardio"].id,
                "department_name": "Cardiology",
                "intents": ["BOOK_APPOINTMENT"],
            },
            "administrative_intents": ["BOOK_APPOINTMENT"],
        },
        init,
    )
    state = _merge(state, appointment_node(state, db))
    db.commit()
    update = followup_node(state, db)
    db.commit()
    assert update["followup_result"]["ok"] is True
    assert len(update["followup_result"]["reminder_ids"]) == 2
    assert update["followup_result"]["notification_status"] == "SKIPPED"
    get_settings.cache_clear()


def test_coordinator_finalize_returns_confirmation(db: Session, hospital):
    init = coordinator_init(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "Book cardiology",
        },
        db,
    )
    db.commit()
    state: GraphState = {
        **init,
        "actor_user_id": hospital["user"].id,
        "actor_role": UserRole.PATIENT.value,
        "safety_result": {"safe": True},
        "routing_result": {"department_name": "Cardiology"},
        "appointment_result": {
            "ok": True,
            "appointment_id": "appt-x",
            "doctor_name": "Dr. Mehta",
            "start_time": hospital["start"].isoformat(),
        },
        "document_result": {"stored": [], "missing": []},
        "followup_result": {"reminder_ids": ["r1"], "notification_status": "SKIPPED"},
    }
    update = coordinator_finalize(state, db)
    db.commit()
    assert update["current_step"] == "coordinator_finalize"
    assert update["confirmation"]["ok"] is True
    assert "Dr. Mehta" in update["confirmation"]["summary"]


# ---------------------------------------------------------------------------
# Manual end-to-end chain (Phase 3 — no LangGraph graph yet)
# ---------------------------------------------------------------------------


def test_happy_path_node_chain(db: Session, hospital, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    state: GraphState = {
        "actor_user_id": hospital["user"].id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": (
            "I need a cardiology follow-up next week and want to attach my old ECG."
        ),
        "uploaded_files": [
            {
                "filename": "old_ecg.pdf",
                "content": b"%PDF-HAPPY-PATH-ECG",
                "mime_type": "application/pdf",
            }
        ],
    }

    state = _merge(state, coordinator_init(state, db))
    db.commit()
    state = _merge(state, safety_node(state, db))
    assert state["safety_result"]["safe"] is True

    state = _merge(state, routing_node(state, db))
    assert state["routing_result"]["department_name"] == "Cardiology"

    state = _merge(state, appointment_node(state, db))
    db.commit()
    assert state["appointment_result"]["ok"] is True
    appt_id = state["appointment_result"]["appointment_id"]
    assert AppointmentRepository(db).get_by_id(appt_id) is not None

    state = _merge(state, document_node(state, db))
    db.commit()
    assert len(state["document_result"]["stored"]) == 1

    state = _merge(state, followup_node(state, db))
    db.commit()
    assert state["followup_result"]["ok"] is True

    state = _merge(state, coordinator_finalize(state, db))
    db.commit()

    assert state["confirmation"]["ok"] is True
    assert state["confirmation"]["appointment_id"] == appt_id
    run = WorkflowRepository(db).get_by_id(state["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value

    get_settings.cache_clear()


def test_safety_block_chain_skips_booking(db: Session, hospital):
    state: GraphState = {
        "actor_user_id": hospital["user"].id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "What medicine should I take?",
    }
    state = _merge(state, coordinator_init(state, db))
    db.commit()
    state = _merge(state, safety_node(state, db))
    db.commit()
    assert state["hitl_required"] is True

    # Mimic Phase 4 short-circuit: go straight to finalize (no appointment)
    state = _merge(state, coordinator_finalize(state, db))
    db.commit()

    assert state["confirmation"]["ok"] is False
    assert AppointmentRepository(db).list_for_patient(state["patient_id"]) == []
    run = WorkflowRepository(db).get_by_id(state["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.BLOCKED_SAFETY.value

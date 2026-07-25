"""Phase 4.1–4.2 — LangGraph pipeline + HITL interrupt/resume."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langgraph.types import Command
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.pipeline import (
    compile_workflow,
    route_after_appointment,
    route_after_routing,
    route_after_safety,
    route_after_staff_review,
)
from db.models import Base, DocumentType, UserRole, WorkflowStatus
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
        email="asha.pipeline@example.com",
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
    SlotRepository(db).create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db.commit()
    return {"user": user, "cardio": cardio, "doctor": doctor, "start": start}


def _config() -> dict:
    return {"configurable": {"thread_id": str(uuid4())}}


def test_route_after_safety_branches():
    assert route_after_safety({"safety_result": {"safe": True}}) == "routing"
    assert route_after_safety({"safety_result": {"safe": False}}) == "staff_review"


def test_route_after_routing_branches():
    assert (
        route_after_routing({"routing_result": {"needs_staff_review": False}})
        == "appointment"
    )
    assert (
        route_after_routing({"routing_result": {"needs_staff_review": True}})
        == "staff_review"
    )


def test_route_after_appointment_branches():
    assert (
        route_after_appointment({"appointment_result": {"ok": True}, "hitl_required": False})
        == "document"
    )
    assert (
        route_after_appointment(
            {"appointment_result": {"ok": False}, "hitl_required": True}
        )
        == "staff_review"
    )


def test_route_after_staff_review_branches():
    assert (
        route_after_staff_review(
            {
                "hitl_source": "safety",
                "staff_decision": {"decision": "approve"},
                "safety_result": {"safe": False},
            }
        )
        == "coordinator_finalize"
    )
    assert (
        route_after_staff_review(
            {
                "hitl_source": "routing",
                "staff_decision": {"decision": "approve"},
                "routing_result": {"department_id": "d1"},
            }
        )
        == "appointment"
    )
    assert (
        route_after_staff_review(
            {
                "hitl_source": "routing",
                "staff_decision": {"decision": "reject"},
                "routing_result": {"department_id": "d1"},
            }
        )
        == "coordinator_finalize"
    )
    assert (
        route_after_staff_review(
            {
                "hitl_source": "appointment",
                "staff_decision": {"decision": "approve"},
            }
        )
        == "document"
    )


def test_pipeline_happy_path(db: Session, hospital, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    graph = compile_workflow(db)
    final = graph.invoke(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": (
                "I need a cardiology follow-up next week and want to attach my old ECG."
            ),
            "uploaded_files": [
                {
                    "filename": "old_ecg.pdf",
                    "content": b"%PDF-PIPELINE-ECG",
                    "mime_type": "application/pdf",
                }
            ],
        },
        _config(),
    )
    db.commit()

    assert final["current_step"] == "coordinator_finalize"
    assert final["confirmation"]["ok"] is True
    assert AppointmentRepository(db).get_by_id(
        final["appointment_result"]["appointment_id"]
    )
    run = WorkflowRepository(db).get_by_id(final["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.COMPLETED.value
    get_settings.cache_clear()


def test_pipeline_safety_block_interrupts_then_resume(db: Session, hospital):
    graph = compile_workflow(db)
    config = _config()

    paused = graph.invoke(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "What medicine should I take for chest pain?",
        },
        config,
    )
    db.commit()

    assert "__interrupt__" in paused
    assert paused["safety_result"]["safe"] is False
    assert not (paused.get("appointment_result") or {}).get("ok")
    run = WorkflowRepository(db).get_by_id(paused["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.WAITING_HITL.value

    final = graph.invoke(
        Command(resume={"decision": "approve", "note": "Staff will call patient"}),
        config,
    )
    db.commit()

    assert final["current_step"] == "coordinator_finalize"
    assert final["confirmation"]["ok"] is False
    assert AppointmentRepository(db).list_for_patient(final["patient_id"]) == []
    run = WorkflowRepository(db).get_by_id(final["workflow_run_id"])
    assert run is not None
    assert run.status == WorkflowStatus.BLOCKED_SAFETY.value


def test_pipeline_routing_hitl_resume_continues_to_book(db: Session, hospital, monkeypatch):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()

    graph = compile_workflow(db)
    config = _config()

    paused = graph.invoke(
        {
            "actor_user_id": hospital["user"].id,
            "actor_role": UserRole.PATIENT.value,
            "raw_request": "hello",  # low confidence → staff review
        },
        config,
    )
    db.commit()

    assert "__interrupt__" in paused
    interrupt_val = paused["__interrupt__"][0].value
    assert interrupt_val["source"] == "routing"

    final = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "department_id": hospital["cardio"].id,
                "department_name": "Cardiology",
                "note": "Assign Cardiology",
            }
        ),
        config,
    )
    db.commit()

    assert final["current_step"] == "coordinator_finalize"
    assert final["routing_result"]["department_id"] == hospital["cardio"].id
    assert final["appointment_result"]["ok"] is True
    assert final["confirmation"]["ok"] is True
    get_settings.cache_clear()

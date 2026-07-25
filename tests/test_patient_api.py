"""Phase 5.3 — patient API: requests, appointments, documents, reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.config import get_settings
from db.models import AppointmentStatus, DocumentType, ReminderStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DocumentRepository,
    DoctorRepository,
    ReminderRepository,
    SlotRepository,
)
from services.workflow_service import set_checkpointer_override
from tests.conftest import auth_header, login


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("SMTP_DISABLED", "true")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    memory = MemorySaver()
    set_checkpointer_override(memory)
    yield
    set_checkpointer_override(None)
    get_settings.cache_clear()


@pytest.fixture()
def hospital(db_session):
    """Departments + slots so POST /requests can complete a happy path."""
    cardio = DepartmentRepository(db_session).create(
        name="Cardiology", description="Heart"
    )
    DepartmentRepository(db_session).add_document_requirement(
        department_id=cardio.id,
        document_type=DocumentType.ECG.value,
        required=True,
    )
    for name in ("Radiology", "General Medicine", "Orthopedics", "Dermatology"):
        DepartmentRepository(db_session).create(name=name, description=name)

    doctor = DoctorRepository(db_session).create(
        department_id=cardio.id, name="Dr. Mehta"
    )
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    slot = SlotRepository(db_session).create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    SlotRepository(db_session).create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    db_session.commit()
    return {"cardio": cardio, "doctor": doctor, "slot": slot}


@pytest.fixture()
def asha_appointment(db_session, hospital):
    asha_pid = db_session.info["asha_patient_id"]
    slot = hospital["slot"]
    appt = AppointmentRepository(db_session).book(
        patient_id=asha_pid,
        doctor_id=hospital["doctor"].id,
        slot=slot,
        reason="Follow-up",
    )
    rem = ReminderRepository(db_session).create(
        patient_id=asha_pid,
        appointment_id=appt.id,
        reminder_type="APPOINTMENT",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=12),
        status=ReminderStatus.SCHEDULED.value,
    )
    doc = DocumentRepository(db_session).create(
        patient_id=asha_pid,
        file_path="/tmp/asha_ecg.pdf",
        checksum="a" * 64,
        document_type=DocumentType.ECG.value,
    )
    db_session.commit()
    return {"appointment": appt, "reminder": rem, "document": doc}


def test_get_request_richer_than_probe_and_403_cross_patient(client, db_session):
    workflow_id = db_session.info["asha_workflow_id"]
    # Enrich state so summary fields surface
    from db.repositories import WorkflowRepository

    wf = WorkflowRepository(db_session).get_by_id(workflow_id)
    assert wf is not None
    WorkflowRepository(db_session).update_state(
        wf,
        current_step="coordinator_finalize",
        state={
            "confirmation": {"ok": True, "summary": "demo"},
            "routing_result": {"department_name": "Cardiology"},
        },
    )
    db_session.commit()

    asha = login(client, "asha.patient@example.com")
    ok = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(asha))
    assert ok.status_code == 200
    body = ok.json()
    assert body["id"] == workflow_id
    assert body["confirmation"]["ok"] is True
    assert body["routing_result"]["department_name"] == "Cardiology"

    ravi = login(client, "ravi.patient@example.com")
    denied = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(ravi))
    assert denied.status_code == 403


def test_staff_still_can_read_workflow(client, db_session):
    workflow_id = db_session.info["asha_workflow_id"]
    token = login(client, "sam.staff@example.com")
    resp = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(token))
    assert resp.status_code == 200


def test_submit_request_happy_path(client, hospital):
    token = login(client, "asha.patient@example.com")
    resp = client.post(
        "/api/v1/requests",
        data={
            "raw_request": (
                "I need a cardiology follow-up next week and want to attach my old ECG."
            )
        },
        files={
            "files": ("old_ecg.pdf", b"%PDF-API-ECG-001", "application/pdf"),
        },
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["workflow_run_id"]
    assert body["confirmation"]["ok"] is True

    detail = client.get(
        f"/api/v1/requests/{body['workflow_run_id']}",
        headers=auth_header(token),
    )
    assert detail.status_code == 200
    assert detail.json()["appointment_result"] is not None


def test_staff_cannot_submit_request(client):
    token = login(client, "sam.staff@example.com")
    resp = client.post(
        "/api/v1/requests",
        data={"raw_request": "Book cardiology"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403


def test_list_appointments_own_only(client, asha_appointment):
    asha = login(client, "asha.patient@example.com")
    mine = client.get("/api/v1/appointments", headers=auth_header(asha))
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    row = mine.json()[0]
    assert row["id"] == asha_appointment["appointment"].id
    assert row["doctor_name"]
    assert row["start_time"]
    assert row["end_time"]

    ravi = login(client, "ravi.patient@example.com")
    other = client.get("/api/v1/appointments", headers=auth_header(ravi))
    assert other.status_code == 200
    assert other.json() == []


def test_cancel_appointment_own_and_403_cross(client, asha_appointment):
    appt_id = asha_appointment["appointment"].id
    asha = login(client, "asha.patient@example.com")
    ok = client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        headers=auth_header(asha),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == AppointmentStatus.CANCELLED.value

    # Re-book style: create another for cross-patient deny
    # Use ravi against already-cancelled? Better seed second appt for asha via remaining slot
    ravi = login(client, "ravi.patient@example.com")
    denied = client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        headers=auth_header(ravi),
    )
    assert denied.status_code == 403


def test_documents_list_and_upload(client, asha_appointment, db_session, tmp_path):
    asha = login(client, "asha.patient@example.com")
    listed = client.get("/api/v1/documents", headers=auth_header(asha))
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    ravi = login(client, "ravi.patient@example.com")
    assert client.get("/api/v1/documents", headers=auth_header(ravi)).json() == []

    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("blood_report.pdf", b"%PDF-BLOOD-1", "application/pdf")},
        headers=auth_header(asha),
    )
    assert up.status_code == 201, up.text
    assert up.json()["patient_id"] == db_session.info["asha_patient_id"]
    assert up.json()["document_type"]

    listed2 = client.get("/api/v1/documents", headers=auth_header(asha))
    assert len(listed2.json()) == 2


def test_list_reminders_own_only(client, asha_appointment):
    asha = login(client, "asha.patient@example.com")
    rem = client.get("/api/v1/reminders", headers=auth_header(asha))
    assert rem.status_code == 200
    assert len(rem.json()) == 1
    assert rem.json()[0]["id"] == asha_appointment["reminder"].id

    ravi = login(client, "ravi.patient@example.com")
    assert client.get("/api/v1/reminders", headers=auth_header(ravi)).json() == []

"""Phase 5.4 — staff API: request queue, escalations, HITL resume."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.config import get_settings
from db.models import DocumentType, EscalationStatus, WorkflowStatus
from db.repositories import (
    DepartmentRepository,
    DoctorRepository,
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
    set_checkpointer_override(MemorySaver())
    yield
    set_checkpointer_override(None)
    get_settings.cache_clear()


@pytest.fixture()
def hospital(db_session):
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
    SlotRepository(db_session).create(
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
    return {"cardio": cardio, "doctor": doctor}


def test_patient_cannot_list_staff_escalations(client):
    token = login(client, "asha.patient@example.com")
    resp = client.get("/api/v1/staff/escalations", headers=auth_header(token))
    assert resp.status_code == 403


def test_staff_lists_requests_including_asha(client, db_session):
    token = login(client, "sam.staff@example.com")
    resp = client.get("/api/v1/staff/requests", headers=auth_header(token))
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert db_session.info["asha_workflow_id"] in ids


def test_resolve_safety_escalation_via_staff_api(client, hospital):
    asha = login(client, "asha.patient@example.com")
    clinical = "What medicine should I take for chest pain?"
    submitted = client.post(
        "/api/v1/requests",
        data={"raw_request": clinical},
        headers=auth_header(asha),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "interrupted"
    assert body["interrupt"]["source"] == "safety"
    assert body["interrupt"]["raw_request"] == clinical

    staff = login(client, "sam.staff@example.com")
    pending = client.get("/api/v1/staff/escalations", headers=auth_header(staff))
    assert pending.status_code == 200
    assert len(pending.json()) >= 1
    esc_id = pending.json()[0]["id"]
    assert pending.json()[0].get("raw_request_preview")

    detail = client.get(f"/api/v1/staff/escalations/{esc_id}", headers=auth_header(staff))
    assert detail.status_code == 200, detail.text
    pack = detail.json()
    assert pack["raw_request"] == clinical
    assert pack["patient_name"]
    assert pack["hitl_source"] == "safety"
    assert pack["safety_result"]

    resolved = client.post(
        f"/api/v1/staff/escalations/{esc_id}/resolve",
        json={"decision": "approve", "note": "Staff will call patient"},
        headers=auth_header(staff),
    )
    assert resolved.status_code == 200, resolved.text
    out = resolved.json()
    assert out["status"] == "completed"
    assert out["escalation_status"] == EscalationStatus.APPROVED.value
    assert out["confirmation"]["ok"] is False

    all_esc = client.get(
        "/api/v1/staff/escalations?pending_only=false",
        headers=auth_header(staff),
    )
    match = next(e for e in all_esc.json() if e["id"] == esc_id)
    assert match["status"] == EscalationStatus.APPROVED.value
    assert match["reviewed_by"]


def test_resume_routing_hitl_via_workflows_resume(client, hospital):
    asha = login(client, "asha.patient@example.com")
    submitted = client.post(
        "/api/v1/requests",
        data={"raw_request": "hello"},
        headers=auth_header(asha),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "interrupted"
    workflow_id = body["workflow_run_id"]
    cardio_id = hospital["cardio"].id

    staff = login(client, "sam.staff@example.com")
    resumed = client.post(
        f"/api/v1/workflows/{workflow_id}/resume",
        json={
            "decision": "approve",
            "department_id": cardio_id,
            "department_name": "Cardiology",
            "note": "Routed to cardio",
        },
        headers=auth_header(staff),
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"

    detail = client.get(
        f"/api/v1/requests/{workflow_id}",
        headers=auth_header(staff),
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == WorkflowStatus.COMPLETED.value


def test_patient_cannot_resume_workflow(client, db_session):
    token = login(client, "asha.patient@example.com")
    resp = client.post(
        f"/api/v1/workflows/{db_session.info['asha_workflow_id']}/resume",
        json={"decision": "approve"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403

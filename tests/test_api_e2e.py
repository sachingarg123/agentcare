"""Phase 7.2 — HTTP E2E via TestClient (PRD §13.3 / 7.2).

Covers the required checklist in one place:
  - Patient happy path (submit → GET status from DB)
  - RBAC matrix (cross-patient 403, staff read, staff≠admin, admin CRUD)
  - Clinical trap via API (escalation, no appointment)

Uses seeded users from conftest; SMTP off; in-memory checkpointer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.config import get_settings
from db.models import DocumentType, EscalationStatus
from db.repositories import (
    AppointmentRepository,
    DepartmentRepository,
    DoctorRepository,
    EscalationRepository,
    SlotRepository,
    WorkflowRepository,
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_patient_submit_request_then_get_status(client, hospital):
    """Patient submits request → GET status 200 with confirmation from DB."""
    token = login(client, "asha.patient@example.com")
    submitted = client.post(
        "/api/v1/requests",
        data={
            "raw_request": (
                "I need a cardiology follow-up next week and want to attach my old ECG."
            )
        },
        files={
            "files": ("old_ecg.pdf", b"%PDF-E2E-ECG-001", "application/pdf"),
        },
        headers=auth_header(token),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "completed"
    assert body["workflow_run_id"]
    assert body["confirmation"]["ok"] is True

    detail = client.get(
        f"/api/v1/requests/{body['workflow_run_id']}",
        headers=auth_header(token),
    )
    assert detail.status_code == 200
    summary = detail.json()
    assert summary["id"] == body["workflow_run_id"]
    assert summary["confirmation"]["ok"] is True
    assert summary["routing_result"]["department_name"] == "Cardiology"
    assert summary["appointment_result"] is not None


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------


def test_patient_a_cannot_get_patient_b_workflow(client, db_session):
    """Patient A cannot GET Patient B's workflow → 403."""
    workflow_id = db_session.info["asha_workflow_id"]

    asha = login(client, "asha.patient@example.com")
    ok = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(asha))
    assert ok.status_code == 200

    ravi = login(client, "ravi.patient@example.com")
    denied = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(ravi))
    assert denied.status_code == 403


def test_staff_can_get_any_workflow(client, db_session):
    """STAFF can GET any workflow → 200."""
    workflow_id = db_session.info["asha_workflow_id"]
    token = login(client, "sam.staff@example.com")
    resp = client.get(f"/api/v1/requests/{workflow_id}", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == workflow_id


def test_staff_cannot_post_departments(client):
    """STAFF cannot POST /staff/departments → 403."""
    token = login(client, "sam.staff@example.com")
    resp = client.post(
        "/api/v1/staff/departments",
        json={"name": "E2E-Staff-Should-Fail", "description": "nope"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403


def test_admin_can_crud_department(client):
    """ADMIN can CRUD department → 201 (create) + read/update/delete."""
    token = login(client, "ada.admin@example.com")
    headers = auth_header(token)

    created = client.post(
        "/api/v1/staff/departments",
        json={"name": "E2E-Admin-Dept", "description": "Eval clinic"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    dept = created.json()
    assert dept["name"] == "E2E-Admin-Dept"
    dept_id = dept["id"]

    listed = client.get("/api/v1/staff/departments", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == dept_id for d in listed.json())

    got = client.get(f"/api/v1/staff/departments/{dept_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == dept_id

    patched = client.patch(
        f"/api/v1/staff/departments/{dept_id}",
        json={"description": "Updated eval clinic"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "Updated eval clinic"

    deleted = client.delete(f"/api/v1/staff/departments/{dept_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["id"] == dept_id


# ---------------------------------------------------------------------------
# Clinical trap via API
# ---------------------------------------------------------------------------


def test_clinical_trap_via_api_escalates_without_appointment(client, hospital, db_session):
    """Clinical trap via API → 200 with escalation; no appointment."""
    token = login(client, "asha.patient@example.com")
    clinical = "What medicine should I take for chest pain?"
    asha_patient_id = db_session.info["asha_patient_id"]
    appts_before = {
        a.id for a in AppointmentRepository(db_session).list_for_patient(asha_patient_id)
    }

    submitted = client.post(
        "/api/v1/requests",
        data={"raw_request": clinical},
        headers=auth_header(token),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "interrupted"
    assert body["hitl_required"] is True
    assert body["interrupt"]["source"] == "safety"
    assert body["interrupt"]["raw_request"] == clinical

    workflow_id = body["workflow_run_id"]
    detail = client.get(
        f"/api/v1/requests/{workflow_id}",
        headers=auth_header(token),
    )
    assert detail.status_code == 200
    summary = detail.json()
    assert summary["safety_result"] is not None
    assert summary["safety_result"].get("safe") is False
    assert not (summary.get("appointment_result") or {}).get("appointment_id")

    pending = EscalationRepository(db_session).list_pending()
    match = [e for e in pending if e.workflow_run_id == workflow_id]
    assert len(match) == 1
    assert match[0].status == EscalationStatus.PENDING.value

    appts_after = {
        a.id for a in AppointmentRepository(db_session).list_for_patient(asha_patient_id)
    }
    assert appts_after == appts_before

    wf = WorkflowRepository(db_session).get_by_id(workflow_id)
    assert wf is not None
    assert wf.status != "COMPLETED"

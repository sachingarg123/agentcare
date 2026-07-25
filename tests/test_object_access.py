"""Phase 1.6 — object-level access: patient A ≠ patient B (PRD §4.4 Layer 2)."""

from fastapi import HTTPException

from auth.ownership import assert_patient_owns_workflow
from db.models import UserRole
from db.repositories import UserRepository, WorkflowRepository
from tests.conftest import auth_header, login


def test_patient_a_gets_403_on_patient_b_workflow_http(client, db_session):
    """Exit criteria: Asha cannot GET Ravi's workflow — but we use Asha's workflow
    and try as Ravi (patient B accessing patient A's resource).
    """
    workflow_id = db_session.info["asha_workflow_id"]

    # Owner (Asha) — OK
    asha_token = login(client, "asha.patient@example.com")
    ok = client.get(
        f"/api/v1/requests/{workflow_id}",
        headers=auth_header(asha_token),
    )
    assert ok.status_code == 200
    assert ok.json()["id"] == workflow_id

    # Other patient (Ravi) — 403
    ravi_token = login(client, "ravi.patient@example.com")
    denied = client.get(
        f"/api/v1/requests/{workflow_id}",
        headers=auth_header(ravi_token),
    )
    assert denied.status_code == 403
    assert "Not your workflow" in denied.json()["detail"]


def test_staff_can_read_any_workflow(client, db_session):
    workflow_id = db_session.info["asha_workflow_id"]
    token = login(client, "sam.staff@example.com")
    resp = client.get(
        f"/api/v1/requests/{workflow_id}",
        headers=auth_header(token),
    )
    assert resp.status_code == 200


def test_ownership_helper_directly(db_session):
    """Unit-level: assert_patient_owns_workflow raises HTTPException 403."""
    users = UserRepository(db_session)
    asha = users.get_by_email("asha.patient@example.com")
    ravi = users.get_by_email("ravi.patient@example.com")
    workflow = WorkflowRepository(db_session).get_by_id(
        db_session.info["asha_workflow_id"]
    )

    # Owner — no raise
    assert_patient_owns_workflow(asha, workflow, db_session)

    # Other patient — 403
    try:
        assert_patient_owns_workflow(ravi, workflow, db_session)
        raised = False
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 403
    assert raised

    # Staff — allowed
    staff = users.get_by_email("sam.staff@example.com")
    assert staff.role == UserRole.STAFF.value
    assert_patient_owns_workflow(staff, workflow, db_session)

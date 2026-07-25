"""Phase 5.6 — GET /staff/audit (STAFF / ADMIN read-only)."""

from __future__ import annotations

from db.repositories import AuditRepository
from tests.conftest import auth_header, login


def test_patient_cannot_read_audit(client):
    token = login(client, "asha.patient@example.com")
    resp = client.get("/api/v1/staff/audit", headers=auth_header(token))
    assert resp.status_code == 403


def test_staff_lists_audit_after_admin_action(client, db_session):
    admin = login(client, "ada.admin@example.com")
    created = client.post(
        "/api/v1/staff/departments",
        json={"name": "Audit-Trail-Dept"},
        headers=auth_header(admin),
    )
    assert created.status_code == 201
    dept_id = created.json()["id"]

    staff = login(client, "sam.staff@example.com")
    resp = client.get("/api/v1/staff/audit", headers=auth_header(staff))
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()}
    assert "department_create" in actions

    filtered = client.get(
        f"/api/v1/staff/audit?entity_type=department&entity_id={dept_id}",
        headers=auth_header(staff),
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1
    assert all(e["entity_id"] == dept_id for e in filtered.json())
    assert filtered.json()[0]["actor_id"]


def test_audit_filter_by_actor(client, db_session):
    admin = login(client, "ada.admin@example.com")
    client.post(
        "/api/v1/staff/departments",
        json={"name": "Audit-Actor-Dept"},
        headers=auth_header(admin),
    )
    from db.repositories import UserRepository

    ada = UserRepository(db_session).get_by_email("ada.admin@example.com")
    assert ada is not None

    staff = login(client, "sam.staff@example.com")
    resp = client.get(
        f"/api/v1/staff/audit?actor_id={ada.id}",
        headers=auth_header(staff),
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert all(e["actor_id"] == ada.id for e in resp.json())


def test_audit_repo_list_filtered(db_session):
    from db.repositories import UserRepository

    ada = UserRepository(db_session).get_by_email("ada.admin@example.com")
    assert ada is not None
    AuditRepository(db_session).create(
        actor_id=ada.id,
        action="unit_test_action",
        entity_type="test",
        entity_id="e1",
        event_metadata={"k": "v"},
    )
    db_session.commit()
    rows = AuditRepository(db_session).list_filtered(
        action="unit_test_action", limit=10
    )
    assert len(rows) == 1
    assert rows[0].event_metadata["k"] == "v"

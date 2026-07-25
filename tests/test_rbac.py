"""Phase 1.6 — route-level RBAC tests (PRD §4.4 Layer 1).

Admin-only CRUD gates move to Phase 5.5; staff gates use real staff routes.
"""

from tests.conftest import auth_header, login


def test_login_returns_jwt_with_role_claim(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "asha.patient@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "PATIENT"
    assert body["access_token"]
    assert body["user_id"]


def test_login_wrong_password_401(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "asha.patient@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_current_user(client):
    token = login(client, "sam.staff@example.com")
    resp = client.get("/api/v1/auth/me", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "STAFF"
    assert resp.json()["email"] == "sam.staff@example.com"


def test_patient_cannot_access_staff_escalations(client):
    token = login(client, "asha.patient@example.com")
    resp = client.get("/api/v1/staff/escalations", headers=auth_header(token))
    assert resp.status_code == 403


def test_staff_can_access_staff_escalations(client):
    token = login(client, "sam.staff@example.com")
    resp = client.get("/api/v1/staff/escalations", headers=auth_header(token))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_can_access_staff_escalations(client):
    token = login(client, "ada.admin@example.com")
    resp = client.get("/api/v1/staff/escalations", headers=auth_header(token))
    assert resp.status_code == 200


def test_patient_cannot_list_staff_requests(client):
    token = login(client, "asha.patient@example.com")
    resp = client.get("/api/v1/staff/requests", headers=auth_header(token))
    assert resp.status_code == 403


def test_staff_cannot_post_departments(client):
    token = login(client, "sam.staff@example.com")
    resp = client.post(
        "/api/v1/staff/departments",
        json={"name": "ShouldFail"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403


def test_admin_can_post_departments(client):
    token = login(client, "ada.admin@example.com")
    resp = client.post(
        "/api/v1/staff/departments",
        json={"name": "RBAC-Admin-Dept"},
        headers=auth_header(token),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "RBAC-Admin-Dept"

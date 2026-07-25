"""Phase 5.2 — auth register / login / me."""

from __future__ import annotations

from tests.conftest import DEMO_PASSWORD, auth_header, login


def test_register_creates_patient_and_returns_token(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "name": "New Patient",
            "email": "new.patient@example.com",
            "password": "securepass1",
            "phone": "+91-99999",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "PATIENT"
    assert body["access_token"]
    assert body["user_id"]

    me = client.get("/api/v1/auth/me", headers=auth_header(body["access_token"]))
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["email"] == "new.patient@example.com"
    assert me_body["role"] == "PATIENT"
    assert me_body["patient_id"]


def test_register_duplicate_email_conflict(client):
    payload = {
        "name": "Dup",
        "email": "dup.patient@example.com",
        "password": "securepass1",
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    again = client.post("/api/v1/auth/register", json=payload)
    assert again.status_code == 409
    assert "already" in again.json()["detail"].lower()


def test_register_short_password_rejected(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"name": "X", "email": "short@example.com", "password": "short"},
    )
    assert resp.status_code == 422


def test_login_and_me_seed_user(client):
    token = login(client, "asha.patient@example.com", DEMO_PASSWORD)
    me = client.get("/api/v1/auth/me", headers=auth_header(token))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "asha.patient@example.com"
    assert body["patient_id"]


def test_login_bad_password(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "asha.patient@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401

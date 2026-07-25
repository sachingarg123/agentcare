"""Phase 5.5 — admin CRUD for departments, doctors, slots (ADMIN only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header, login


def test_staff_cannot_create_department(client):
    token = login(client, "sam.staff@example.com")
    resp = client.post(
        "/api/v1/staff/departments",
        json={"name": "Neurology", "description": "Brain"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403


def test_patient_cannot_list_departments(client):
    token = login(client, "asha.patient@example.com")
    resp = client.get("/api/v1/staff/departments", headers=auth_header(token))
    assert resp.status_code == 403


def test_admin_department_crud(client):
    token = login(client, "ada.admin@example.com")
    headers = auth_header(token)

    created = client.post(
        "/api/v1/staff/departments",
        json={"name": "Neurology", "description": "Brain"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    dept_id = created.json()["id"]
    assert created.json()["name"] == "Neurology"
    assert created.json()["active"] is True

    listed = client.get("/api/v1/staff/departments", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == dept_id for d in listed.json())

    patched = client.patch(
        f"/api/v1/staff/departments/{dept_id}",
        json={"description": "Brain & nerves"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Brain & nerves"

    deleted = client.delete(f"/api/v1/staff/departments/{dept_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["active"] is False


def test_admin_doctor_and_slot_crud(client):
    token = login(client, "ada.admin@example.com")
    headers = auth_header(token)

    dept = client.post(
        "/api/v1/staff/departments",
        json={"name": "ENT"},
        headers=headers,
    ).json()

    doctor = client.post(
        "/api/v1/staff/doctors",
        json={"department_id": dept["id"], "name": "Dr. Rao"},
        headers=headers,
    )
    assert doctor.status_code == 201, doctor.text
    doctor_id = doctor.json()["id"]

    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    end = start + timedelta(minutes=30)
    slot = client.post(
        "/api/v1/staff/slots",
        json={
            "doctor_id": doctor_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        },
        headers=headers,
    )
    assert slot.status_code == 201, slot.text
    slot_id = slot.json()["id"]
    assert slot.json()["status"] == "AVAILABLE"

    listed = client.get(
        f"/api/v1/staff/slots?doctor_id={doctor_id}",
        headers=headers,
    )
    assert listed.status_code == 200
    assert any(s["id"] == slot_id for s in listed.json())

    patched = client.patch(
        f"/api/v1/staff/slots/{slot_id}",
        json={"end_time": (end + timedelta(minutes=15)).isoformat()},
        headers=headers,
    )
    assert patched.status_code == 200

    deleted = client.delete(f"/api/v1/staff/slots/{slot_id}", headers=headers)
    assert deleted.status_code == 204

    gone = client.get(f"/api/v1/staff/slots/{slot_id}", headers=headers)
    assert gone.status_code == 404

    deactivated = client.delete(f"/api/v1/staff/doctors/{doctor_id}", headers=headers)
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False


def test_staff_cannot_create_slot(client):
    # Staff has no way to create dept/doctor first; just hit slots create
    token = login(client, "sam.staff@example.com")
    start = datetime.now(timezone.utc) + timedelta(days=3)
    resp = client.post(
        "/api/v1/staff/slots",
        json={
            "doctor_id": "does-not-matter",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
        },
        headers=auth_header(token),
    )
    assert resp.status_code == 403


def test_admin_duplicate_department_409(client):
    token = login(client, "ada.admin@example.com")
    headers = auth_header(token)
    assert (
        client.post(
            "/api/v1/staff/departments",
            json={"name": "Cardiology-Admin"},
            headers=headers,
        ).status_code
        == 201
    )
    dup = client.post(
        "/api/v1/staff/departments",
        json={"name": "Cardiology-Admin"},
        headers=headers,
    )
    assert dup.status_code == 409

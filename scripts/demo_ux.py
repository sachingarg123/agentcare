#!/usr/bin/env python3
"""Phase 6.7 — UX demo script against seed-shaped data (API paths the UI uses).

Run:
  uv run python scripts/demo_ux.py

Does not need a live browser. Verifies login → patient request → staff HITL →
admin CRUD using FastAPI TestClient + in-memory DB (same surface as the UI).

Browser checklist (for you after starting uvicorn) is printed at the end.
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_API_KEY", "")
os.environ.setdefault("SMTP_DISABLED", "true")

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from db.models import Base, DocumentType, UserRole
from db.repositories import (
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)
from db.session import get_db
from main import app
from services.workflow_service import set_checkpointer_override

DEMO_PASSWORD = "password123"


def _seed(session):
    pw = hash_password(DEMO_PASSWORD)
    users = UserRepository(session)
    patients = PatientRepository(session)

    asha = users.create(
        name="Asha Patient",
        email="asha.patient@example.com",
        password_hash=pw,
        role=UserRole.PATIENT.value,
    )
    users.create(
        name="Ravi Patient",
        email="ravi.patient@example.com",
        password_hash=pw,
        role=UserRole.PATIENT.value,
    )
    users.create(
        name="Sam Staff",
        email="sam.staff@example.com",
        password_hash=pw,
        role=UserRole.STAFF.value,
    )
    users.create(
        name="Ada Admin",
        email="ada.admin@example.com",
        password_hash=pw,
        role=UserRole.ADMIN.value,
    )
    patients.create(user_id=asha.id, phone="+91-1")
    patients.create(
        user_id=users.get_by_email("ravi.patient@example.com").id,
        phone="+91-2",
    )

    cardio = DepartmentRepository(session).create(name="Cardiology", description="Heart")
    DepartmentRepository(session).add_document_requirement(
        department_id=cardio.id,
        document_type=DocumentType.ECG.value,
        required=True,
    )
    for name in ("Radiology", "General Medicine", "Orthopedics", "Dermatology"):
        DepartmentRepository(session).create(name=name, description=name)

    doctor = DoctorRepository(session).create(department_id=cardio.id, name="Dr. Mehta")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    SlotRepository(session).create(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )
    SlotRepository(session).create(
        doctor_id=doctor.id,
        start_time=start + timedelta(hours=1),
        end_time=start + timedelta(hours=1, minutes=30),
    )
    session.commit()
    return {"cardio": cardio, "asha": asha}


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": DEMO_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    get_settings.cache_clear()
    upload = ROOT / "data" / "demo_ux_uploads"
    upload.mkdir(parents=True, exist_ok=True)
    os.environ["UPLOAD_DIR"] = str(upload)
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    hospital = _seed(session)

    def override_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    set_checkpointer_override(MemorySaver())

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    try:
        with TestClient(app) as client:
            print("\n=== Phase 6.7 UX demo (API = what the browser UI calls) ===\n")

            # Pages
            for path in ("/", "/patient", "/staff", "/staff/admin"):
                r = client.get(path)
                check(f"Page {path}", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""))

            # Patient happy path
            asha = _login(client, "asha.patient@example.com")
            me = client.get("/api/v1/auth/me", headers=_auth(asha))
            check("Patient /auth/me", me.status_code == 200 and me.json()["role"] == "PATIENT")

            happy = client.post(
                "/api/v1/requests",
                data={
                    "raw_request": (
                        "I need a cardiology follow-up next week and want to attach my old ECG."
                    )
                },
                files={
                    "files": ("old_ecg.pdf", io.BytesIO(b"%PDF-DEMO-ECG"), "application/pdf"),
                },
                headers=_auth(asha),
            )
            check(
                "Patient submit cardiology+ECG",
                happy.status_code == 200 and happy.json().get("status") == "completed",
                happy.json().get("status", happy.text[:120]),
            )
            if happy.status_code == 200:
                wid = happy.json()["workflow_run_id"]
                detail = client.get(f"/api/v1/requests/{wid}", headers=_auth(asha))
                conf = (detail.json() or {}).get("confirmation") or {}
                check(
                    "Patient workflow confirmation from DB",
                    detail.status_code == 200 and conf.get("ok") is True,
                    f"step={detail.json().get('current_step')}",
                )
                wf_page = client.get(f"/patient/workflows/{wid}")
                check("Workflow HTML page", wf_page.status_code == 200)

            # Clinical trap → escalation → staff resolve
            trap = client.post(
                "/api/v1/requests",
                data={"raw_request": "What medicine should I take for chest pain?"},
                headers=_auth(asha),
            )
            check(
                "Clinical trap interrupts",
                trap.status_code == 200 and trap.json().get("status") == "interrupted",
                trap.json().get("status", trap.text[:120]),
            )

            sam = _login(client, "sam.staff@example.com")
            esc_list = client.get("/api/v1/staff/escalations", headers=_auth(sam))
            check(
                "Staff lists pending escalations",
                esc_list.status_code == 200 and len(esc_list.json()) >= 1,
                f"count={len(esc_list.json()) if esc_list.status_code == 200 else 0}",
            )
            if esc_list.status_code == 200 and esc_list.json():
                esc_id = esc_list.json()[0]["id"]
                esc_page = client.get(f"/staff/escalations/{esc_id}")
                check("Escalation HTML page", esc_page.status_code == 200)
                resolved = client.post(
                    f"/api/v1/staff/escalations/{esc_id}/resolve",
                    json={"decision": "approve", "note": "Demo: staff will call patient"},
                    headers=_auth(sam),
                )
                check(
                    "Staff resolve escalation + resume",
                    resolved.status_code == 200 and resolved.json().get("status") == "completed",
                    resolved.json().get("status", resolved.text[:120]),
                )

            staff_reqs = client.get("/api/v1/staff/requests", headers=_auth(sam))
            check("Staff request queue", staff_reqs.status_code == 200 and len(staff_reqs.json()) >= 1)

            # Admin CRUD
            ada = _login(client, "ada.admin@example.com")
            denied = client.post(
                "/api/v1/staff/departments",
                json={"name": "Should-Fail"},
                headers=_auth(sam),
            )
            check("STAFF cannot create department (403)", denied.status_code == 403)

            created = client.post(
                "/api/v1/staff/departments",
                json={"name": "Demo-UX-Dept", "description": "6.7"},
                headers=_auth(ada),
            )
            check("ADMIN create department", created.status_code == 201, created.json().get("id", "")[:8])

            # Routing HITL resume path (optional)
            low = client.post(
                "/api/v1/requests",
                data={"raw_request": "hello"},
                headers=_auth(asha),
            )
            if low.status_code == 200 and low.json().get("status") == "interrupted":
                wid = low.json()["workflow_run_id"]
                resumed = client.post(
                    f"/api/v1/workflows/{wid}/resume",
                    json={
                        "decision": "approve",
                        "department_id": hospital["cardio"].id,
                        "department_name": "Cardiology",
                        "note": "Demo route to cardio",
                    },
                    headers=_auth(sam),
                )
                check(
                    "Staff resume routing HITL",
                    resumed.status_code == 200 and resumed.json().get("status") == "completed",
                    resumed.json().get("status", resumed.text[:80]),
                )
            else:
                check("Staff resume routing HITL", False, "low-confidence path did not interrupt")

    finally:
        set_checkpointer_override(None)
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
        get_settings.cache_clear()

    failed = [c for c in checks if not c[1]]
    print(f"\n=== Result: {len(checks) - len(failed)}/{len(checks)} passed ===\n")

    print("Browser checklist (after you start the server):")
    print("  1. Open http://127.0.0.1:8000")
    print("  2. Login asha.patient@example.com / password123")
    print("  3. Submit a cardiology request (+ optional ECG PDF)")
    print("  4. Confirm workflow page shows confirmation")
    print("  5. Logout → sam.staff@example.com → review any escalations")
    print("  6. Logout → ada.admin@example.com → /staff/admin manage slots")
    print()
    print("Start server:")
    print("  cd \"/Users/sachinga@backbase.com/Documents/AI Learning/agentcare\"")
    print("  source .venv/bin/activate")
    print("  uvicorn main:app --reload --host 127.0.0.1 --port 8000")
    print()

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

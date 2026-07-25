#!/usr/bin/env python3
"""Seed synthetic hospital data into AgentCare SQLite (Phase 1.4).

Why
---
Empty tables can't demo agents: routing needs departments, booking needs slots,
RBAC needs PATIENT / STAFF / ADMIN users. This script fills the DB with *fake*
data only (no real PHI — PRD G9).

What it creates (PRD 1.4)
-------------------------
- 5 departments, 10 doctors (2 each), 50 available slots
- Department document requirements (e.g. Cardiology → ECG)
- 2 PATIENT users (+ PatientProfile), 1 STAFF, 1 ADMIN
- Shared demo password for all seed accounts (see DEMO_PASSWORD)

Usage
-----
    uv run python scripts/seed_data.py
    uv run python scripts/seed_data.py --force   # wipe seed users/hospital rows & re-seed

Later: main.py lifespan can call seed_if_empty() on startup (Phase 5).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Allow `python scripts/seed_data.py` from repo root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth.passwords import hash_password
from core.config import get_settings
from db.models import DocumentType, UserRole
from db.repositories import (
    DepartmentRepository,
    DoctorRepository,
    PatientRepository,
    SlotRepository,
    UserRepository,
)
from db.session import SessionLocal

DEMO_PASSWORD = "password123"

# Fixed emails — used by tests and demo login later
SEED_USERS = [
    {
        "name": "Asha Patient",
        "email": "asha.patient@example.com",
        "role": UserRole.PATIENT.value,
        "dob": date(1990, 5, 12),
        "phone": "+91-90000-00001",
    },
    {
        "name": "Ravi Patient",
        "email": "ravi.patient@example.com",
        "role": UserRole.PATIENT.value,
        "dob": date(1985, 11, 3),
        "phone": "+91-90000-00002",
    },
    {
        "name": "Sam Staff",
        "email": "sam.staff@example.com",
        "role": UserRole.STAFF.value,
    },
    {
        "name": "Ada Admin",
        "email": "ada.admin@example.com",
        "role": UserRole.ADMIN.value,
    },
]

DEPARTMENTS = [
    ("Cardiology", "Heart and circulatory care"),
    ("Radiology", "Imaging and diagnostics"),
    ("General Medicine", "Primary outpatient care"),
    ("Orthopedics", "Bones and joints"),
    ("Dermatology", "Skin care"),
]

# Two doctors per department (10 total)
DOCTOR_NAMES = {
    "Cardiology": ["Dr. Mehta", "Dr. Kapoor"],
    "Radiology": ["Dr. Singh", "Dr. Iyer"],
    "General Medicine": ["Dr. Nair", "Dr. Bose"],
    "Orthopedics": ["Dr. Reddy", "Dr. Khan"],
    "Dermatology": ["Dr. Patel", "Dr. Das"],
}

# Required docs for routing / document agent demos
DOC_REQUIREMENTS = {
    "Cardiology": [DocumentType.ECG.value, DocumentType.REFERRAL_LETTER.value],
    "Radiology": [DocumentType.REFERRAL_LETTER.value],
    "Orthopedics": [DocumentType.RADIOLOGY.value],
}


def is_seeded(db) -> bool:
    users = UserRepository(db)
    return users.get_by_email(SEED_USERS[0]["email"]) is not None


def clear_hospital_and_users(db) -> None:
    """Delete in FK-safe order for --force reseed."""
    from sqlalchemy import delete

    from db.models import (
        Appointment,
        AppointmentSlot,
        AuditEvent,
        Department,
        DepartmentDocumentRequirement,
        Doctor,
        Escalation,
        PatientDocument,
        PatientProfile,
        Reminder,
        User,
        WorkflowRun,
    )

    for model in (
        Reminder,
        Appointment,
        AppointmentSlot,
        PatientDocument,
        Escalation,
        AuditEvent,
        WorkflowRun,
        DepartmentDocumentRequirement,
        Doctor,
        Department,
        PatientProfile,
        User,
    ):
        db.execute(delete(model))
    db.commit()


def seed(db) -> dict:
    users_repo = UserRepository(db)
    patients_repo = PatientRepository(db)
    depts_repo = DepartmentRepository(db)
    doctors_repo = DoctorRepository(db)
    slots_repo = SlotRepository(db)

    password_hash = hash_password(DEMO_PASSWORD)

    # --- Users + patient profiles ---
    created_users = []
    for spec in SEED_USERS:
        user = users_repo.create(
            name=spec["name"],
            email=spec["email"],
            password_hash=password_hash,
            role=spec["role"],
        )
        created_users.append(user)
        if spec["role"] == UserRole.PATIENT.value:
            patients_repo.create(
                user_id=user.id,
                date_of_birth=spec.get("dob"),
                phone=spec.get("phone"),
                preferred_language="en",
                emergency_contact="Emergency Contact (synthetic)",
            )

    # --- Departments + doctors + doc requirements ---
    dept_by_name: dict[str, str] = {}
    doctor_ids: list[str] = []
    for name, description in DEPARTMENTS:
        dept = depts_repo.create(name=name, description=description)
        dept_by_name[name] = dept.id
        for doc_type in DOC_REQUIREMENTS.get(name, []):
            depts_repo.add_document_requirement(
                department_id=dept.id, document_type=doc_type, required=True
            )
        for dname in DOCTOR_NAMES[name]:
            doctor = doctors_repo.create(department_id=dept.id, name=dname)
            doctor_ids.append(doctor.id)

    # --- 50 slots: 5 per doctor, starting tomorrow, 30-min blocks ---
    base = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    base = base + timedelta(days=1)
    slot_count = 0
    for doctor_id in doctor_ids:
        for i in range(5):
            start = base + timedelta(hours=i)
            slots_repo.create(
                doctor_id=doctor_id,
                start_time=start,
                end_time=start + timedelta(minutes=30),
            )
            slot_count += 1

    db.commit()
    return {
        "users": len(created_users),
        "departments": len(DEPARTMENTS),
        "doctors": len(doctor_ids),
        "slots": slot_count,
    }


def seed_if_empty() -> bool:
    """Return True if seeding ran, False if already populated."""
    from db import session as db_session

    get_settings().ensure_data_dirs()
    db = db_session.SessionLocal()
    try:
        if is_seeded(db):
            return False
        seed(db)
        return True
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AgentCare synthetic data")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing rows and re-seed",
    )
    args = parser.parse_args()

    get_settings().ensure_data_dirs()
    db = SessionLocal()
    try:
        if is_seeded(db) and not args.force:
            print("Database already seeded. Use --force to re-seed.")
            print("Demo logins (password: password123):")
            for u in SEED_USERS:
                print(f"  {u['role']:8}  {u['email']}")
            return

        if args.force and is_seeded(db):
            print("Clearing existing data (--force)...")
            clear_hospital_and_users(db)

        stats = seed(db)
        print("Seed complete:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print()
        print(f"Demo password for all users: {DEMO_PASSWORD}")
        for u in SEED_USERS:
            print(f"  {u['role']:8}  {u['email']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

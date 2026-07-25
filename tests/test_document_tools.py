"""Phase 2.4 — document classify / store / dedupe / required checklist."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, UserRole
from db.repositories import DepartmentRepository, DocumentRepository, PatientRepository, UserRepository
from tools.document_tools import (
    check_document_duplicates,
    classify_document,
    get_required_documents,
    missing_documents,
    store_document,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_patient(db: Session):
    user = UserRepository(db).create(
        name="Asha",
        email="asha-doc@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    db.commit()
    return user, profile


def test_classify_ecg_filename_stage1():
    result = classify_document("patient_old_ECG_scan.pdf")
    assert result["document_type"] == DocumentType.ECG.value
    assert result["stage"] == 1
    assert result["confidence"] >= 0.9


def test_store_and_duplicate_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    db = _session()
    user, profile = _seed_patient(db)
    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    content = b"%PDF-fake-ecg-content-001"

    first = store_document(state, db, filename="old_ecg.pdf", content=content)
    db.commit()
    assert first["ok"] is True
    assert first["document_type"] == DocumentType.ECG.value
    assert Path(first["file_path"]).is_file()

    second = store_document(state, db, filename="old_ecg_copy.pdf", content=content)
    assert second["ok"] is False
    assert second["error"] == "duplicate"

    dup = check_document_duplicates(state, db, checksum=first["checksum"])
    assert dup["is_duplicate"] is True


def test_required_and_missing_documents():
    db = _session()
    user, profile = _seed_patient(db)
    dept = DepartmentRepository(db).create(name="Cardiology")
    DepartmentRepository(db).add_document_requirement(
        department_id=dept.id, document_type=DocumentType.ECG.value, required=True
    )
    DepartmentRepository(db).add_document_requirement(
        department_id=dept.id,
        document_type=DocumentType.REFERRAL_LETTER.value,
        required=True,
    )
    db.commit()

    checklist = get_required_documents(db, department_id=dept.id)
    assert checklist["ok"] is True
    assert len(checklist["required"]) == 2

    state: GraphState = {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
    }
    # Store only ECG metadata via repo (skip file IO for this unit)
    DocumentRepository(db).create(
        patient_id=profile.id,
        file_path="/tmp/ecg.pdf",
        checksum="abc",
        document_type=DocumentType.ECG.value,
    )
    db.commit()

    miss = missing_documents(state, db, department_id=dept.id)
    assert miss["missing"] == [DocumentType.REFERRAL_LETTER.value]
    assert DocumentType.ECG.value in miss["have"]

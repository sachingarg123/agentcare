"""Phase 3.6 — document_node: store uploads, duplicates, missing checklist."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.document_node import DOCUMENT_PROMPT, document_node, get_document_tools
from auth.passwords import hash_password
from core.config import get_settings
from core.graph_state import GraphState
from db.models import Base, DocumentType, UserRole
from db.repositories import (
    DepartmentRepository,
    DocumentRepository,
    PatientRepository,
    UserRepository,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session, *, with_ecg_requirement: bool = True) -> GraphState:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-doc-node@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    cardio = DepartmentRepository(db).create(name="Cardiology", description="Heart")
    if with_ecg_requirement:
        DepartmentRepository(db).add_document_requirement(
            department_id=cardio.id,
            document_type=DocumentType.ECG.value,
            required=True,
        )
    db.commit()
    return {
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "routing_result": {
            "department_id": cardio.id,
            "department_name": "Cardiology",
        },
        "uploaded_files": [],
    }


def test_document_prompt_loaded():
    assert "Document" in DOCUMENT_PROMPT
    assert "store_document" in DOCUMENT_PROMPT


def test_document_node_stores_ecg_and_clears_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    db = _session()
    state = _seed(db)
    state["uploaded_files"] = [
        {
            "filename": "old_ecg.pdf",
            "content": b"%PDF-ECG-NODE-001",
            "mime_type": "application/pdf",
        }
    ]
    update = document_node(state, db)
    db.commit()

    assert update["current_step"] == "document"
    result = update["document_result"]
    assert result["ok"] is True
    assert len(result["stored"]) == 1
    assert result["stored"][0]["document_type"] == DocumentType.ECG.value
    assert result["duplicates"] == []
    assert result["missing"] == []
    assert DocumentType.ECG.value in result["have"]
    assert DocumentRepository(db).get_by_id(result["stored"][0]["document_id"]) is not None

    get_settings.cache_clear()


def test_document_node_flags_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    db = _session()
    state = _seed(db)
    content = b"%PDF-SAME-BYTES"
    state["uploaded_files"] = [
        {"filename": "a_ecg.pdf", "content": content},
        {"filename": "b_ecg.pdf", "content": content},
    ]
    update = document_node(state, db)
    db.commit()

    result = update["document_result"]
    assert result["ok"] is True
    assert len(result["stored"]) == 1
    assert len(result["duplicates"]) == 1
    assert result["duplicates"][0]["checksum"]

    get_settings.cache_clear()


def test_document_node_no_files_reports_missing():
    db = _session()
    state = _seed(db)
    update = document_node(state, db)

    result = update["document_result"]
    assert result["ok"] is True
    assert result["stored"] == []
    assert DocumentType.ECG.value in result["missing"]


def test_get_document_tools_binds_five():
    db = _session()
    state = _seed(db)
    tools = get_document_tools(state, db)
    assert {t.name for t in tools} == {
        "classify_document",
        "store_document",
        "check_document_duplicates",
        "get_required_documents",
        "missing_documents",
    }
    classified = tools[0].invoke({"filename": "patient_ECG.pdf"})
    assert classified["document_type"] == DocumentType.ECG.value

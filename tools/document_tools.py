"""Document tools — classify, store, dedupe, required-doc checklist (PRD 2.4).

Used by the Document agent after appointment booking (or when uploads are attached).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.classifier import classify_document_file
from core.config import get_settings
from core.graph_state import GraphState
from db.repositories import DepartmentRepository, DocumentRepository
from tools._scope import ToolScopeError, assert_tool_scope


def _require_patient_id(state: GraphState) -> str:
    patient_id = state.get("patient_id")
    if not patient_id:
        raise ToolScopeError("GraphState missing patient_id")
    return patient_id


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def classify_document(
    filename: str,
    *,
    content: bytes | None = None,
    text_hint: str | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Classify document type via 3-stage pipeline (filename → keywords → optional LLM).

    Read-only regarding patient DB rows.
    """
    return classify_document_file(
        filename,
        text_hint=text_hint,
        use_llm=use_llm,
        file_bytes=content,
    )


def check_document_duplicates(
    state: GraphState,
    db: Session,
    *,
    checksum: str,
) -> dict[str, Any]:
    """Return whether this patient already has a file with the same SHA-256."""
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    existing = DocumentRepository(db).find_by_checksum(patient_id, checksum)
    if existing is None:
        return {"is_duplicate": False, "existing_document_id": None}
    return {
        "is_duplicate": True,
        "existing_document_id": existing.id,
        "existing_document_type": existing.document_type,
        "existing_file_path": existing.file_path,
    }


def store_document(
    state: GraphState,
    db: Session,
    *,
    filename: str,
    content: bytes,
    document_type: str | None = None,
    document_date: date | None = None,
    use_llm_classify: bool = False,
) -> dict[str, Any]:
    """
    Classify (if needed), checksum, dedupe, write bytes under data/uploads/, insert row.

    Order: scope → checksum → duplicate check → classify → write file → DB insert.
    Does not commit — caller commits.
    """
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    if not content:
        return {"ok": False, "error": "empty_file", "message": "No file content"}

    checksum = _sha256(content)
    dup = check_document_duplicates(state, db, checksum=checksum)
    if dup["is_duplicate"]:
        return {
            "ok": False,
            "error": "duplicate",
            "message": "Document with same checksum already stored for this patient",
            "duplicate": dup,
            "checksum": checksum,
        }

    if document_type is None:
        classification = classify_document(
            filename, content=content, use_llm=use_llm_classify
        )
        document_type = classification["document_type"]
    else:
        classification = {
            "document_type": document_type,
            "stage": None,
            "confidence": 1.0,
            "reason": "Caller-supplied document_type",
        }

    settings = get_settings()
    settings.ensure_data_dirs()
    upload_root = Path(settings.upload_dir)
    patient_dir = upload_root / patient_id
    patient_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name or "upload.bin"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    dest = patient_dir / stored_name
    dest.write_bytes(content)

    doc = DocumentRepository(db).create(
        patient_id=patient_id,
        file_path=str(dest),
        checksum=checksum,
        document_type=document_type,
        document_date=document_date,
    )

    return {
        "ok": True,
        "document_id": doc.id,
        "patient_id": patient_id,
        "file_path": doc.file_path,
        "checksum": checksum,
        "document_type": doc.document_type,
        "classification": classification,
    }


def get_required_documents(
    db: Session,
    *,
    department_id: str,
) -> dict[str, Any]:
    """
    Department document checklist from DepartmentDocumentRequirement (real DB).

    Used to compute missing docs after uploads.
    """
    dept_repo = DepartmentRepository(db)
    dept = dept_repo.get_by_id(department_id)
    if dept is None:
        return {
            "ok": False,
            "error": "department_not_found",
            "department_id": department_id,
            "required": [],
        }

    reqs = dept_repo.list_document_requirements(department_id, required_only=True)
    required = [
        {"document_type": r.document_type, "required": r.required} for r in reqs
    ]
    return {
        "ok": True,
        "department_id": department_id,
        "department_name": dept.name,
        "required": required,
    }


def missing_documents(
    state: GraphState,
    db: Session,
    *,
    department_id: str,
) -> dict[str, Any]:
    """Compare required types for a department vs what this patient has stored."""
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    checklist = get_required_documents(db, department_id=department_id)
    if not checklist.get("ok"):
        return checklist

    stored = DocumentRepository(db).list_for_patient(patient_id)
    have = {d.document_type for d in stored}
    missing = [
        item["document_type"]
        for item in checklist["required"]
        if item["document_type"] not in have
    ]
    return {
        "ok": True,
        "department_id": department_id,
        "department_name": checklist["department_name"],
        "required": checklist["required"],
        "have": sorted(have),
        "missing": missing,
    }

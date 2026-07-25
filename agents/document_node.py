"""Document agent node — classify, store, dedupe, missing checklist (Phase 3.6).

Deterministic node over uploaded_files + department requirements.
Also exposes LangChain StructuredTools for optional LLM binding.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import DocumentResult, GraphState, StoredDocumentInfo
from tools.document_tools import (
    check_document_duplicates,
    classify_document,
    get_required_documents,
    missing_documents,
    store_document,
)
from tools.safety_tools import write_audit_event

DOCUMENT_PROMPT = load_prompt("document")


def _department_id(state: GraphState) -> str | None:
    routing = state.get("routing_result") or {}
    return routing.get("department_id")


def document_node(
    state: GraphState,
    db: Session,
    *,
    use_llm_classify: bool = False,
) -> GraphState:
    """
    Process ``uploaded_files`` and compute missing docs for the routed department.

    For each file: store (classify + checksum + dedupe). Duplicates are collected,
    not treated as a hard node failure. Empty uploads still run the missing-doc
    checklist when a department is known.

    Does not commit — caller commits the session.
    """
    if not state.get("patient_id"):
        if state.get("actor_user_id"):
            write_audit_event(
                state,
                db,
                action="document.process",
                entity_type="WorkflowRun",
                entity_id=state.get("workflow_run_id"),
                event_metadata={"ok": False, "error": "missing_patient_id"},
            )
        return {
            "current_step": "document",
            "document_result": {
                "ok": False,
                "error": "missing_patient_id",
                "message": "GraphState missing patient_id",
                "stored": [],
                "duplicates": [],
                "missing": [],
            },
            "error": "GraphState missing patient_id",
        }

    stored: list[StoredDocumentInfo] = []
    duplicates: list[dict[str, Any]] = []
    files = list(state.get("uploaded_files") or [])

    for item in files:
        filename = (item.get("filename") or "upload.bin").strip() or "upload.bin"
        content = item.get("content") or b""
        if isinstance(content, str):
            content = content.encode("utf-8")

        result = store_document(
            state,
            db,
            filename=filename,
            content=content,
            use_llm_classify=use_llm_classify,
        )
        if result.get("ok"):
            info: StoredDocumentInfo = {
                "document_id": result["document_id"],
                "filename": filename,
                "document_type": result["document_type"],
                "checksum": result["checksum"],
                "file_path": result["file_path"],
            }
            stored.append(info)
        elif result.get("error") == "duplicate":
            duplicates.append(
                {
                    "filename": filename,
                    "checksum": result.get("checksum"),
                    "duplicate": result.get("duplicate"),
                    "message": result.get("message"),
                }
            )
        else:
            duplicates.append(
                {
                    "filename": filename,
                    "error": result.get("error"),
                    "message": result.get("message"),
                }
            )

    dept_id = _department_id(state)
    missing: list[str] = []
    required: list[dict[str, Any]] = []
    have: list[str] = []
    department_name: str | None = None

    if dept_id:
        checklist = missing_documents(state, db, department_id=dept_id)
        if checklist.get("ok"):
            missing = list(checklist.get("missing") or [])
            required = list(checklist.get("required") or [])
            have = list(checklist.get("have") or [])
            department_name = checklist.get("department_name")

    document_result: DocumentResult = {
        "ok": True,
        "stored": stored,
        "duplicates": duplicates,
        "missing": missing,
        "required": required,
        "have": have,
        "department_id": dept_id,
        "department_name": department_name,
    }
    if not files and not dept_id:
        document_result["message"] = "No uploaded files and no department for checklist"

    if state.get("actor_user_id"):
        write_audit_event(
            state,
            db,
            action="document.process",
            entity_type="WorkflowRun",
            entity_id=state.get("workflow_run_id"),
            event_metadata={
                "stored_count": len(stored),
                "duplicate_count": len(duplicates),
                "missing": missing,
                "department_id": dept_id,
            },
        )

    return {
        "current_step": "document",
        "document_result": document_result,
        "hitl_required": False,
        "hitl_reason": None,
    }


def get_document_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """Bind document tools to the current workflow state + DB session."""

    def _classify(filename: str, use_llm: bool = False) -> dict[str, Any]:
        return classify_document(filename, use_llm=use_llm)

    def _store(filename: str, content_b64: str = "", use_llm_classify: bool = False) -> dict[str, Any]:
        # StructuredTool-friendly: accept utf-8 / plain bytes via latin-1 roundtrip
        content = content_b64.encode("latin-1") if content_b64 else b""
        return store_document(
            state,
            db,
            filename=filename,
            content=content,
            use_llm_classify=use_llm_classify,
        )

    def _dupes(checksum: str) -> dict[str, Any]:
        return check_document_duplicates(state, db, checksum=checksum)

    def _required(department_id: str = "") -> dict[str, Any]:
        return get_required_documents(
            db,
            department_id=department_id or _department_id(state) or "",
        )

    def _missing(department_id: str = "") -> dict[str, Any]:
        return missing_documents(
            state,
            db,
            department_id=department_id or _department_id(state) or "",
        )

    return [
        StructuredTool.from_function(
            func=_classify,
            name="classify_document",
            description="Classify a document filename via the 3-stage pipeline.",
        ),
        StructuredTool.from_function(
            func=_store,
            name="store_document",
            description=(
                "Store a document for the workflow patient. Pass file bytes as "
                "latin-1-encoded content_b64 string for tool calling."
            ),
        ),
        StructuredTool.from_function(
            func=_dupes,
            name="check_document_duplicates",
            description="Check if checksum already exists for this patient.",
        ),
        StructuredTool.from_function(
            func=_required,
            name="get_required_documents",
            description="Department document checklist from the database.",
        ),
        StructuredTool.from_function(
            func=_missing,
            name="missing_documents",
            description="Required types still missing for this patient/department.",
        ),
    ]

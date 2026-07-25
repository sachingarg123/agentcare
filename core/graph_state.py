"""LangGraph shared state — TypedDict contracts (Phase 3.1 / PRD §6.4).

Nodes return partial updates; LangGraph merges them into this bag.
Identity fields are set when a workflow starts (API / workflow_service).
Result types mirror what Phase 2 tools already return so nodes map, not reshape.
"""

from __future__ import annotations

from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Nested result contracts (one per agent / pipeline stage)
# ---------------------------------------------------------------------------


class UploadedFile(TypedDict, total=False):
    """File attached at workflow start or mid-flow."""

    filename: str
    content: bytes
    mime_type: str
    size: int


class SafetyResult(TypedDict, total=False):
    """Output of safety screening / block path."""

    safe: bool
    flags: list[str]
    category: str | None
    matched: list[str]
    safe_alternative: str | None
    reason: str
    stage: str  # "keywords" | "llm" | "keywords+llm"
    escalation_id: str | None
    blocked: bool
    message: str


class RoutingResult(TypedDict, total=False):
    """Output of intent + department classification."""

    intents: list[str]
    department_id: str | None
    department_name: str | None
    confidence: float
    reason: str
    needs_staff_review: bool
    raw_request: str


class AppointmentResult(TypedDict, total=False):
    """Output of slot search / book / cancel / reschedule."""

    ok: bool
    appointment_id: str | None
    slot_id: str | None
    status: str | None
    doctor_id: str | None
    doctor_name: str | None
    start_time: str | None
    end_time: str | None
    reason: str | None
    error: str | None
    message: str | None
    available_count: int
    slots: list[dict[str, Any]]


class StoredDocumentInfo(TypedDict, total=False):
    document_id: str
    filename: str
    document_type: str
    checksum: str
    file_path: str


class DocumentResult(TypedDict, total=False):
    """Output of classify / store / missing-doc checks."""

    ok: bool
    stored: list[StoredDocumentInfo]
    duplicates: list[dict[str, Any]]
    missing: list[str]
    required: list[dict[str, Any]]
    have: list[str]
    department_id: str | None
    department_name: str | None
    error: str | None
    message: str | None


class FollowupResult(TypedDict, total=False):
    """Output of reminder / follow-up / notification tools."""

    ok: bool
    reminder_ids: list[str]
    followup_task_id: str | None
    notification_id: str | None
    notification_status: str | None
    error: str | None
    message: str | None


class ConfirmationResult(TypedDict, total=False):
    """Final assembled summary for the patient (from persisted records)."""

    ok: bool
    summary: str
    appointment_id: str | None
    department_name: str | None
    doctor_name: str | None
    start_time: str | None
    documents_stored: int
    reminders_scheduled: int
    workflow_run_id: str | None


# ---------------------------------------------------------------------------
# Top-level graph state
# ---------------------------------------------------------------------------


class GraphState(TypedDict, total=False):
    """
    State bag passed through every agent node and into tools.

    Identity (set at workflow start):
      - workflow_run_id, patient_id, actor_user_id, actor_role, raw_request
    Coordinator / nodes fill result keys and current_step as the graph progresses.
    """

    # Identity — required for tool scope / audit
    workflow_run_id: str
    patient_id: str
    actor_user_id: str
    actor_role: str  # "PATIENT" | "STAFF" | "ADMIN"
    raw_request: str
    uploaded_files: list[UploadedFile]

    # Coordinator
    administrative_intents: list[str]
    current_step: str

    # Per-stage results
    safety_result: SafetyResult
    routing_result: RoutingResult
    appointment_result: AppointmentResult
    document_result: DocumentResult
    followup_result: FollowupResult
    confirmation: ConfirmationResult

    # Terminal / error / HITL
    error: str | None
    hitl_required: bool
    hitl_reason: str | None
    hitl_source: str  # "safety" | "routing" | "appointment"
    staff_decision: dict[str, Any]  # {decision, department_id?, note?, ...}

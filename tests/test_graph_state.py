"""Phase 3.1 — GraphState TypedDict contracts are importable and constructible."""

from __future__ import annotations

from core.graph_state import (
    AppointmentResult,
    ConfirmationResult,
    DocumentResult,
    FollowupResult,
    GraphState,
    RoutingResult,
    SafetyResult,
    UploadedFile,
)


def test_graph_state_identity_and_results_constructible():
    uploaded: UploadedFile = {
        "filename": "old_ecg.pdf",
        "content": b"%PDF",
        "mime_type": "application/pdf",
    }
    safety: SafetyResult = {
        "safe": True,
        "flags": [],
        "category": None,
        "reason": "Administrative request — allowed",
        "stage": "keywords",
    }
    routing: RoutingResult = {
        "intents": ["BOOK_APPOINTMENT"],
        "department_id": "dept-1",
        "department_name": "Cardiology",
        "confidence": 0.85,
        "reason": "Matched Cardiology",
        "needs_staff_review": False,
    }
    appointment: AppointmentResult = {
        "ok": True,
        "appointment_id": "appt-1",
        "slot_id": "slot-1",
        "status": "BOOKED",
        "doctor_name": "Dr. Mehta",
    }
    documents: DocumentResult = {
        "ok": True,
        "stored": [
            {
                "document_id": "doc-1",
                "filename": "old_ecg.pdf",
                "document_type": "ECG",
                "checksum": "abc",
            }
        ],
        "duplicates": [],
        "missing": [],
    }
    followup: FollowupResult = {
        "ok": True,
        "reminder_ids": ["rem-1"],
        "followup_task_id": "rem-2",
    }
    confirmation: ConfirmationResult = {
        "ok": True,
        "summary": "Appointment booked with Dr. Mehta",
        "appointment_id": "appt-1",
        "documents_stored": 1,
        "reminders_scheduled": 2,
    }

    state: GraphState = {
        "workflow_run_id": "wf-1",
        "patient_id": "pat-1",
        "actor_user_id": "user-1",
        "actor_role": "PATIENT",
        "raw_request": "Book cardiology follow-up",
        "uploaded_files": [uploaded],
        "administrative_intents": ["BOOK_APPOINTMENT", "UPLOAD_DOCUMENT"],
        "current_step": "confirm",
        "safety_result": safety,
        "routing_result": routing,
        "appointment_result": appointment,
        "document_result": documents,
        "followup_result": followup,
        "confirmation": confirmation,
        "error": None,
        "hitl_required": False,
    }

    assert state["actor_role"] == "PATIENT"
    assert state["safety_result"]["safe"] is True
    assert state["routing_result"]["department_name"] == "Cardiology"
    assert state["appointment_result"]["ok"] is True
    assert len(state["document_result"]["stored"]) == 1
    assert state["followup_result"]["followup_task_id"] == "rem-2"
    assert state["confirmation"]["ok"] is True


def test_partial_update_shape_for_langgraph_merge():
    """Nodes return only keys they change (total=False)."""
    update: GraphState = {
        "current_step": "safety",
        "safety_result": {"safe": False, "flags": ["clinical"], "reason": "blocked"},
        "hitl_required": True,
        "hitl_reason": "Clinical language detected",
    }
    assert set(update.keys()) == {
        "current_step",
        "safety_result",
        "hitl_required",
        "hitl_reason",
    }

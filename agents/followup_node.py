"""Follow-up agent node — reminders, follow-up task, notification (Phase 3.7).

Deterministic node after a successful booking. Also exposes LangChain
StructuredTools for optional LLM binding.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import FollowupResult, GraphState
from tools.followup_tools import (
    NOTIFY_CONFIRMATION,
    NOTIFY_DOCUMENT_REQUEST,
    create_reminder,
    schedule_followup,
    send_notification,
)
from tools.safety_tools import write_audit_event

FOLLOWUP_PROMPT = load_prompt("followup")


def followup_node(
    state: GraphState,
    db: Session,
    *,
    hours_before: int = 24,
    days_after: int = 7,
    send_confirmation: bool = True,
) -> GraphState:
    """
    Schedule reminder + follow-up task and optionally email confirmation.

    Requires ``appointment_result.appointment_id`` from a successful book.
    SMTP_DISABLED → notification status SKIPPED (still recorded).

    Does not commit — caller commits the session.
    """
    if not state.get("patient_id"):
        if state.get("actor_user_id"):
            write_audit_event(
                state,
                db,
                action="followup.schedule",
                entity_type="WorkflowRun",
                entity_id=state.get("workflow_run_id"),
                event_metadata={"ok": False, "error": "missing_patient_id"},
            )
        return {
            "current_step": "followup",
            "followup_result": {
                "ok": False,
                "error": "missing_patient_id",
                "message": "GraphState missing patient_id",
                "reminder_ids": [],
            },
            "error": "GraphState missing patient_id",
        }

    appt = state.get("appointment_result") or {}
    appointment_id = appt.get("appointment_id")
    if not appointment_id or appt.get("ok") is False:
        # No booking → follow-up agent intentionally skipped (not an agent "action").
        return {
            "current_step": "followup",
            "followup_result": {
                "ok": False,
                "error": "no_appointment",
                "message": "No successful appointment to attach reminders to",
                "reminder_ids": [],
            },
        }

    reminder_ids: list[str] = []
    followup_task_id: str | None = None
    errors: list[str] = []

    rem = create_reminder(
        state,
        db,
        appointment_id=appointment_id,
        hours_before=hours_before,
    )
    if rem.get("ok"):
        rid = (rem.get("reminder") or {}).get("reminder_id")
        if rid:
            reminder_ids.append(rid)
    else:
        errors.append(rem.get("error") or "reminder_failed")

    fu = schedule_followup(
        state,
        db,
        appointment_id=appointment_id,
        days_after=days_after,
    )
    if fu.get("ok"):
        followup_task_id = (fu.get("followup") or {}).get("reminder_id")
        if followup_task_id:
            reminder_ids.append(followup_task_id)
    else:
        errors.append(fu.get("error") or "followup_failed")

    notification_id: str | None = None
    notification_status: str | None = None

    if send_confirmation:
        doctor = appt.get("doctor_name") or "your clinician"
        when = appt.get("start_time") or "your scheduled time"
        notify = send_notification(
            state,
            db,
            email_type=NOTIFY_CONFIRMATION,
            subject="PulseDesk — Appointment confirmed",
            body_text=(
                f"Your appointment with {doctor} is confirmed for {when}. "
                "This is an administrative notice only."
            ),
            reminder_id=reminder_ids[0] if reminder_ids else None,
        )
        delivery = notify.get("delivery") or {}
        if notify.get("ok"):
            notification_id = delivery.get("notification_id")
            notification_status = delivery.get("status")
        else:
            errors.append(notify.get("error") or delivery.get("error") or "notify_failed")
            notification_status = delivery.get("status")

        # Optional: ask for missing docs administratively
        missing = (state.get("document_result") or {}).get("missing") or []
        if missing:
            doc_notify = send_notification(
                state,
                db,
                email_type=NOTIFY_DOCUMENT_REQUEST,
                subject="PulseDesk — Documents requested",
                body_text=(
                    "Please upload the following required documents: "
                    + ", ".join(missing)
                    + ". This is an administrative request only."
                ),
            )
            if not doc_notify.get("ok"):
                errors.append(doc_notify.get("error") or "document_request_failed")

    ok = len(errors) == 0 and bool(reminder_ids)
    followup_result: FollowupResult = {
        "ok": ok,
        "reminder_ids": reminder_ids,
        "followup_task_id": followup_task_id,
        "notification_id": notification_id,
        "notification_status": notification_status,
    }
    if errors:
        followup_result["error"] = errors[0]
        followup_result["message"] = "; ".join(errors)

    if state.get("actor_user_id"):
        write_audit_event(
            state,
            db,
            action="followup.schedule",
            entity_type="Appointment",
            entity_id=appointment_id,
            event_metadata={
                "ok": ok,
                "reminder_ids": reminder_ids,
                "followup_task_id": followup_task_id,
                "notification_status": notification_status,
            },
        )

    return {
        "current_step": "followup",
        "followup_result": followup_result,
        "hitl_required": False,
        "hitl_reason": None,
    }


def get_followup_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """Bind follow-up tools to the current workflow state + DB session."""

    def _reminder(appointment_id: str, hours_before: int = 24) -> dict[str, Any]:
        return create_reminder(
            state,
            db,
            appointment_id=appointment_id,
            hours_before=hours_before,
        )

    def _followup(appointment_id: str, days_after: int = 7) -> dict[str, Any]:
        return schedule_followup(
            state,
            db,
            appointment_id=appointment_id,
            days_after=days_after,
        )

    def _notify(
        email_type: str,
        subject: str,
        body_text: str,
        reminder_id: str = "",
    ) -> dict[str, Any]:
        return send_notification(
            state,
            db,
            email_type=email_type,
            subject=subject,
            body_text=body_text,
            reminder_id=reminder_id or None,
        )

    return [
        StructuredTool.from_function(
            func=_reminder,
            name="create_reminder",
            description="Schedule an appointment reminder (default 24h before).",
        ),
        StructuredTool.from_function(
            func=_followup,
            name="schedule_followup",
            description="Schedule a post-visit follow-up task (default 7 days after).",
        ),
        StructuredTool.from_function(
            func=_notify,
            name="send_notification",
            description="Send an administrative email notification (no clinical advice).",
        ),
    ]

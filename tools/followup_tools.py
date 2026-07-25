"""Follow-up tools — reminders, post-visit tasks, notifications (PRD 2.5).

Used by the Follow-up agent after booking / document steps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.models import ReminderStatus
from db.repositories import (
    AppointmentRepository,
    PatientRepository,
    ReminderRepository,
    SlotRepository,
    UserRepository,
)
from services.email_service import send_email
from tools._scope import ToolScopeError, assert_tool_scope

REMINDER_APPOINTMENT = "APPOINTMENT_REMINDER"
REMINDER_FOLLOWUP = "FOLLOWUP_TASK"
NOTIFY_CONFIRMATION = "APPOINTMENT_CONFIRMATION"
NOTIFY_DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
NOTIFY_ESCALATION = "ESCALATION_ALERT"


def _require_patient_id(state: GraphState) -> str:
    patient_id = state.get("patient_id")
    if not patient_id:
        raise ToolScopeError("GraphState missing patient_id")
    return patient_id


def _reminder_dict(reminder) -> dict[str, Any]:
    return {
        "reminder_id": reminder.id,
        "patient_id": reminder.patient_id,
        "appointment_id": reminder.appointment_id,
        "reminder_type": reminder.reminder_type,
        "scheduled_at": reminder.scheduled_at.isoformat() if reminder.scheduled_at else None,
        "status": reminder.status,
    }


def create_reminder(
    state: GraphState,
    db: Session,
    *,
    appointment_id: str,
    hours_before: int = 24,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Schedule an APPOINTMENT_REMINDER (default: 24h before slot start).

    Persists a Reminder row — real DB write, not a fixed success string.
    """
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    appt = AppointmentRepository(db).get_by_id(appointment_id)
    if appt is None:
        return {"ok": False, "error": "appointment_not_found"}
    if appt.patient_id != patient_id:
        raise ToolScopeError("Cannot create reminder for another patient's appointment")

    if scheduled_at is None:
        slot = SlotRepository(db).get_by_id(appt.slot_id)
        if slot is None or slot.start_time is None:
            return {"ok": False, "error": "slot_not_found"}
        start = slot.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        scheduled_at = start - timedelta(hours=hours_before)

    reminder = ReminderRepository(db).create(
        patient_id=patient_id,
        appointment_id=appointment_id,
        reminder_type=REMINDER_APPOINTMENT,
        scheduled_at=scheduled_at,
    )
    return {"ok": True, "reminder": _reminder_dict(reminder)}


def schedule_followup(
    state: GraphState,
    db: Session,
    *,
    appointment_id: str,
    days_after: int = 7,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Schedule a post-visit FOLLOWUP_TASK linked to the appointment.

    Default: 7 days after the appointment slot start.
    """
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    appt = AppointmentRepository(db).get_by_id(appointment_id)
    if appt is None:
        return {"ok": False, "error": "appointment_not_found"}
    if appt.patient_id != patient_id:
        raise ToolScopeError("Cannot schedule follow-up for another patient's appointment")

    if scheduled_at is None:
        slot = SlotRepository(db).get_by_id(appt.slot_id)
        if slot is None or slot.start_time is None:
            return {"ok": False, "error": "slot_not_found"}
        start = slot.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        scheduled_at = start + timedelta(days=days_after)

    reminder = ReminderRepository(db).create(
        patient_id=patient_id,
        appointment_id=appointment_id,
        reminder_type=REMINDER_FOLLOWUP,
        scheduled_at=scheduled_at,
    )
    return {"ok": True, "followup": _reminder_dict(reminder)}


def send_notification(
    state: GraphState,
    db: Session,
    *,
    email_type: str,
    subject: str,
    body_text: str,
    to_user_id: str | None = None,
    reminder_id: str | None = None,
) -> dict[str, Any]:
    """
    Send an administrative notification email (no clinical advice).

    Recipient: to_user_id, or the workflow patient's user email.
    Uses services.email_service (stub until 2.5b). Updates Reminder status when provided.
    """
    patient_id = state.get("patient_id")
    if not state.get("actor_user_id") or not state.get("actor_role"):
        raise ToolScopeError("GraphState missing actor identity")

    users = UserRepository(db)
    patients = PatientRepository(db)

    if to_user_id:
        user = users.get_by_id(to_user_id)
    elif patient_id:
        assert_tool_scope(state, patient_id, db)
        profile = patients.get_by_id(patient_id)
        if profile is None:
            return {"ok": False, "error": "patient_not_found"}
        user = users.get_by_id(profile.user_id)
    else:
        return {"ok": False, "error": "no_recipient"}

    if user is None or not user.email:
        return {"ok": False, "error": "recipient_email_missing"}

    result = send_email(
        to_address=user.email,
        subject=subject,
        body_text=body_text,
        email_type=email_type,
        db=db,
        patient_id=patient_id,
    )

    if reminder_id:
        reminder = ReminderRepository(db).get_by_id(reminder_id)
        if reminder is not None:
            if result.get("ok") and result.get("status") == "SENT":
                ReminderRepository(db).mark_status(reminder, ReminderStatus.SENT.value)
            elif result.get("status") == "SKIPPED":
                # Dev/test: leave SCHEDULED or treat as handled — keep SCHEDULED
                pass
            elif not result.get("ok"):
                ReminderRepository(db).mark_status(reminder, ReminderStatus.FAILED.value)

    return {
        "ok": bool(result.get("ok")),
        "email_type": email_type,
        "to": user.email,
        "delivery": result,
    }

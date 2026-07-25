"""Administrative email templates — no clinical advice (PRD §14.2)."""

from __future__ import annotations

from typing import Any


def render_email(email_type: str, context: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """
    Return (subject, body_text, body_html) for a known email type.

    Context keys vary by type (patient_name, appointment_time, doctor_name, …).
    """
    ctx = context or {}
    name = ctx.get("patient_name") or "Patient"
    builder = _TEMPLATES.get(email_type, _generic)
    return builder(name, ctx)


def _wrap_html(title: str, paragraphs: list[str]) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<!DOCTYPE html>
<html><body style="font-family: sans-serif; color: #222;">
  <h2 style="color: #0b5fff;">{title}</h2>
  {body}
  <hr/>
  <p style="font-size: 12px; color: #666;">
    PulseDesk administrative notice — this message never contains diagnosis or prescription advice.
  </p>
</body></html>"""


def _appointment_confirmation(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    when = ctx.get("appointment_time", "your scheduled time")
    doctor = ctx.get("doctor_name", "your clinician")
    subject = "PulseDesk — Appointment confirmed"
    text = (
        f"Hello {name},\n\n"
        f"Your appointment with {doctor} is confirmed for {when}.\n"
        f"Please arrive a few minutes early and bring any requested documents.\n\n"
        f"— PulseDesk (administrative message only)\n"
    )
    html = _wrap_html(
        "Appointment confirmed",
        [
            f"Hello {name},",
            f"Your appointment with <strong>{doctor}</strong> is confirmed for <strong>{when}</strong>.",
            "Please arrive a few minutes early and bring any requested documents.",
        ],
    )
    return subject, text, html


def _appointment_reminder(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    when = ctx.get("appointment_time", "your upcoming appointment")
    doctor = ctx.get("doctor_name", "your clinician")
    subject = "PulseDesk — Appointment reminder"
    text = (
        f"Hello {name},\n\n"
        f"Reminder: you have an appointment with {doctor} at {when}.\n\n"
        f"— PulseDesk (administrative message only)\n"
    )
    html = _wrap_html(
        "Appointment reminder",
        [
            f"Hello {name},",
            f"This is a reminder for your appointment with <strong>{doctor}</strong> at <strong>{when}</strong>.",
        ],
    )
    return subject, text, html


def _document_request(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    missing = ctx.get("missing_documents") or ["required documents"]
    if isinstance(missing, list):
        missing_str = ", ".join(missing)
    else:
        missing_str = str(missing)
    dept = ctx.get("department_name", "your department")
    subject = "PulseDesk — Documents needed"
    text = (
        f"Hello {name},\n\n"
        f"To complete your {dept} request, please upload: {missing_str}.\n\n"
        f"— PulseDesk (administrative message only)\n"
    )
    html = _wrap_html(
        "Documents needed",
        [
            f"Hello {name},",
            f"To complete your <strong>{dept}</strong> request, please upload: <strong>{missing_str}</strong>.",
        ],
    )
    return subject, text, html


def _escalation_alert(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    reason = ctx.get("reason", "A workflow requires staff review")
    workflow_id = ctx.get("workflow_run_id", "unknown")
    subject = "PulseDesk — Staff escalation"
    text = (
        f"Hello {name},\n\n"
        f"Escalation for workflow {workflow_id}.\n"
        f"Reason: {reason}\n"
        f"Please review in the staff console.\n\n"
        f"— PulseDesk (administrative message only)\n"
    )
    html = _wrap_html(
        "Staff escalation",
        [
            f"Hello {name},",
            f"Workflow <code>{workflow_id}</code> needs review.",
            f"Reason: {reason}",
        ],
    )
    return subject, text, html


def _followup_task(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    when = ctx.get("scheduled_at", "the scheduled date")
    subject = "PulseDesk — Follow-up scheduled"
    text = (
        f"Hello {name},\n\n"
        f"A post-visit follow-up has been scheduled for {when}.\n\n"
        f"— PulseDesk (administrative message only)\n"
    )
    html = _wrap_html(
        "Follow-up scheduled",
        [
            f"Hello {name},",
            f"A post-visit follow-up has been scheduled for <strong>{when}</strong>.",
        ],
    )
    return subject, text, html


def _generic(name: str, ctx: dict[str, Any]) -> tuple[str, str, str]:
    subject = ctx.get("subject") or "PulseDesk notice"
    body = ctx.get("body_text") or "You have an administrative update from PulseDesk."
    text = f"Hello {name},\n\n{body}\n\n— PulseDesk\n"
    html = _wrap_html(subject, [f"Hello {name},", body])
    return subject, text, html


_TEMPLATES = {
    "APPOINTMENT_CONFIRMATION": _appointment_confirmation,
    "APPOINTMENT_REMINDER": _appointment_reminder,
    "DOCUMENT_REQUEST": _document_request,
    "ESCALATION_ALERT": _escalation_alert,
    "FOLLOWUP_TASK": _followup_task,
}

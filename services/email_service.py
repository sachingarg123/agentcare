"""SMTP email delivery + notification logging (PRD §14.2 / Phase 2.5b)."""

from __future__ import annotations

import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlalchemy.orm import Session

from core.config import get_settings
from db.models import NotificationStatus
from db.repositories.notification_repo import NotificationRepository
from services.email_templates import render_email

logger = logging.getLogger("agentcare.email")


def send_email(
    *,
    to_address: str,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    email_type: str = "GENERIC",
    template_context: dict[str, Any] | None = None,
    db: Session | None = None,
    patient_id: str | None = None,
) -> dict[str, Any]:
    """
    Send an administrative email via SMTP.

    - If template_context is provided (or subject/body omitted), render from email_type.
    - SMTP_DISABLED → status SKIPPED (still logged when db session provided).
    - On success → SENT; on error → FAILED.
    """
    if not to_address:
        return _finish(
            db,
            to_address="",
            email_type=email_type,
            subject=subject or "",
            status=NotificationStatus.FAILED.value,
            error="missing_recipient",
            patient_id=patient_id,
            ok=False,
        )

    # Render template when caller uses typed emails
    if template_context is not None or subject is None or body_text is None:
        tpl_subject, tpl_text, tpl_html = render_email(email_type, template_context)
        subject = subject or tpl_subject
        body_text = body_text or tpl_text
        body_html = body_html or tpl_html

    assert subject is not None and body_text is not None

    settings = get_settings()
    if settings.smtp_disabled:
        return _finish(
            db,
            to_address=to_address,
            email_type=email_type,
            subject=subject,
            status=NotificationStatus.SKIPPED.value,
            error="SMTP_DISABLED",
            patient_id=patient_id,
            ok=True,
        )

    try:
        _smtp_send(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            from_addr=settings.smtp_from,
            to_addr=to_address,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            use_tls=settings.smtp_tls,
        )
        return _finish(
            db,
            to_address=to_address,
            email_type=email_type,
            subject=subject,
            status=NotificationStatus.SENT.value,
            error=None,
            patient_id=patient_id,
            ok=True,
        )
    except Exception as exc:
        logger.exception("SMTP send failed")
        return _finish(
            db,
            to_address=to_address,
            email_type=email_type,
            subject=subject,
            status=NotificationStatus.FAILED.value,
            error=str(exc),
            patient_id=patient_id,
            ok=False,
        )


def _smtp_send(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    use_tls: bool,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    # Envelope sender must be a bare address; display-name form is header-only.
    envelope_from = from_addr
    match = re.search(r"<([^>]+)>", from_addr)
    if match:
        envelope_from = match.group(1).strip()

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if user:
            server.login(user, password)
        server.sendmail(envelope_from, [to_addr], msg.as_string())


def _finish(
    db: Session | None,
    *,
    to_address: str,
    email_type: str,
    subject: str,
    status: str,
    error: str | None,
    patient_id: str | None,
    ok: bool,
) -> dict[str, Any]:
    notification_id = None
    if db is not None and to_address:
        row = NotificationRepository(db).create(
            to_address=to_address,
            email_type=email_type,
            subject=subject,
            status=status,
            error=error,
            patient_id=patient_id,
        )
        notification_id = row.id

    return {
        "ok": ok,
        "status": status,
        "reason": error,
        "to": to_address,
        "subject": subject,
        "email_type": email_type,
        "notification_id": notification_id,
    }

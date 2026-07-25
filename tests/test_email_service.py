"""Phase 2.5b — SMTP email service + aiosmtpd capture + templates."""

from __future__ import annotations

from email import message_from_bytes

import pytest
from aiosmtpd.controller import Controller
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import get_settings
from db.models import Base, NotificationStatus
from db.repositories import NotificationRepository
from services.email_service import send_email
from services.email_templates import render_email


class _MemoryHandler:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.messages.append(
            {
                "mail_from": envelope.mail_from,
                "rcpt_tos": list(envelope.rcpt_tos),
                "content": envelope.content,
            }
        )
        return "250 OK"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_render_appointment_confirmation_template():
    subject, text, html = render_email(
        "APPOINTMENT_CONFIRMATION",
        {"patient_name": "Asha", "doctor_name": "Dr. Mehta", "appointment_time": "tomorrow 10:00"},
    )
    assert "confirmed" in subject.lower() or "Appointment" in subject
    assert "Asha" in text and "Dr. Mehta" in text
    assert "administrative" in html.lower()
    assert "diagnosis" in html.lower() or "prescription" in html.lower()


def test_send_email_skipped_when_disabled(monkeypatch, db_session):
    monkeypatch.setenv("SMTP_DISABLED", "true")
    get_settings.cache_clear()
    result = send_email(
        to_address="asha@example.com",
        email_type="APPOINTMENT_CONFIRMATION",
        template_context={"patient_name": "Asha", "doctor_name": "Dr. X", "appointment_time": "soon"},
        db=db_session,
    )
    db_session.commit()
    assert result["ok"] is True
    assert result["status"] == NotificationStatus.SKIPPED.value
    rows = NotificationRepository(db_session).list_recent()
    assert len(rows) == 1
    assert rows[0].status == NotificationStatus.SKIPPED.value
    get_settings.cache_clear()


def test_send_email_via_aiosmtpd(monkeypatch, db_session):
    handler = _MemoryHandler()
    # Bind explicitly — port 0 can fail with errno 49 on some macOS setups
    controller = Controller(handler, hostname="localhost", port=8025)
    controller.start()
    try:
        monkeypatch.setenv("SMTP_DISABLED", "false")
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "8025")
        monkeypatch.setenv("SMTP_USER", "")
        monkeypatch.setenv("SMTP_PASSWORD", "")
        monkeypatch.setenv("SMTP_FROM", "PulseDesk <pulsedesk@test.local>")
        monkeypatch.setenv("SMTP_TLS", "false")
        get_settings.cache_clear()

        result = send_email(
            to_address="patient@example.com",
            email_type="APPOINTMENT_REMINDER",
            template_context={
                "patient_name": "Ravi",
                "doctor_name": "Dr. Mehta",
                "appointment_time": "2026-07-26 09:00",
            },
            db=db_session,
        )
        db_session.commit()

        assert result["ok"] is True
        assert result["status"] == NotificationStatus.SENT.value
        assert len(handler.messages) == 1
        raw = handler.messages[0]["content"]
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        msg = message_from_bytes(raw)
        assert "Reminder" in (msg["Subject"] or "") or "reminder" in (msg["Subject"] or "").lower()
        assert "patient@example.com" in handler.messages[0]["rcpt_tos"]
    finally:
        controller.stop()
        get_settings.cache_clear()

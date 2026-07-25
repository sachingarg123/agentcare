"""Notification send-log persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Notification


class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        to_address: str,
        email_type: str,
        subject: str,
        status: str,
        error: str | None = None,
        patient_id: str | None = None,
    ) -> Notification:
        row = Notification(
            to_address=to_address,
            email_type=email_type,
            subject=subject,
            status=status,
            error=error,
            patient_id=patient_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_recent(self, *, limit: int = 50) -> list[Notification]:
        stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())

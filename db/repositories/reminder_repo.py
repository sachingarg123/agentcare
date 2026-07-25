"""Reminders and follow-up tasks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Reminder, ReminderStatus


class ReminderRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, reminder_id: str) -> Reminder | None:
        return self.db.get(Reminder, reminder_id)

    def list_for_patient(self, patient_id: str) -> list[Reminder]:
        stmt = (
            select(Reminder)
            .where(Reminder.patient_id == patient_id)
            .order_by(Reminder.scheduled_at)
        )
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        *,
        patient_id: str,
        reminder_type: str,
        scheduled_at: datetime,
        appointment_id: str | None = None,
        status: str = ReminderStatus.SCHEDULED.value,
    ) -> Reminder:
        reminder = Reminder(
            patient_id=patient_id,
            appointment_id=appointment_id,
            reminder_type=reminder_type,
            scheduled_at=scheduled_at,
            status=status,
        )
        self.db.add(reminder)
        self.db.flush()
        return reminder

    def mark_status(self, reminder: Reminder, status: str) -> Reminder:
        reminder.status = status
        self.db.flush()
        return reminder

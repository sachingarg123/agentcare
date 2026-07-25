"""Appointment slot availability — used by booking tools."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AppointmentSlot, SlotStatus


class SlotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, slot_id: str) -> AppointmentSlot | None:
        return self.db.get(AppointmentSlot, slot_id)

    def list_available(
        self,
        *,
        doctor_id: str | None = None,
        department_id: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[AppointmentSlot]:
        """Query open slots. Optional filters for doctor / time window.

        department_id filter joins via Doctor when provided (caller can also
        pre-resolve doctor ids — kept simple here with a subquery-style join).
        """
        from db.models import Doctor

        stmt = select(AppointmentSlot).where(
            AppointmentSlot.status == SlotStatus.AVAILABLE.value
        )
        if doctor_id:
            stmt = stmt.where(AppointmentSlot.doctor_id == doctor_id)
        if department_id:
            stmt = stmt.join(Doctor).where(Doctor.department_id == department_id)
        if after:
            stmt = stmt.where(AppointmentSlot.start_time >= after)
        if before:
            stmt = stmt.where(AppointmentSlot.start_time < before)
        stmt = stmt.order_by(AppointmentSlot.start_time).limit(limit)
        return list(self.db.scalars(stmt).all())

    def list_for_doctor(
        self,
        doctor_id: str,
        *,
        limit: int = 100,
    ) -> list[AppointmentSlot]:
        stmt = (
            select(AppointmentSlot)
            .where(AppointmentSlot.doctor_id == doctor_id)
            .order_by(AppointmentSlot.start_time)
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self, *, limit: int = 200) -> list[AppointmentSlot]:
        stmt = (
            select(AppointmentSlot)
            .order_by(AppointmentSlot.start_time.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        *,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime,
        status: str = SlotStatus.AVAILABLE.value,
    ) -> AppointmentSlot:
        slot = AppointmentSlot(
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            status=status,
        )
        self.db.add(slot)
        self.db.flush()
        return slot

    def update(
        self,
        slot: AppointmentSlot,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        status: str | None = None,
        doctor_id: str | None = None,
    ) -> AppointmentSlot:
        if start_time is not None:
            slot.start_time = start_time
        if end_time is not None:
            slot.end_time = end_time
        if status is not None:
            slot.status = status
        if doctor_id is not None:
            slot.doctor_id = doctor_id
        self.db.flush()
        return slot

    def delete(self, slot: AppointmentSlot) -> None:
        self.db.delete(slot)
        self.db.flush()

    def mark_booked(self, slot: AppointmentSlot) -> AppointmentSlot:
        slot.status = SlotStatus.BOOKED.value
        self.db.flush()
        return slot

    def mark_available(self, slot: AppointmentSlot) -> AppointmentSlot:
        slot.status = SlotStatus.AVAILABLE.value
        self.db.flush()
        return slot

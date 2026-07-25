"""Appointment booking / cancel / reschedule persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Appointment, AppointmentStatus, AppointmentSlot, SlotStatus


class AppointmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, appointment_id: str) -> Appointment | None:
        return self.db.get(Appointment, appointment_id)

    def list_for_patient(self, patient_id: str) -> list[Appointment]:
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id)
            .order_by(Appointment.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def book(
        self,
        *,
        patient_id: str,
        doctor_id: str,
        slot: AppointmentSlot,
        reason: str | None = None,
    ) -> Appointment:
        """Create appointment and mark slot BOOKED in one flush (caller commits)."""
        if slot.status != SlotStatus.AVAILABLE.value:
            raise ValueError(f"Slot {slot.id} is not AVAILABLE (status={slot.status})")

        slot.status = SlotStatus.BOOKED.value
        appointment = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            slot_id=slot.id,
            status=AppointmentStatus.BOOKED.value,
            reason=reason,
        )
        self.db.add(appointment)
        self.db.flush()
        return appointment

    def cancel(self, appointment: Appointment) -> Appointment:
        appointment.status = AppointmentStatus.CANCELLED.value
        slot = self.db.get(AppointmentSlot, appointment.slot_id)
        if slot:
            slot.status = SlotStatus.AVAILABLE.value
        self.db.flush()
        return appointment

    def reschedule(
        self,
        appointment: Appointment,
        *,
        new_slot: AppointmentSlot,
        new_doctor_id: str | None = None,
    ) -> Appointment:
        if new_slot.status != SlotStatus.AVAILABLE.value:
            raise ValueError(f"Slot {new_slot.id} is not AVAILABLE")

        old_slot = self.db.get(AppointmentSlot, appointment.slot_id)
        if old_slot:
            old_slot.status = SlotStatus.AVAILABLE.value

        new_slot.status = SlotStatus.BOOKED.value
        appointment.slot_id = new_slot.id
        if new_doctor_id:
            appointment.doctor_id = new_doctor_id
        else:
            appointment.doctor_id = new_slot.doctor_id
        appointment.status = AppointmentStatus.RESCHEDULED.value
        self.db.flush()
        return appointment

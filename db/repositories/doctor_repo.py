"""Doctor reference data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Doctor


class DoctorRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, doctor_id: str) -> Doctor | None:
        return self.db.get(Doctor, doctor_id)

    def list_by_department(
        self, department_id: str, *, active_only: bool = True
    ) -> list[Doctor]:
        stmt = select(Doctor).where(Doctor.department_id == department_id)
        if active_only:
            stmt = stmt.where(Doctor.active.is_(True))
        stmt = stmt.order_by(Doctor.name)
        return list(self.db.scalars(stmt).all())

    def list_all(self, *, department_id: str | None = None) -> list[Doctor]:
        stmt = select(Doctor)
        if department_id:
            stmt = stmt.where(Doctor.department_id == department_id)
        stmt = stmt.order_by(Doctor.name)
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        *,
        department_id: str,
        name: str,
        active: bool = True,
    ) -> Doctor:
        doctor = Doctor(department_id=department_id, name=name, active=active)
        self.db.add(doctor)
        self.db.flush()
        return doctor

    def update(
        self,
        doctor: Doctor,
        *,
        department_id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
    ) -> Doctor:
        if department_id is not None:
            doctor.department_id = department_id
        if name is not None:
            doctor.name = name
        if active is not None:
            doctor.active = active
        self.db.flush()
        return doctor

    def deactivate(self, doctor: Doctor) -> Doctor:
        doctor.active = False
        self.db.flush()
        return doctor

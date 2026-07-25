"""Patient profile persistence — subject of workflows / appointments / docs."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import PatientProfile


class PatientRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, patient_id: str) -> PatientProfile | None:
        return self.db.get(PatientProfile, patient_id)

    def get_by_user_id(self, user_id: str) -> PatientProfile | None:
        stmt = select(PatientProfile).where(PatientProfile.user_id == user_id)
        return self.db.scalars(stmt).first()

    def create(
        self,
        *,
        user_id: str,
        date_of_birth: date | None = None,
        phone: str | None = None,
        preferred_language: str = "en",
        emergency_contact: str | None = None,
    ) -> PatientProfile:
        profile = PatientProfile(
            user_id=user_id,
            date_of_birth=date_of_birth,
            phone=phone,
            preferred_language=preferred_language,
            emergency_contact=emergency_contact,
        )
        self.db.add(profile)
        self.db.flush()
        return profile

    def get_or_create_for_user(self, user_id: str, **kwargs) -> PatientProfile:
        existing = self.get_by_user_id(user_id)
        if existing:
            return existing
        return self.create(user_id=user_id, **kwargs)

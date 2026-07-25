"""Object-level access checks (PRD §4.4 Layer 2).

PATIENT may only touch their own patient_id resources.
STAFF / ADMIN may access all (route-level still gates what they can do).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from db.models import (
    Appointment,
    PatientDocument,
    Reminder,
    User,
    UserRole,
    WorkflowRun,
)
from db.repositories import PatientRepository


def _is_staff_or_admin(user: User) -> bool:
    return user.role in (UserRole.STAFF.value, UserRole.ADMIN.value)


def _patient_id_for_user(db: Session, user: User) -> str:
    """Resolve PatientProfile.id for a PATIENT user; 403 if missing."""
    profile = PatientRepository(db).get_by_user_id(user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No patient profile for this user",
        )
    return profile.id


def assert_patient_owns_workflow(
    user: User, workflow: WorkflowRun, db: Session
) -> None:
    if _is_staff_or_admin(user):
        return
    if user.role != UserRole.PATIENT.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if workflow.patient_id != _patient_id_for_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your workflow",
        )


def assert_patient_owns_appointment(
    user: User, appointment: Appointment, db: Session
) -> None:
    if _is_staff_or_admin(user):
        return
    if user.role != UserRole.PATIENT.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if appointment.patient_id != _patient_id_for_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your appointment",
        )


def assert_patient_owns_document(
    user: User, document: PatientDocument, db: Session
) -> None:
    if _is_staff_or_admin(user):
        return
    if user.role != UserRole.PATIENT.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if document.patient_id != _patient_id_for_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your document",
        )


def assert_patient_owns_reminder(
    user: User, reminder: Reminder, db: Session
) -> None:
    if _is_staff_or_admin(user):
        return
    if user.role != UserRole.PATIENT.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if reminder.patient_id != _patient_id_for_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your reminder",
        )

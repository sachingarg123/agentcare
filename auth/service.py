"""Auth helpers used by auth routes and tests."""

from __future__ import annotations

from sqlalchemy.orm import Session

from auth.passwords import hash_password, verify_password
from db.models import User, UserRole
from db.repositories import PatientRepository, UserRepository


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return User if email/password match; otherwise None."""
    user = UserRepository(db).get_by_email(email)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def register_patient(
    db: Session,
    *,
    name: str,
    email: str,
    password: str,
    phone: str | None = None,
) -> User:
    """
    Create a PATIENT user + PatientProfile.

    Raises ValueError if email is already registered.
    Does not commit — caller commits.
    """
    users = UserRepository(db)
    if users.get_by_email(email) is not None:
        raise ValueError("email_taken")

    user = users.create(
        name=name.strip(),
        email=email,
        password_hash=hash_password(password),
        role=UserRole.PATIENT.value,
    )
    PatientRepository(db).create(user_id=user.id, phone=phone)
    return user

"""User persistence — login identity + RBAC role."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import User, UserRole


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return self.db.scalars(stmt).first()

    def create(
        self,
        *,
        name: str,
        email: str,
        password_hash: str,
        role: str = UserRole.PATIENT.value,
    ) -> User:
        user = User(
            name=name,
            email=email.lower(),
            password_hash=password_hash,
            role=role,
        )
        self.db.add(user)
        self.db.flush()  # assign id without full commit (caller controls commit)
        return user

    def list_by_role(self, role: str) -> list[User]:
        stmt = select(User).where(User.role == role).order_by(User.created_at)
        return list(self.db.scalars(stmt).all())

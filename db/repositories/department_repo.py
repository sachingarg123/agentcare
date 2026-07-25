"""Department reference data — routing targets."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Department, DepartmentDocumentRequirement


class DepartmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, department_id: str) -> Department | None:
        return self.db.get(Department, department_id)

    def get_by_name(self, name: str) -> Department | None:
        stmt = select(Department).where(Department.name == name)
        return self.db.scalars(stmt).first()

    def list_active(self) -> list[Department]:
        stmt = (
            select(Department)
            .where(Department.active.is_(True))
            .order_by(Department.name)
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self) -> list[Department]:
        stmt = select(Department).order_by(Department.name)
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        active: bool = True,
    ) -> Department:
        dept = Department(name=name, description=description, active=active)
        self.db.add(dept)
        self.db.flush()
        return dept

    def update(
        self,
        dept: Department,
        *,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> Department:
        if name is not None:
            dept.name = name
        if description is not None:
            dept.description = description
        if active is not None:
            dept.active = active
        self.db.flush()
        return dept

    def deactivate(self, dept: Department) -> Department:
        dept.active = False
        self.db.flush()
        return dept

    def add_document_requirement(
        self,
        *,
        department_id: str,
        document_type: str,
        required: bool = True,
    ) -> DepartmentDocumentRequirement:
        req = DepartmentDocumentRequirement(
            department_id=department_id,
            document_type=document_type,
            required=required,
        )
        self.db.add(req)
        self.db.flush()
        return req

    def list_document_requirements(
        self, department_id: str, *, required_only: bool = False
    ) -> list[DepartmentDocumentRequirement]:
        stmt = select(DepartmentDocumentRequirement).where(
            DepartmentDocumentRequirement.department_id == department_id
        )
        if required_only:
            stmt = stmt.where(DepartmentDocumentRequirement.required.is_(True))
        return list(self.db.scalars(stmt).all())

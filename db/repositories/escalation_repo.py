"""Escalation / HITL records."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Escalation, EscalationStatus


class EscalationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, escalation_id: str) -> Escalation | None:
        return self.db.get(Escalation, escalation_id)

    def list_pending(self) -> list[Escalation]:
        stmt = (
            select(Escalation)
            .where(Escalation.status == EscalationStatus.PENDING.value)
            .order_by(Escalation.created_at)
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self, *, limit: int = 100) -> list[Escalation]:
        stmt = (
            select(Escalation).order_by(Escalation.created_at.desc()).limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def create(self, *, workflow_run_id: str, reason: str) -> Escalation:
        esc = Escalation(
            workflow_run_id=workflow_run_id,
            reason=reason,
            status=EscalationStatus.PENDING.value,
        )
        self.db.add(esc)
        self.db.flush()
        return esc

    def resolve(
        self,
        escalation: Escalation,
        *,
        status: str,
        reviewed_by: str,
    ) -> Escalation:
        escalation.status = status
        escalation.reviewed_by = reviewed_by
        self.db.flush()
        return escalation

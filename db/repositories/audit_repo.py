"""Append-only audit trail."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AuditEvent


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        event_metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=event_metadata or {},
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_recent(self, *, limit: int = 100) -> list[AuditEvent]:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def list_filtered(
        self,
        *,
        actor_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Staff/admin audit query with optional filters (newest first)."""
        stmt = select(AuditEvent)
        if actor_id:
            stmt = stmt.where(AuditEvent.actor_id == actor_id)
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        stmt = stmt.order_by(AuditEvent.created_at.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == entity_id,
            )
            .order_by(AuditEvent.created_at)
        )
        return list(self.db.scalars(stmt).all())

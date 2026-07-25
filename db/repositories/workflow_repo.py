"""Workflow run persistence — agent journey state snapshots."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import WorkflowRun, WorkflowStatus


class WorkflowRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, workflow_id: str) -> WorkflowRun | None:
        return self.db.get(WorkflowRun, workflow_id)

    def list_for_patient(self, patient_id: str) -> list[WorkflowRun]:
        stmt = (
            select(WorkflowRun)
            .where(WorkflowRun.patient_id == patient_id)
            .order_by(WorkflowRun.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self, *, status: str | None = None, limit: int = 100) -> list[WorkflowRun]:
        stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit)
        if status:
            stmt = select(WorkflowRun).where(WorkflowRun.status == status).order_by(
                WorkflowRun.created_at.desc()
            ).limit(limit)
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        *,
        patient_id: str,
        current_step: str = "coordinator_init",
        state: dict[str, Any] | None = None,
        status: str = WorkflowStatus.PENDING.value,
        id: str | None = None,
    ) -> WorkflowRun:
        kwargs: dict[str, Any] = {
            "patient_id": patient_id,
            "current_step": current_step,
            "state": state or {},
            "status": status,
        }
        if id is not None:
            kwargs["id"] = id
        run = WorkflowRun(**kwargs)
        self.db.add(run)
        self.db.flush()
        return run

    def update_state(
        self,
        run: WorkflowRun,
        *,
        current_step: str | None = None,
        state: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> WorkflowRun:
        if current_step is not None:
            run.current_step = current_step
        if state is not None:
            run.state = state
        if status is not None:
            run.status = status
        self.db.flush()
        return run

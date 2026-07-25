"""WebSocket: live workflow progress for patient/staff UI (Phase 5.7)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from auth.jwt import TokenError, decode_access_token
from auth.ownership import assert_patient_owns_workflow
from db.repositories import UserRepository, WorkflowRepository
from db.session import get_db
from services.workflow_events import get_event_hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


def _snapshot_event(workflow) -> dict:
    state = workflow.state or {}
    return {
        "type": "snapshot",
        "workflow_run_id": workflow.id,
        "patient_id": workflow.patient_id,
        "status": workflow.status,
        "current_step": workflow.current_step,
        "confirmation": state.get("confirmation"),
        "hitl_required": state.get("hitl_required"),
        "hitl_reason": state.get("hitl_reason"),
        "error": state.get("error"),
    }


@router.websocket("/ws/workflows/{workflow_id}")
async def workflow_progress_ws(
    websocket: WebSocket,
    workflow_id: str,
    token: str = Query(
        ...,
        description="JWT access token (browsers cannot set WS Authorization easily)",
    ),
    db: Session = Depends(get_db),
) -> None:
    """
    Stream progress for one workflow.

    Auth: ``?token=<jwt>``. Object access: same rules as GET /requests/{id}.
    First message is always a DB snapshot; then live hub events until disconnect.
    """
    hub = get_event_hub()
    queue = None
    try:
        try:
            claims = decode_access_token(token)
        except TokenError:
            await websocket.close(code=4401)
            return

        user = UserRepository(db).get_by_id(claims.get("sub") or "")
        if user is None:
            await websocket.close(code=4401)
            return

        workflow = WorkflowRepository(db).get_by_id(workflow_id)
        if workflow is None:
            await websocket.close(code=4404)
            return

        try:
            assert_patient_owns_workflow(user, workflow, db)
        except HTTPException:
            await websocket.close(code=4403)
            return

        await websocket.accept()
        await websocket.send_json(_snapshot_event(workflow))

        latest = hub.latest(workflow_id)
        if latest and latest.get("type") != "snapshot":
            await websocket.send_json(latest)

        queue = hub.subscribe(workflow_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "workflow_run_id": workflow_id})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug("WS disconnected workflow_id=%s", workflow_id)
    finally:
        if queue is not None:
            hub.unsubscribe(workflow_id, queue)

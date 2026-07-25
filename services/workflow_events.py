"""In-process pub/sub for workflow progress (Phase 5.7).

Single-process MVP: subscribers are asyncio.Queues keyed by workflow_run_id.
Multi-worker / multi-host would need Redis or similar — out of scope for hackathon.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class WorkflowEventHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._latest: dict[str, dict[str, Any]] = {}

    def latest(self, workflow_id: str) -> dict[str, Any] | None:
        return self._latest.get(workflow_id)

    def subscribe(self, workflow_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers[workflow_id].append(queue)
        return queue

    def unsubscribe(self, workflow_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(workflow_id)
        if not subs:
            return
        try:
            subs.remove(queue)
        except ValueError:
            pass
        if not subs:
            self._subscribers.pop(workflow_id, None)

    def publish(self, workflow_id: str, event: dict[str, Any]) -> None:
        """Publish from sync or async code (put_nowait; drop if a queue is full)."""
        if not workflow_id:
            return
        payload = {
            **event,
            "workflow_run_id": workflow_id,
            "ts": event.get("ts") or datetime.now(timezone.utc).isoformat(),
        }
        self._latest[workflow_id] = payload
        for queue in list(self._subscribers.get(workflow_id, [])):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                continue


_hub = WorkflowEventHub()


def get_event_hub() -> WorkflowEventHub:
    return _hub


def emit_workflow_event(
    workflow_id: str | None,
    *,
    event_type: str,
    current_step: str | None = None,
    status: str | None = None,
    confirmation: dict[str, Any] | None = None,
    interrupt: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not workflow_id:
        return
    event: dict[str, Any] = {"type": event_type}
    if current_step is not None:
        event["current_step"] = current_step
    if status is not None:
        event["status"] = status
    if confirmation is not None:
        event["confirmation"] = confirmation
    if interrupt is not None:
        event["interrupt"] = interrupt
    if extra:
        event.update(extra)
    get_event_hub().publish(workflow_id, event)

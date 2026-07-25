"""Phase 5.7 — WebSocket workflow progress."""

from __future__ import annotations

from services.workflow_events import emit_workflow_event, get_event_hub
from tests.conftest import login


def test_ws_snapshot_for_owner(client, db_session):
    token = login(client, "asha.patient@example.com")
    workflow_id = db_session.info["asha_workflow_id"]

    with client.websocket_connect(
        f"/api/v1/ws/workflows/{workflow_id}?token={token}"
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["workflow_run_id"] == workflow_id
        assert msg["patient_id"] == db_session.info["asha_patient_id"]


def test_ws_forbidden_for_other_patient(client, db_session):
    from starlette.websockets import WebSocketDisconnect

    token = login(client, "ravi.patient@example.com")
    workflow_id = db_session.info["asha_workflow_id"]

    try:
        with client.websocket_connect(
            f"/api/v1/ws/workflows/{workflow_id}?token={token}"
        ):
            raise AssertionError("expected WebSocketDisconnect before accept")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403


def test_ws_receives_published_progress(client, db_session):
    token = login(client, "asha.patient@example.com")
    workflow_id = db_session.info["asha_workflow_id"]
    hub = get_event_hub()

    with client.websocket_connect(
        f"/api/v1/ws/workflows/{workflow_id}?token={token}"
    ) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        emit_workflow_event(
            workflow_id,
            event_type="completed",
            current_step="coordinator_finalize",
            status="completed",
            confirmation={"ok": True},
        )
        # May receive replay of latest if publish happened after subscribe setup
        # — drain until completed
        seen = []
        for _ in range(5):
            msg = ws.receive_json()
            seen.append(msg["type"])
            if msg["type"] == "completed":
                assert msg["confirmation"]["ok"] is True
                break
        assert "completed" in seen


def test_staff_can_watch_any_workflow(client, db_session):
    token = login(client, "sam.staff@example.com")
    workflow_id = db_session.info["asha_workflow_id"]
    with client.websocket_connect(
        f"/api/v1/ws/workflows/{workflow_id}?token={token}"
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"


def test_hub_subscribe_unsubscribe():
    hub = get_event_hub()
    q = hub.subscribe("wf-test-1")
    hub.publish("wf-test-1", {"type": "started", "current_step": "x"})
    assert q.get_nowait()["type"] == "started"
    hub.unsubscribe("wf-test-1", q)

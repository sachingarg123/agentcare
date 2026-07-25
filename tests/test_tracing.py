"""Phase 4.5 — LangSmith run config tags / metadata (no network calls)."""

from __future__ import annotations

import os

from core.config import get_settings
from core.tracing import build_run_config, configure_langsmith


def test_build_run_config_includes_prd_metadata():
    cfg = build_run_config(
        thread_id="wf-123",
        workflow_run_id="wf-123",
        patient_id="pat-1",
        actor_role="PATIENT",
        actor_user_id="user-1",
    )
    assert cfg["configurable"]["thread_id"] == "wf-123"
    assert "agentcare" in cfg["tags"]
    assert "role:PATIENT" in cfg["tags"]
    assert "workflow:wf-123" in cfg["tags"]
    assert cfg["metadata"]["workflow_run_id"] == "wf-123"
    assert cfg["metadata"]["patient_id"] == "pat-1"
    assert cfg["metadata"]["actor_role"] == "PATIENT"
    assert cfg["metadata"]["actor_user_id"] == "user-1"
    assert cfg["run_name"].startswith("agentcare:")


def test_build_run_config_resume_extra_tags():
    cfg = build_run_config(
        thread_id="wf-1",
        workflow_run_id="wf-1",
        patient_id="p1",
        actor_role="STAFF",
        extra_tags=["hitl_resume"],
        extra_metadata={"hitl_decision": "approve"},
    )
    assert "hitl_resume" in cfg["tags"]
    assert cfg["metadata"]["hitl_decision"] == "approve"


def test_configure_langsmith_sets_env(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "agentcare-test")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    get_settings.cache_clear()

    enabled = configure_langsmith()
    assert enabled is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_PROJECT"] == "agentcare-test"
    assert os.environ["LANGSMITH_API_KEY"] == "ls-test-key"

    get_settings.cache_clear()

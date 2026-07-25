"""LangSmith / LangChain tracing helpers (Phase 4.5).

Configures env from settings when tracing is enabled, and builds invoke
``config`` with tags + metadata (workflow_run_id, patient_id, actor_role).
"""

from __future__ import annotations

import os
from typing import Any

from core.config import get_settings


def configure_langsmith() -> bool:
    """
    Sync LangSmith-related env vars from settings.

    Returns True if tracing is enabled (caller may still run without a key;
    LangChain no-ops or warns). Safe to call multiple times.
    """
    settings = get_settings()
    enabled = bool(settings.langchain_tracing_v2)
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if enabled else "false"
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project or "agentcare"
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    return enabled


def build_run_config(
    *,
    thread_id: str,
    workflow_run_id: str,
    patient_id: str | None = None,
    actor_role: str | None = None,
    actor_user_id: str | None = None,
    extra_tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    LangGraph invoke config with checkpointer thread + LangSmith tags/metadata.

    PRD §13.1: tag runs with workflow_run_id, patient_id, actor_role.
    """
    tags = ["agentcare", f"workflow:{workflow_run_id}"]
    if actor_role:
        tags.append(f"role:{actor_role}")
    if extra_tags:
        tags.extend(extra_tags)

    metadata: dict[str, Any] = {
        "workflow_run_id": workflow_run_id,
        "patient_id": patient_id,
        "actor_role": actor_role,
        "actor_user_id": actor_user_id,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    # Drop None values for cleaner LangSmith UI
    metadata = {k: v for k, v in metadata.items() if v is not None}

    return {
        "configurable": {"thread_id": thread_id},
        "tags": tags,
        "metadata": metadata,
        "run_name": f"agentcare:{workflow_run_id[:8]}",
    }

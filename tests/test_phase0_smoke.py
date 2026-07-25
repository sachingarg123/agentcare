"""Smoke tests for Phase 0 bootstrap."""

from core.config import get_settings
from core import llm as llm_module


def test_settings_load(monkeypatch):
    monkeypatch.setenv("APP_NAME", "PulseDesk")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_name == "PulseDesk"
    assert "agentcare.db" in settings.database_url or "sqlite" in settings.database_url
    assert settings.google_model == "gemma-4-31b-it"
    get_settings.cache_clear()


def test_llm_factory_importable():
    """Factory must exist; calling it without API keys should raise clearly."""
    assert callable(llm_module.get_llm)


def test_fastapi_app_imports():
    import main

    assert main.app.title == "PulseDesk"
    paths = set(main.app.openapi()["paths"].keys())
    assert "/health" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/staff/escalations" in paths
    assert "/api/v1/staff/requests" in paths
    assert "/api/v1/staff/departments" in paths
    assert "/api/v1/staff/audit" in paths
    assert "/api/v1/workflows/{workflow_id}/resume" in paths
    assert "/api/v1/requests/{workflow_id}" in paths
    # WebSockets are not in OpenAPI — verify via included router routes
    ws_paths = set()
    for route in main.app.routes:
        original = getattr(route, "original_router", None)
        if original is None:
            continue
        for sub in original.routes:
            path = getattr(sub, "path", None)
            if path:
                ws_paths.add(path)
    assert "/ws/workflows/{workflow_id}" in ws_paths

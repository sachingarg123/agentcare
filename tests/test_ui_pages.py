"""Phase 6 — UI pages are served (React SPA when built, else legacy static HTML)."""

from pathlib import Path

from main import FRONTEND_DIST, SPA_INDEX, STATIC_DIR

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_ui_pages_served(client):
    pages = [
        "/",
        "/patient",
        "/patient/workflows/demo-id",
        "/staff",
        "/staff/escalations/demo-esc",
        "/staff/admin",
    ]
    for path in pages:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "text/html" in resp.headers["content-type"]
        body = resp.content.lower()
        assert b"pulsedesk" in body or b"agentcare" in body


def test_static_assets(client):
    if SPA_INDEX.is_file():
        # Built SPA: hashed assets under /assets, not legacy /static/*
        assets_dir = FRONTEND_DIST / "assets"
        css_files = list(assets_dir.glob("*.css"))
        js_files = list(assets_dir.glob("*.js"))
        assert css_files, "expected built CSS under frontend/dist/assets"
        assert js_files, "expected built JS under frontend/dist/assets"

        css = client.get(f"/assets/{css_files[0].name}")
        assert css.status_code == 200
        assert "text/css" in css.headers.get("content-type", "")

        js = client.get(f"/assets/{js_files[0].name}")
        assert js.status_code == 200
        return

    # Legacy static HTML mode
    css = client.get("/static/css/app.css")
    assert css.status_code == 200
    assert b"--accent" in css.content

    js = client.get("/static/js/api.js")
    assert js.status_code == 200
    assert b"requireAuth" in js.content


def test_static_files_exist():
    expected = [
        "login.html",
        "patient.html",
        "workflow.html",
        "staff.html",
        "escalation.html",
        "admin.html",
        "css/app.css",
        "js/api.js",
    ]
    for name in expected:
        assert (STATIC / name).is_file(), name
    assert STATIC_DIR.is_dir()

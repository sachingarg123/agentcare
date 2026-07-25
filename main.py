"""AgentCare FastAPI entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.admin import router as admin_router
from api.patient import router as patient_router
from api.staff import router as staff_router
from api.ws import router as ws_router
from auth.router import router as auth_router
from core.bootstrap import bootstrap_database
from core.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
SPA_INDEX = FRONTEND_DIST / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure SQLite schema + seed demo data if empty (Phase 5.1)."""
    bootstrap_database()
    yield


app = FastAPI(
    title="PulseDesk",
    description="Agentic healthcare administration API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(patient_router, prefix="/api/v1")
app.include_router(staff_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")


def _legacy_page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name)


@app.get("/health")
def health():
    """Liveness probe — proves the API process is up."""
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name}


# Prefer React SPA when built; fall back to static HTML without frontend/dist.
if SPA_INDEX.is_file():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="spa-assets")

    @app.get("/")
    def spa_root():
        return FileResponse(SPA_INDEX)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """Client-side routes for the React SPA (API/docs registered above)."""
        reserved = ("api/", "docs", "redoc", "openapi.json", "health")
        if full_path.startswith(reserved) or full_path in reserved:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(SPA_INDEX)

else:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def login_page():
        return _legacy_page("login.html")

    @app.get("/patient")
    def patient_page():
        return _legacy_page("patient.html")

    @app.get("/patient/workflows/{workflow_id}")
    def workflow_page(workflow_id: str):
        return _legacy_page("workflow.html")

    @app.get("/staff")
    def staff_page():
        return _legacy_page("staff.html")

    @app.get("/staff/escalations/{escalation_id}")
    def escalation_page(escalation_id: str):
        return _legacy_page("escalation.html")

    @app.get("/staff/admin")
    def admin_page():
        return _legacy_page("admin.html")


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()

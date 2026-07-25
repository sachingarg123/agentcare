"""API route modules (Phase 5)."""

from api.admin import router as admin_router
from api.patient import router as patient_router
from api.staff import router as staff_router
from api.ws import router as ws_router

__all__ = ["admin_router", "patient_router", "staff_router", "ws_router"]

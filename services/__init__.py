"""Application services (email, workflow orchestration)."""

from services.email_service import send_email
from services.email_templates import render_email

__all__ = [
    "send_email",
    "render_email",
    "start_workflow",
    "resume_workflow",
]


def __getattr__(name: str):
    """Lazy export workflow helpers to avoid circular imports with tools/pipeline."""
    if name in ("start_workflow", "resume_workflow"):
        from services.workflow_service import resume_workflow, start_workflow

        return start_workflow if name == "start_workflow" else resume_workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

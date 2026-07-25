"""App bootstrap — schema + seed-if-empty (Phase 5.1)."""

from __future__ import annotations

import logging

from core.config import get_settings
from db.models import Base

logger = logging.getLogger("agentcare.bootstrap")


def bootstrap_database() -> dict[str, bool]:
    """
    Ensure data dirs, create tables if missing, seed demo data if empty.

    Safe to call on every startup (idempotent). Returns what ran.
    """
    settings = get_settings()
    settings.ensure_data_dirs()

    # Import engine at call time so tests can rebind db.session.engine
    from db import session as db_session

    Base.metadata.create_all(bind=db_session.engine)
    logger.info("Database schema ensured (create_all)")

    from scripts.seed_data import seed_if_empty

    seeded = seed_if_empty()
    if seeded:
        logger.info("Demo seed data loaded (database was empty)")
    else:
        logger.info("Demo seed skipped (already present)")

    return {"schema_ready": True, "seeded": seeded}

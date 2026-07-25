"""JWT create / decode — signed with Settings.secret_key (HS256)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from core.config import get_settings


class TokenError(Exception):
    """Raised when a token is missing, expired, or invalid."""


def create_access_token(
    *,
    user_id: str,
    role: str,
    expires_minutes: int | None = None,
) -> str:
    """Build a signed JWT. Claims: sub=user_id, role, exp, iat."""
    settings = get_settings()
    expire_mins = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expire_mins),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Return claims or raise TokenError."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise TokenError("Invalid or expired token") from exc

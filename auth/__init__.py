"""Auth package — JWT, passwords, RBAC dependencies, ownership (Phase 1.5)."""

from auth.dependencies import get_current_user, require_role
from auth.jwt import create_access_token, decode_access_token
from auth.passwords import hash_password, verify_password

__all__ = [
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "hash_password",
    "require_role",
    "verify_password",
]

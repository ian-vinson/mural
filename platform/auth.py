# platform/auth.py — JWT authentication helpers
# MIT License

from __future__ import annotations
import os
from typing import Any

from jose import JWTError, jwt

_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
_ALGORITHM = "HS256"


def create_token(user_id: str) -> str:
    """Return a signed JWT for *user_id*."""
    return jwt.encode({"sub": user_id}, _SECRET, algorithm=_ALGORITHM)


def verify_token(token: str) -> str | None:
    """Return the user_id encoded in *token*, or ``None`` if invalid."""
    try:
        payload: dict[str, Any] = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

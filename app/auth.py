import hashlib
import os
import secrets
import hmac
from typing import Optional
from fastapi import Request, HTTPException, status
from app.database import get_db, get_setting

# In-memory session store: session_token -> username
SESSION_STORE = {}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return f"{salt}${key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, key_hex = stored_hash.split("$")
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
        )
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    SESSION_STORE[token] = username
    return token


def remove_session(token: str):
    SESSION_STORE.pop(token, None)


def get_current_user(request: Request) -> Optional[str]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    return SESSION_STORE.get(token)


def is_authenticated(request: Request) -> bool:
    return get_current_user(request) is not None


def check_access(request: Request, require_write: bool = False) -> Optional[str]:
    """
    Check access permission based on auth_mode setting.
    If require_write is True, always requires authentication.
    If auth_mode is 'require_login', always requires authentication.
    If auth_mode is 'readonly_public' and require_write is False, allows guest.
    Returns username if logged in, or None if guest (when allowed).
    Raises HTTPException(401) if not authorized.
    """
    user = get_current_user(request)
    auth_mode = get_setting("auth_mode", "readonly_public")

    if require_write:
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for write access",
            )
        return user

    if auth_mode == "require_login":
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        return user

    # auth_mode == "readonly_public" and read request -> allow
    return user

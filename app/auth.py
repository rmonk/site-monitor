import hashlib
import os
import secrets
import hmac
import time
import json
import logging
from typing import Optional, Dict, Any, Tuple, List
from fastapi import Request, HTTPException, status
from app.database import (
    get_db,
    get_setting,
    get_user_by_username,
    get_user_by_id,
    get_user_passkeys,
    get_passkey_by_credential_id,
    update_passkey_usage,
)

import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
    AttestationConveyancePreference,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes

logger = logging.getLogger("site_monitor.auth")

# In-memory session store: session_token -> username
SESSION_STORE: Dict[str, str] = {}

# In-memory WebAuthn challenge store: challenge_id -> { challenge: bytes, user_id: Optional[int], created_at: float }
CHALLENGE_STORE: Dict[str, Dict[str, Any]] = {}
CHALLENGE_TTL_SECONDS = 300  # 5 minutes


def cleanup_expired_challenges():
    """Removes challenges older than CHALLENGE_TTL_SECONDS."""
    now = time.time()
    expired = [
        k
        for k, v in CHALLENGE_STORE.items()
        if now - v.get("created_at", 0) > CHALLENGE_TTL_SECONDS
    ]
    for k in expired:
        CHALLENGE_STORE.pop(k, None)


def store_challenge(challenge_bytes: bytes, user_id: Optional[int] = None) -> str:
    """Stores a challenge in the in-memory challenge store and returns its ID."""
    cleanup_expired_challenges()
    challenge_id = secrets.token_urlsafe(32)
    CHALLENGE_STORE[challenge_id] = {
        "challenge": challenge_bytes,
        "user_id": user_id,
        "created_at": time.time(),
    }
    return challenge_id


def pop_challenge(challenge_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves and deletes a challenge by ID."""
    cleanup_expired_challenges()
    return CHALLENGE_STORE.pop(challenge_id, None)


def get_rp_id(request: Request) -> str:
    """Extracts the Relying Party ID (hostname) from request headers or environment."""
    custom_rp_id = os.environ.get("WEBAUTHN_RP_ID")
    if custom_rp_id:
        return custom_rp_id.strip()
    host_header = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.hostname
        or "localhost"
    )
    # Strip port if present
    return host_header.split(":")[0].strip()


def get_origin(request: Request) -> str:
    """Extracts the full origin (scheme://host[:port]) from request headers or environment."""
    custom_origin = os.environ.get("WEBAUTHN_ORIGIN")
    if custom_origin:
        return custom_origin.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or (
            f"{request.url.hostname}:{request.url.port}"
            if request.url.port
            else request.url.hostname
        )
        or "localhost"
    )
    return f"{proto}://{host}".rstrip("/")


def generate_passkey_registration_options(
    user_id: int, username: str, rp_id: str, rp_name: str = "Site Monitor"
) -> Tuple[str, str]:
    """Generates WebAuthn registration options and stores challenge in store.

    Returns (options_json_string, challenge_id).
    """
    existing_passkeys = get_user_passkeys(user_id)
    exclude_credentials = []
    for pk in existing_passkeys:
        try:
            cred_id_bytes = base64url_to_bytes(pk["credential_id"])
            exclude_credentials.append(PublicKeyCredentialDescriptor(id=cred_id_bytes))
        except Exception:
            pass

    user_handle = str(user_id).encode("utf-8")
    options = webauthn.generate_registration_options(
        rp_name=rp_name,
        rp_id=rp_id,
        user_id=user_handle,
        user_name=username,
        user_display_name=username,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude_credentials if exclude_credentials else None,
    )
    challenge_id = store_challenge(options.challenge, user_id=user_id)
    return webauthn.options_to_json(options), challenge_id


def verify_passkey_registration(
    registration_json: str,
    challenge_id: str,
    rp_id: str,
    origin: str,
    expected_user_id: int,
) -> Dict[str, Any]:
    """Verifies client WebAuthn registration response.

    Returns credential metadata dict.
    """
    challenge_data = pop_challenge(challenge_id)
    if not challenge_data:
        raise ValueError("Invalid or expired registration challenge. Please try again.")

    if challenge_data.get("user_id") != expected_user_id:
        raise ValueError("Registration challenge user mismatch.")

    expected_challenge = challenge_data["challenge"]
    verification = webauthn.verify_registration_response(
        credential=registration_json,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        require_user_verification=False,
    )

    credential_id_str = bytes_to_base64url(verification.credential_id)
    public_key_str = bytes_to_base64url(verification.credential_public_key)
    aaguid_str = str(verification.aaguid) if verification.aaguid else None

    return {
        "credential_id": credential_id_str,
        "public_key": public_key_str,
        "sign_count": verification.sign_count,
        "aaguid": aaguid_str,
    }


def generate_passkey_authentication_options(
    rp_id: str, username: Optional[str] = None
) -> Tuple[str, str]:
    """Generates WebAuthn authentication options.

    If username is provided, restricts allow_credentials to that user. Otherwise
    allows discoverable credentials (usernameless login).
    """
    allow_credentials = []
    if username:
        user = get_user_by_username(username)
        if user:
            passkeys = get_user_passkeys(user["id"])
            for pk in passkeys:
                try:
                    cred_id_bytes = base64url_to_bytes(pk["credential_id"])
                    allow_credentials.append(
                        PublicKeyCredentialDescriptor(id=cred_id_bytes)
                    )
                except Exception:
                    pass

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=allow_credentials if allow_credentials else None,
    )
    challenge_id = store_challenge(options.challenge)
    return webauthn.options_to_json(options), challenge_id


def verify_passkey_authentication(
    authentication_json: str, challenge_id: str, rp_id: str, origin: str
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Verifies client WebAuthn authentication response.

    Returns (success, username, error_message).
    """
    challenge_data = pop_challenge(challenge_id)
    if not challenge_data:
        return False, None, "Invalid or expired login challenge. Please try again."

    expected_challenge = challenge_data["challenge"]

    try:
        cred_obj = (
            json.loads(authentication_json)
            if isinstance(authentication_json, str)
            else authentication_json
        )
        cred_id_raw = cred_obj.get("id") or cred_obj.get("rawId")
    except Exception:
        return False, None, "Malformed authentication payload."

    if not cred_id_raw:
        return False, None, "Missing credential ID in authentication assertion."

    passkey = get_passkey_by_credential_id(cred_id_raw)
    if not passkey:
        return (
            False,
            None,
            "Unrecognized passkey. Please sign in with password or use a registered passkey.",
        )

    user = get_user_by_id(passkey["user_id"])
    if not user:
        return False, None, "User account associated with this passkey was not found."

    public_key_bytes = base64url_to_bytes(passkey["public_key"])
    try:
        verification = webauthn.verify_authentication_response(
            credential=authentication_json,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=public_key_bytes,
            credential_current_sign_count=passkey["sign_count"],
            require_user_verification=False,
        )
    except Exception as exc:
        logger.warning(f"Passkey authentication verification failed: {exc}")
        return False, None, "Passkey authentication verification failed."

    update_passkey_usage(passkey["credential_id"], verification.new_sign_count)
    return True, user["username"], None


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
    """Check access permission based on auth_mode setting.

    If require_write is True, always requires authentication. If auth_mode is
    'require_login', always requires authentication. If auth_mode is
    'readonly_public' and require_write is False, allows guest. Returns username
    if logged in, or None if guest (when allowed). Raises HTTPException(401) if
    not authorized.
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

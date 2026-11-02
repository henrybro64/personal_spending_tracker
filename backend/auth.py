import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Request, Response, status

from backend.database import UserRecord, get_user_for_session, record_audit_event


SESSION_COOKIE_NAME = "pst_session"
PASSWORD_MIN_LENGTH = 8
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 80
_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._@-]*$")


@dataclass(frozen=True)
class AuditRequestContext:
    request_id: str | None
    ip_address: str | None
    user_agent: str | None


def validate_username(username: str) -> str:
    normalized = username.strip().lower()
    if len(normalized) < USERNAME_MIN_LENGTH or len(normalized) > USERNAME_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Username must be {USERNAME_MIN_LENGTH}-"
                f"{USERNAME_MAX_LENGTH} characters."
            ),
        )
    if not _USERNAME_PATTERN.match(normalized):
        raise HTTPException(
            status_code=400,
            detail="Username may only use letters, numbers, and . _ - @",
        )
    return normalized


def validate_email(email: str) -> str:
    """Backward-compatible alias for login identifier validation."""
    return validate_username(email)


def validate_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
            ),
        )
    return password


def display_name_from_username(username: str) -> str:
    local_part = username.split("@", 1)[0].strip()
    sanitized = local_part.replace(".", " ").replace("_", " ").replace("-", " ")
    candidate = " ".join(part for part in sanitized.split() if part)
    return candidate[:80] if candidate else username[:80]


def display_name_from_email(email: str) -> str:
    """Backward-compatible alias."""
    return display_name_from_username(email)


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8")


def _urlsafe_b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return (
        f"scrypt$16384$8$1${_urlsafe_b64encode(salt)}"
        f"${_urlsafe_b64encode(derived)}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, hash_text = encoded_hash.split(
            "$"
        )
    except ValueError:
        return False

    if algorithm != "scrypt":
        return False

    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_urlsafe_b64decode(salt_text),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
        )
    except ValueError:
        return False

    expected = _urlsafe_b64decode(hash_text)
    return secrets.compare_digest(derived, expected)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> datetime:
    ttl_days = int(os.getenv("SESSION_TTL_DAYS", "30"))
    return datetime.now(timezone.utc) + timedelta(days=ttl_days)


def _cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def attach_session_cookie(response: Response, token: str) -> None:
    ttl_seconds = int((session_expiry() - datetime.now(timezone.utc)).total_seconds())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=ttl_seconds,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def attach_oidc_binding_cookie(
    response: Response, *, name: str, token: str, max_age: int
) -> None:
    """Ties a Microsoft sign-in to the browser that started it (#100).

    SameSite=Lax is still sent on Microsoft's top-level redirect back.
    """
    response.set_cookie(
        key=name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=max_age,
        path="/",
    )


def clear_oidc_binding_cookie(response: Response, *, name: str) -> None:
    response.delete_cookie(
        key=name, path="/", httponly=True, samesite="lax", secure=_cookie_secure()
    )


def get_audit_request_context(request: Request) -> AuditRequestContext:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    ip_address = (
        forwarded_for.split(",", 1)[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )
    return AuditRequestContext(
        request_id=getattr(request.state, "request_id", None),
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )


def get_optional_current_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> UserRecord | None:
    if not session_token:
        return None

    current_user = get_user_for_session(hash_session_token(session_token))
    if current_user:
        request.state.current_user = current_user
    return current_user


def require_current_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> UserRecord:
    current_user = get_optional_current_user(request, session_token)
    if current_user:
        return current_user

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="authorization_failure",
        status="rejected",
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"path": request.url.path, "method": request.method},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
    )

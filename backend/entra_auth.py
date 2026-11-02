"""Microsoft Entra ID sign-in (#100): OIDC authorization code flow with PKCE.

Adapted from Stock Ticker's ``entra_auth_service`` (#240, #299). Differences:

- The login transaction (state, nonce, PKCE verifier) is stored server-side in
  ``oidc_login_states`` and bound to the browser by a random HttpOnly cookie,
  so no extra signing secret is needed.
- Only Entra object ids listed in ``ENTRA_ALLOWED_OBJECT_IDS`` may sign in.
  The first sign-in of an allowed identity creates a new, empty user; it never
  attaches to an existing local user.
- The ID token's tenant must be the configured tenant.

Codes, tokens, the client secret and Microsoft's error descriptions never go
to logs or to the browser. Failures log allowlisted OAuth fields only.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import PyJWKClient

from backend.database import (
    UserRecord,
    consume_oidc_login_state,
    create_user_with_identity,
    get_user_for_identity,
    save_oidc_login_state,
)

logger = logging.getLogger(__name__)

ENTRA_PROVIDER = "entra"
OIDC_BINDING_COOKIE_NAME = "pst_oidc"
LOGIN_STATE_TTL_SECONDS = 600
# Unfinished sign-ins kept at once. Far above real use by a few people.
MAX_PENDING_LOGIN_STATES = 10_000
CALLBACK_PATH_SUFFIX = "/auth/entra/callback"
_SCOPE = "openid profile email"
_METADATA_TTL_SECONDS = 600
# Clock skew allowed when checking exp/iat/nbf.
_TOKEN_LEEWAY_SECONDS = 60
_HTTP_TIMEOUT_SECONDS = 15.0

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# Maps each allowed OAuth error to itself, so the logged value is always one
# of these strings and never the text from the request.
_OAUTH_ERROR_ALLOWLIST = {
    code: code
    for code in (
        "access_denied",
        "consent_required",
        "interaction_required",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "login_required",
        "server_error",
        "temporarily_unavailable",
        "unauthorized_client",
        "unsupported_grant_type",
    )
}
_AADSTS_RE = re.compile(r"AADSTS(\d{4,10})", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

# Error codes passed to the sign-in page as ?auth_error=<code>.
ERROR_NOT_ALLOWED = "entra_not_allowed"
ERROR_SIGN_IN_FAILED = "entra_signin_failed"
ERROR_STATE_INVALID = "entra_state_invalid"
ERROR_UNAVAILABLE = "entra_unavailable"


class EntraSignInError(Exception):
    """A sign-in that must be refused. ``code`` is safe to show the user."""

    def __init__(
        self, code: str, reason: str, *, object_id: str | None = None
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.object_id = object_id


@dataclass(frozen=True)
class EntraSettings:
    enabled: bool
    tenant_id: str
    client_id: str
    client_secret: str
    authority: str
    redirect_uri: str
    post_logout_redirect_uri: str
    allowed_object_ids: frozenset[str]
    allowed_object_ids_raw: tuple[str, ...]


@dataclass(frozen=True)
class EntraLoginStart:
    authorization_url: str
    browser_binding_token: str


@dataclass(frozen=True)
class EntraSignIn:
    user: UserRecord
    return_to: str
    created: bool


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_flag(name: str, *, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load_entra_settings() -> EntraSettings:
    raw_ids = tuple(
        item.strip().lower()
        for item in _env("ENTRA_ALLOWED_OBJECT_IDS").split(",")
        if item.strip()
    )
    return EntraSettings(
        enabled=_env_flag("ENTRA_ENABLED", default=False),
        tenant_id=_env("ENTRA_TENANT_ID").lower(),
        client_id=_env("ENTRA_CLIENT_ID").lower(),
        client_secret=_env("ENTRA_CLIENT_SECRET"),
        authority=_env("ENTRA_AUTHORITY").rstrip("/"),
        redirect_uri=_env("ENTRA_REDIRECT_URI"),
        post_logout_redirect_uri=_env("ENTRA_POST_LOGOUT_REDIRECT_URI"),
        allowed_object_ids=frozenset(
            item for item in raw_ids if _GUID_RE.match(item)
        ),
        allowed_object_ids_raw=raw_ids,
    )


def local_registration_enabled() -> bool:
    return _env_flag("LOCAL_REGISTRATION_ENABLED", default=True)


def _is_https_or_local(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {
        host.strip("[]") for host in _LOCAL_HOSTS
    }


def entra_config_problems(settings: EntraSettings) -> list[str]:
    """Why Entra sign-in cannot run. Messages never contain secret values."""
    if not settings.enabled:
        return []
    problems: list[str] = []
    for name, value in (
        ("ENTRA_TENANT_ID", settings.tenant_id),
        ("ENTRA_CLIENT_ID", settings.client_id),
    ):
        if not value:
            problems.append(f"{name} is required.")
        elif not _GUID_RE.match(value):
            problems.append(f"{name} must be a GUID.")
    if not settings.client_secret:
        problems.append(
            "ENTRA_CLIENT_SECRET is empty; set it in the spending-tracker-entra Secret."
        )
    authority = urlparse(settings.authority)
    if not settings.authority:
        problems.append("ENTRA_AUTHORITY is required.")
    elif authority.scheme != "https" or not authority.netloc or authority.query:
        problems.append("ENTRA_AUTHORITY must be an https URL without a query.")
    elif settings.tenant_id and settings.tenant_id not in settings.authority.lower():
        problems.append("ENTRA_AUTHORITY must contain ENTRA_TENANT_ID.")
    if not settings.redirect_uri:
        problems.append("ENTRA_REDIRECT_URI is required.")
    elif not _is_https_or_local(settings.redirect_uri):
        problems.append("ENTRA_REDIRECT_URI must use https (http only for localhost).")
    elif not urlparse(settings.redirect_uri).path.endswith(CALLBACK_PATH_SUFFIX):
        problems.append(f"ENTRA_REDIRECT_URI must end with {CALLBACK_PATH_SUFFIX}.")
    if settings.post_logout_redirect_uri and not _is_https_or_local(
        settings.post_logout_redirect_uri
    ):
        problems.append(
            "ENTRA_POST_LOGOUT_REDIRECT_URI must use https (http only for localhost)."
        )
    invalid_ids = len(settings.allowed_object_ids_raw) - len(
        settings.allowed_object_ids
    )
    if invalid_ids:
        problems.append(
            f"ENTRA_ALLOWED_OBJECT_IDS has {invalid_ids} entr"
            f"{'y' if invalid_ids == 1 else 'ies'} that are not GUIDs."
        )
    return problems


def entra_is_configured(settings: EntraSettings | None = None) -> bool:
    resolved = settings or load_entra_settings()
    return resolved.enabled and not entra_config_problems(resolved)


def log_entra_startup_status() -> None:
    """One startup line saying whether Microsoft sign-in is on, and why not."""
    settings = load_entra_settings()
    if not settings.enabled:
        logger.info("entra sign-in disabled (ENTRA_ENABLED is not true)")
        return
    problems = entra_config_problems(settings)
    if problems:
        # Local sign-in keeps working; only Microsoft sign-in is off.
        logger.error(
            "entra sign-in disabled: invalid configuration: %s", " ".join(problems)
        )
        return
    logger.info(
        "entra sign-in enabled client_id=%s redirect_host=%s allowed_identities=%s",
        settings.client_id,
        urlparse(settings.redirect_uri).netloc,
        len(settings.allowed_object_ids),
    )
    if not settings.allowed_object_ids:
        logger.warning(
            "entra sign-in enabled but ENTRA_ALLOWED_OBJECT_IDS is empty; "
            "every Microsoft sign-in will be refused"
        )


def safe_return_to(value: str | None) -> str:
    """Only a same-origin path is allowed after sign-in; anything else is "/"."""
    if not value or len(value) > 512:
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return "/"
    return value


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# --- OIDC metadata and signing keys -----------------------------------------

HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]

_cache_lock = threading.Lock()
_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_clients: dict[str, PyJWKClient] = {}


def clear_caches() -> None:
    with _cache_lock:
        _metadata_cache.clear()
        _jwks_clients.clear()


def fetch_oidc_metadata(
    settings: EntraSettings, *, http_get: HttpGet | None = None
) -> dict[str, Any]:
    url = f"{settings.authority}/.well-known/openid-configuration"
    now = time.monotonic()
    with _cache_lock:
        cached = _metadata_cache.get(url)
        if cached and now - cached[0] < _METADATA_TTL_SECONDS:
            return cached[1]
    getter = http_get or httpx.get
    response = getter(url, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("OIDC metadata is not a JSON object")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.startswith("https://"):
            raise ValueError(f"OIDC metadata has no https {key}")
    # A misconfigured authority must not let another tenant's tokens in.
    if settings.tenant_id not in str(payload["issuer"]).lower():
        raise ValueError("OIDC issuer does not name the configured tenant")
    with _cache_lock:
        _metadata_cache[url] = (now, payload)
    return payload


def _jwks_client(jwks_uri: str) -> PyJWKClient:
    with _cache_lock:
        client = _jwks_clients.get(jwks_uri)
        if client is None:
            client = PyJWKClient(jwks_uri, cache_keys=True)
            _jwks_clients[jwks_uri] = client
        return client


# --- Diagnostics (allowlisted fields only) ----------------------------------


def allowlisted_oauth_error(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    return _OAUTH_ERROR_ALLOWLIST.get(raw.strip().lower())


def extract_aadsts_code(*candidates: object) -> str | None:
    """``AADSTS<digits>`` from numeric codes or text; never the description."""
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int) and candidate > 0:
            return f"AADSTS{candidate}"
        if isinstance(candidate, list):
            found = extract_aadsts_code(*candidate)
            if found:
                return found
        if isinstance(candidate, str):
            match = _AADSTS_RE.search(candidate)
            if match:
                # Rebuilt from the number, so no request text reaches the log.
                return f"AADSTS{int(match.group(1))}"
    return None


def _safe_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()[:64]
    return cleaned if _SAFE_ID_RE.match(cleaned) else None


def _log_failure(event: str, **fields: object) -> None:
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.warning("entra %s %s", event, rendered)


def log_database_unavailable(exc: BaseException) -> None:
    _log_failure("database_unavailable", error_type=type(exc).__name__)


def log_authorize_error(*, error: object, error_description: object) -> None:
    """Microsoft redirected back with ?error=. The description is not logged."""
    description = error_description if isinstance(error_description, str) else ""
    _log_failure(
        "authorize_error",
        oauth_error=allowlisted_oauth_error(error),
        aadsts_code=extract_aadsts_code(description),
    )


def _token_error_fields(response: Any) -> dict[str, object]:
    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:  # noqa: BLE001 - a malformed body is itself the diagnostic
        payload = {}
    description = payload.get("error_description")
    return {
        "http_status": getattr(response, "status_code", None),
        "oauth_error": allowlisted_oauth_error(payload.get("error"))
        or ("malformed_response" if not payload else None),
        "aadsts_code": extract_aadsts_code(
            payload.get("error_codes"),
            description if isinstance(description, str) else None,
        ),
        "correlation_id": _safe_id(payload.get("correlation_id")),
        "trace_id": _safe_id(payload.get("trace_id")),
    }


# --- Flow ---------------------------------------------------------------------


def begin_login(
    return_to: str | None,
    *,
    settings: EntraSettings | None = None,
    http_get: HttpGet | None = None,
) -> EntraLoginStart:
    resolved = settings or load_entra_settings()
    if not entra_is_configured(resolved):
        raise EntraSignInError(ERROR_UNAVAILABLE, "entra is not configured")
    try:
        metadata = fetch_oidc_metadata(resolved, http_get=http_get)
    except (httpx.HTTPError, ValueError) as exc:
        _log_failure("metadata_unavailable", error_type=type(exc).__name__)
        raise EntraSignInError(ERROR_UNAVAILABLE, "metadata unavailable") from None

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    browser_binding_token = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _pkce_pair()
    stored = save_oidc_login_state(
        state_hash=hash_token(state),
        browser_binding_hash=hash_token(browser_binding_token),
        nonce=nonce,
        code_verifier=code_verifier,
        return_to=safe_return_to(return_to),
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=LOGIN_STATE_TTL_SECONDS),
        max_pending=MAX_PENDING_LOGIN_STATES,
    )
    if not stored:
        _log_failure("login_state_limit", max_pending=MAX_PENDING_LOGIN_STATES)
        raise EntraSignInError(ERROR_UNAVAILABLE, "too many pending sign-ins")
    params = {
        "client_id": resolved.client_id,
        "response_type": "code",
        "redirect_uri": resolved.redirect_uri,
        "response_mode": "query",
        "scope": _SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return EntraLoginStart(
        authorization_url=f"{metadata['authorization_endpoint']}?{urlencode(params)}",
        browser_binding_token=browser_binding_token,
    )


def _exchange_code(
    settings: EntraSettings,
    metadata: dict[str, Any],
    *,
    code: str,
    code_verifier: str,
    http_post: HttpPost | None,
) -> str:
    poster = http_post or httpx.post
    try:
        response = poster(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "code": code,
                "redirect_uri": settings.redirect_uri,
                "code_verifier": code_verifier,
                "scope": _SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        _log_failure("token_exchange_failed", error_type=type(exc).__name__)
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "token request failed") from None

    if response.status_code >= 400:
        _log_failure(
            "token_exchange_failed",
            **_token_error_fields(response),
            client_id=settings.client_id,
            redirect_host=urlparse(settings.redirect_uri).netloc,
        )
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "token endpoint refused")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    id_token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(id_token, str) or not id_token:
        _log_failure("token_exchange_failed", oauth_error="missing_id_token")
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "no id_token")
    return id_token


def _validated_claims(
    settings: EntraSettings,
    metadata: dict[str, Any],
    id_token: str,
    *,
    expected_nonce: str,
    jwks_client_factory: Callable[[str], Any] | None,
) -> dict[str, Any]:
    factory = jwks_client_factory or _jwks_client
    try:
        signing_key = factory(metadata["jwks_uri"]).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.client_id,
            issuer=metadata["issuer"],
            leeway=_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss", "nonce"]},
        )
    except jwt.PyJWTError as exc:
        _log_failure("id_token_rejected", error_type=type(exc).__name__)
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "invalid id_token") from None

    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
        _log_failure("id_token_rejected", error_type="NonceMismatch")
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "nonce mismatch")
    tenant = str(claims.get("tid") or "").lower()
    if tenant != settings.tenant_id:
        _log_failure("id_token_rejected", error_type="TenantMismatch")
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "tenant mismatch")
    object_id = str(claims.get("oid") or "").lower()
    if not _GUID_RE.match(object_id):
        _log_failure("id_token_rejected", error_type="MissingObjectId")
        raise EntraSignInError(ERROR_SIGN_IN_FAILED, "no oid claim")
    claims["oid"] = object_id
    return claims


def _username_base(claims: dict[str, Any], object_id: str) -> str:
    """A login name shaped like local usernames; it never matches a local login."""
    raw = str(claims.get("preferred_username") or claims.get("email") or "")
    cleaned = "".join(
        char
        for char in raw.strip().lower()
        if char.isascii() and (char.isalnum() or char in "._@-")
    ).lstrip("._@-")
    if len(cleaned) < 3:
        cleaned = f"entra-{object_id[:8]}"
    return cleaned[:80]


def _display_name(claims: dict[str, Any], username: str) -> str:
    name = " ".join(str(claims.get("name") or "").split())
    return (name or username.split("@", 1)[0])[:80]


def complete_login(
    *,
    code: str | None,
    state: str | None,
    browser_binding_token: str | None,
    settings: EntraSettings | None = None,
    http_get: HttpGet | None = None,
    http_post: HttpPost | None = None,
    jwks_client_factory: Callable[[str], Any] | None = None,
) -> EntraSignIn:
    resolved = settings or load_entra_settings()
    if not entra_is_configured(resolved):
        raise EntraSignInError(ERROR_UNAVAILABLE, "entra is not configured")
    if not state or not code:
        raise EntraSignInError(ERROR_STATE_INVALID, "missing code or state")

    # Consumed before any other check, so a state can never be replayed.
    login_state = consume_oidc_login_state(hash_token(state))
    if login_state is None:
        raise EntraSignInError(ERROR_STATE_INVALID, "unknown, used or expired state")
    if not browser_binding_token or not secrets.compare_digest(
        hash_token(browser_binding_token), login_state["browser_binding_hash"]
    ):
        raise EntraSignInError(ERROR_STATE_INVALID, "state not started by this browser")

    try:
        metadata = fetch_oidc_metadata(resolved, http_get=http_get)
    except (httpx.HTTPError, ValueError) as exc:
        _log_failure("metadata_unavailable", error_type=type(exc).__name__)
        raise EntraSignInError(ERROR_UNAVAILABLE, "metadata unavailable") from None

    id_token = _exchange_code(
        resolved,
        metadata,
        code=code,
        code_verifier=login_state["code_verifier"],
        http_post=http_post,
    )
    claims = _validated_claims(
        resolved,
        metadata,
        id_token,
        expected_nonce=login_state["nonce"],
        jwks_client_factory=jwks_client_factory,
    )
    object_id = claims["oid"]
    # Checked on every sign-in, so removing an id revokes Microsoft sign-in.
    if object_id not in resolved.allowed_object_ids:
        raise EntraSignInError(
            ERROR_NOT_ALLOWED, "object id not allowed", object_id=object_id
        )

    issuer = str(metadata["issuer"])
    subject = str(claims["sub"])
    user = get_user_for_identity(
        provider=ENTRA_PROVIDER, issuer=issuer, subject=subject
    )
    created = False
    if user is None:
        username = _username_base(claims, object_id)
        user = create_user_with_identity(
            username_base=username,
            display_name=_display_name(claims, username),
            provider=ENTRA_PROVIDER,
            issuer=issuer,
            subject=subject,
            tenant_id=resolved.tenant_id,
            object_id=object_id,
        )
        created = True
    return EntraSignIn(user=user, return_to=login_state["return_to"], created=created)


def end_session_url(
    *,
    settings: EntraSettings | None = None,
    http_get: HttpGet | None = None,
) -> str | None:
    """Microsoft sign-out URL, or None when it is not configured or unreachable."""
    resolved = settings or load_entra_settings()
    if not entra_is_configured(resolved) or not resolved.post_logout_redirect_uri:
        return None
    try:
        metadata = fetch_oidc_metadata(resolved, http_get=http_get)
    except (httpx.HTTPError, ValueError):
        return None
    endpoint = metadata.get("end_session_endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return None
    query = urlencode(
        {
            "client_id": resolved.client_id,
            "post_logout_redirect_uri": resolved.post_logout_redirect_uri,
        }
    )
    return f"{endpoint}?{query}"

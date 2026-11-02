"""Microsoft Entra sign-in (#100), against a fake identity provider.

Tokens are real RS256 JWTs signed by a test key, so PyJWT's signature,
audience, issuer and expiry checks run for real.
"""

import base64
import hashlib
import logging
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import backend.entra_auth as entra_auth

TENANT = "4661e227-6bc3-4ff3-b9c2-df41f6420014"
OTHER_TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "0b6e1f0a-9d1c-4a7e-8f00-2c4d6e8a1b3c"
# Random per run: a fake, never a literal that looks like a real credential.
CLIENT_SECRET = secrets.token_urlsafe(24)
ALLOWED_OID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER_OID = "bbbbbbbb-1111-2222-3333-444444444444"
AUTHORITY = f"https://spending.ciamlogin.com/{TENANT}/v2.0"
ISSUER = f"https://{TENANT}.ciamlogin.com/{TENANT}/v2.0"
REDIRECT_URI = "https://spending.example.com/api/auth/entra/callback"
POST_LOGOUT_URI = "https://spending.example.com/"

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeIdentityProvider:
    def __init__(self) -> None:
        self.metadata = {
            "issuer": ISSUER,
            "authorization_endpoint": "https://login.example.test/authorize",
            "token_endpoint": "https://login.example.test/token",
            "jwks_uri": "https://login.example.test/keys",
            "end_session_endpoint": "https://login.example.test/logout",
        }
        self.claim_overrides: dict[str, object] = {}
        self.signing_key = _SIGNING_KEY
        self.token_status = 200
        self.token_body: dict[str, object] | None = None
        self.token_requests: list[dict[str, str]] = []
        self.expected_nonce: str | None = None

    def get(self, url: str, **_kwargs: object) -> httpx.Response:
        assert url == f"{AUTHORITY}/.well-known/openid-configuration"
        return httpx.Response(200, json=self.metadata, request=httpx.Request("GET", url))

    def post(self, url: str, *, data: dict[str, str], **_kwargs: object) -> httpx.Response:
        assert url == self.metadata["token_endpoint"]
        self.token_requests.append(dict(data))
        request = httpx.Request("POST", url)
        if self.token_status != 200:
            return httpx.Response(self.token_status, json=self.token_body, request=request)
        return httpx.Response(200, json={"id_token": self.id_token()}, request=request)

    def id_token(self) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "subject-for-allowed-user",
            "oid": ALLOWED_OID,
            "tid": TENANT,
            "nonce": self.expected_nonce,
            "iat": now,
            "exp": now + 3600,
            "name": "Henry Example",
            "preferred_username": "henry@example.com",
        }
        claims.update(self.claim_overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, self.signing_key, algorithm="RS256", headers={"kid": "k1"})

    def jwks_client(self, jwks_uri: str) -> SimpleNamespace:
        assert jwks_uri == self.metadata["jwks_uri"]
        return SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(
                key=_SIGNING_KEY.public_key()
            )
        )


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> FakeIdentityProvider:
    provider = FakeIdentityProvider()
    entra_auth.clear_caches()
    monkeypatch.setattr(httpx, "get", provider.get)
    monkeypatch.setattr(httpx, "post", provider.post)
    monkeypatch.setattr(entra_auth, "_jwks_client", provider.jwks_client)
    yield provider
    entra_auth.clear_caches()


@pytest.fixture
def entra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "ENTRA_ENABLED": "true",
        "ENTRA_TENANT_ID": TENANT,
        "ENTRA_CLIENT_ID": CLIENT_ID,
        "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
        "ENTRA_AUTHORITY": AUTHORITY,
        "ENTRA_REDIRECT_URI": REDIRECT_URI,
        "ENTRA_POST_LOGOUT_REDIRECT_URI": POST_LOGOUT_URI,
        # Upper case and spaces are normalised.
        "ENTRA_ALLOWED_OBJECT_IDS": f" {ALLOWED_OID.upper()} ",
    }.items():
        monkeypatch.setenv(name, value)


def _start_login(
    client: TestClient, idp: FakeIdentityProvider, *, return_to: str = "/transactions"
) -> dict[str, str]:
    response = client.get(
        "/auth/entra/login", params={"return_to": return_to}, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        idp.metadata["authorization_endpoint"]
    )
    params = {key: values[0] for key, values in parse_qs(location.query).items()}
    idp.expected_nonce = params["nonce"]
    return params


def _callback(client: TestClient, state: str, **extra: str) -> httpx.Response:
    return client.get(
        "/auth/entra/callback",
        params={"code": "auth-code-123", "state": state, **extra},
        follow_redirects=False,
    )


def _sign_in(client: TestClient, idp: FakeIdentityProvider) -> httpx.Response:
    params = _start_login(client, idp)
    response = _callback(client, params["state"])
    assert response.status_code == 302, response.text
    return response


def _error_of(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.path == "/" and not location.netloc
    return {key: values[0] for key, values in parse_qs(location.query).items()}


def _user_count(database_url: str) -> int:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM users")
        return int(cursor.fetchone()[0])


def _register_local(client: TestClient, username: str = "alice@example.com") -> None:
    response = client.post(
        "/auth/register",
        json={"email": username, "password": "password123", "display_name": "Alice"},
    )
    assert response.status_code == 201, response.text


def _add_manual(client: TestClient, merchant: str) -> int:
    response = client.post(
        "/transactions/manual",
        json={"date": "2024-04-20", "merchant": merchant, "amount": 12.5},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["transaction"]["id"])


# --- Provider discovery and configuration -----------------------------------


def test_providers_report_entra_off_by_default(client: TestClient) -> None:
    assert client.get("/auth/providers").json() == {
        "local_registration_enabled": True,
        "entra_enabled": False,
    }
    assert client.get("/auth/entra/login", follow_redirects=False).status_code == 404
    assert client.get("/auth/entra/callback", follow_redirects=False).status_code == 404


def test_providers_report_entra_when_fully_configured(
    client: TestClient, entra_env: None
) -> None:
    assert client.get("/auth/providers").json()["entra_enabled"] is True


def test_incomplete_entra_config_keeps_microsoft_sign_in_off(
    client: TestClient, entra_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "")
    assert client.get("/auth/providers").json()["entra_enabled"] is False
    assert client.get("/auth/entra/login", follow_redirects=False).status_code == 404


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"ENTRA_TENANT_ID": ""}, "ENTRA_TENANT_ID is required."),
        ({"ENTRA_CLIENT_ID": "not-a-guid"}, "ENTRA_CLIENT_ID must be a GUID."),
        ({"ENTRA_CLIENT_SECRET": ""}, "ENTRA_CLIENT_SECRET is empty"),
        ({"ENTRA_AUTHORITY": "http://spending.ciamlogin.com/x"}, "must be an https URL"),
        ({"ENTRA_AUTHORITY": f"https://login.example.com/{OTHER_TENANT}/v2.0"}, "must contain"),
        ({"ENTRA_REDIRECT_URI": "http://spending.example.com/api/auth/entra/callback"}, "must use https"),
        ({"ENTRA_REDIRECT_URI": "https://spending.example.com/api/callback"}, "must end with"),
        ({"ENTRA_POST_LOGOUT_REDIRECT_URI": "http://evil.example.com/"}, "POST_LOGOUT"),
        ({"ENTRA_ALLOWED_OBJECT_IDS": f"{ALLOWED_OID},henry"}, "1 entry that are not GUIDs"),
    ],
)
def test_config_problems_are_reported_without_secrets(
    monkeypatch: pytest.MonkeyPatch, entra_env: None, overrides: dict[str, str], expected: str
) -> None:
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    problems = entra_auth.entra_config_problems(entra_auth.load_entra_settings())
    assert any(expected in problem for problem in problems), problems
    assert not any(CLIENT_SECRET in problem for problem in problems)


def test_localhost_http_redirect_is_allowed_for_development(
    monkeypatch: pytest.MonkeyPatch, entra_env: None
) -> None:
    monkeypatch.setenv("ENTRA_REDIRECT_URI", "http://localhost:3000/api/auth/entra/callback")
    assert entra_auth.entra_config_problems(entra_auth.load_entra_settings()) == []


def test_startup_log_names_problems_but_never_the_secret(
    monkeypatch: pytest.MonkeyPatch, entra_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ENTRA_REDIRECT_URI", "http://spending.example.com/cb")
    with caplog.at_level(logging.INFO, logger="backend.entra_auth"):
        entra_auth.log_entra_startup_status()
    assert "entra sign-in disabled: invalid configuration" in caplog.text
    assert CLIENT_SECRET not in caplog.text


def test_startup_log_warns_when_nobody_is_allowed(
    monkeypatch: pytest.MonkeyPatch, entra_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ENTRA_ALLOWED_OBJECT_IDS", "")
    with caplog.at_level(logging.INFO, logger="backend.entra_auth"):
        entra_auth.log_entra_startup_status()
    assert "every Microsoft sign-in will be refused" in caplog.text
    assert CLIENT_SECRET not in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/transactions?range=month", "/transactions?range=month"),
        ("/", "/"),
        (None, "/"),
        ("//evil.example.com/", "/"),
        ("/\\evil.example.com", "/"),
        ("https://evil.example.com/", "/"),
        ("javascript:alert(1)", "/"),
        ("/ok\r\nSet-Cookie: x=1", "/"),
        ("/" + "a" * 600, "/"),
    ],
)
def test_return_to_only_allows_same_origin_paths(value: str | None, expected: str) -> None:
    assert entra_auth.safe_return_to(value) == expected


# --- Login start --------------------------------------------------------------


def test_login_redirects_with_pkce_state_and_nonce(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    response = client.get(
        "/auth/entra/login", params={"return_to": "/transactions"}, follow_redirects=False
    )
    assert response.status_code == 302
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{entra_auth.OIDC_BINDING_COOKIE_NAME}=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Max-Age=600" in cookie

    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile email"]
    assert len(params["state"][0]) >= 32 and len(params["nonce"][0]) >= 32
    assert params["state"] != params["nonce"]

    # Only a hash of the state is stored, never the state itself.
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT state_hash, return_to FROM oidc_login_states")
        rows = cursor.fetchall()
    assert rows == [(entra_auth.hash_token(params["state"][0]), "/transactions")]


def test_login_with_unreachable_metadata_shows_unavailable(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(url: str, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", down)
    response = client.get("/auth/entra/login", follow_redirects=False)
    assert _error_of(response) == {"auth_error": "entra_unavailable"}


def test_pending_sign_ins_are_capped(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(entra_auth, "MAX_PENDING_LOGIN_STATES", 2)
    _start_login(client, idp)
    _start_login(client, idp)
    with caplog.at_level(logging.WARNING, logger=entra_auth.logger.name):
        refused = client.get("/auth/entra/login", follow_redirects=False)
    assert _error_of(refused) == {"auth_error": "entra_unavailable"}
    assert "entra login_state_limit max_pending=2" in caplog.text

    # Expired states stop counting, so sign-in recovers on its own.
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE oidc_login_states SET expires_at = NOW() - INTERVAL '1 second'")
        connection.commit()
    params = _start_login(client, idp)
    assert _callback(client, params["state"]).headers["location"] == "/transactions"


def test_database_outage_at_login_start_shows_unavailable(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def down(**_kwargs: object) -> bool:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(entra_auth, "save_oidc_login_state", down)
    with caplog.at_level(logging.WARNING, logger=entra_auth.logger.name):
        response = client.get("/auth/entra/login", follow_redirects=False)
    assert _error_of(response) == {"auth_error": "entra_unavailable"}
    assert "entra database_unavailable error_type=OperationalError" in caplog.text
    assert "connection refused" not in caplog.text


def test_metadata_from_another_tenant_is_refused(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    idp.metadata["issuer"] = f"https://login.example.test/{OTHER_TENANT}/v2.0"
    response = client.get("/auth/entra/login", follow_redirects=False)
    assert _error_of(response) == {"auth_error": "entra_unavailable"}


# --- Successful sign-in ---------------------------------------------------------


def test_allowed_identity_signs_in_and_gets_a_new_empty_user(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    params = _start_login(client, idp)
    response = _callback(client, params["state"])

    assert response.status_code == 302
    assert response.headers["location"] == "/transactions"
    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("pst_session=") and "HttpOnly" in c for c in cookies)
    assert any(
        c.startswith(f"{entra_auth.OIDC_BINDING_COOKIE_NAME}=") and "Max-Age=0" in c
        for c in cookies
    )

    # The token request carried the PKCE verifier for this login's challenge.
    (token_request,) = idp.token_requests
    digest = hashlib.sha256(token_request["code_verifier"].encode("ascii")).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]
    assert token_request["client_secret"] == CLIENT_SECRET
    assert token_request["redirect_uri"] == REDIRECT_URI
    assert token_request["code"] == "auth-code-123"

    session = client.get("/auth/session").json()
    assert session["authenticated"] is True
    assert session["user"]["display_name"] == "Henry Example"
    assert session["user"]["email"] == "henry@example.com"
    assert client.get("/transactions").json()["transactions"] == []


def test_second_sign_in_reuses_the_same_user(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    _sign_in(client, idp)
    first_id = client.get("/auth/session").json()["user"]["id"]
    client.post("/auth/logout")
    _sign_in(client, idp)
    assert client.get("/auth/session").json()["user"]["id"] == first_id
    assert _user_count(database_url) == 1


def test_entra_sign_in_never_merges_into_a_local_account(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    # A local user whose username equals the Entra preferred_username.
    _register_local(client, "henry@example.com")
    local_id = client.get("/auth/session").json()["user"]["id"]
    local_tx = _add_manual(client, "Local Only Cafe")
    client.post("/auth/logout")

    _sign_in(client, idp)
    entra_user = client.get("/auth/session").json()["user"]
    assert entra_user["id"] != local_id
    assert entra_user["email"] == "henry@example.com-2"
    # Cross-user isolation: none of the local user's rows are visible or deletable.
    assert client.get("/transactions").json()["transactions"] == []
    assert client.get("/summary").json()["number_of_transactions"] == 0
    assert client.delete(f"/transactions/{local_tx}").status_code == 404
    entra_tx = _add_manual(client, "Entra Only Cafe")
    client.post("/auth/logout")

    # The local account still signs in with its password and sees only its data.
    login = client.post(
        "/auth/login", json={"email": "henry@example.com", "password": "password123"}
    )
    assert login.status_code == 200
    merchants = [row["merchant"] for row in client.get("/transactions").json()["transactions"]]
    assert merchants == ["Local Only Cafe"]
    assert client.delete(f"/transactions/{entra_tx}").status_code == 404
    assert _user_count(database_url) == 2


def test_entra_user_cannot_sign_in_with_a_local_password(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    _sign_in(client, idp)
    client.post("/auth/logout")
    for password in ("password123", "!external-sign-in-only", ""):
        response = client.post(
            "/auth/login", json={"email": "henry@example.com", "password": password}
        )
        assert response.status_code in {400, 401}


def test_entra_users_do_not_claim_legacy_rows(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO transactions (date, merchant, amount, file_name)
            VALUES ('2024-01-01', 'LEGACY ROW', 10, 'legacy.pdf')
            """
        )
        connection.commit()
    _sign_in(client, idp)
    assert client.get("/transactions").json()["transactions"] == []
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT user_id FROM transactions WHERE merchant = 'LEGACY ROW'")
        assert cursor.fetchone() == (None,)


# --- Refused sign-ins -------------------------------------------------------------


def test_identity_not_on_the_allowlist_is_refused_and_shown_its_object_id(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    idp.claim_overrides = {"oid": OTHER_OID, "sub": "stranger"}
    params = _start_login(client, idp)
    response = _callback(client, params["state"])
    assert _error_of(response) == {
        "auth_error": "entra_not_allowed",
        "entra_object_id": OTHER_OID,
    }
    assert not any(c.startswith("pst_session=") for c in response.headers.get_list("set-cookie"))
    assert _user_count(database_url) == 0
    assert client.get("/auth/session").json()["authenticated"] is False


def test_removing_an_id_from_the_allowlist_revokes_microsoft_sign_in(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign_in(client, idp)
    client.post("/auth/logout")
    monkeypatch.setenv("ENTRA_ALLOWED_OBJECT_IDS", OTHER_OID)
    params = _start_login(client, idp)
    assert _error_of(_callback(client, params["state"]))["auth_error"] == "entra_not_allowed"


def test_callback_from_another_browser_is_refused(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, api_main
) -> None:
    """Login CSRF: a callback URL from someone else's login must not sign us in."""
    params = _start_login(client, idp)
    with TestClient(api_main.app) as victim:
        response = _callback(victim, params["state"])
        assert _error_of(response) == {"auth_error": "entra_state_invalid"}
        assert victim.get("/auth/session").json()["authenticated"] is False
    assert idp.token_requests == []
    # The state was consumed, so the attacker's own browser cannot use it either.
    assert _error_of(_callback(client, params["state"]))["auth_error"] == "entra_state_invalid"


def test_a_state_works_only_once(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    params = _start_login(client, idp)
    binding = client.cookies.get(entra_auth.OIDC_BINDING_COOKIE_NAME)
    assert binding
    assert _callback(client, params["state"]).headers["location"] == "/transactions"
    client.post("/auth/logout")
    # Replay the same callback with the original binding cookie restored.
    client.cookies.clear()
    client.cookies.set(entra_auth.OIDC_BINDING_COOKIE_NAME, binding)
    replay = _callback(client, params["state"])
    assert _error_of(replay) == {"auth_error": "entra_state_invalid"}
    assert len(idp.token_requests) == 1


def test_an_expired_state_is_refused(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    params = _start_login(client, idp)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE oidc_login_states SET expires_at = NOW() - INTERVAL '1 second'")
        connection.commit()
    assert _error_of(_callback(client, params["state"]))["auth_error"] == "entra_state_invalid"
    assert idp.token_requests == []


@pytest.mark.parametrize("state", ["", "made-up-state"])
def test_unknown_or_missing_state_is_refused(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, state: str
) -> None:
    _start_login(client, idp)
    assert _error_of(_callback(client, state))["auth_error"] == "entra_state_invalid"


def test_microsoft_error_redirect_is_refused_without_logging_the_description(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _start_login(client, idp)
    description = "AADSTS50105: user henry@example.com is not assigned. secret-ish detail"
    with caplog.at_level(logging.INFO, logger="backend.entra_auth"):
        response = client.get(
            "/auth/entra/callback",
            params={"error": "access_denied", "error_description": description},
            follow_redirects=False,
        )
    assert _error_of(response) == {"auth_error": "entra_signin_failed"}
    assert "oauth_error=access_denied" in caplog.text
    assert "aadsts_code=AADSTS50105" in caplog.text
    assert "henry@example.com" not in caplog.text and "secret-ish" not in caplog.text


def test_token_endpoint_refusal_logs_allowlisted_fields_only(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    idp.token_status = 401
    idp.token_body = {
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided. leaked",
        "error_codes": [7000215],
        "correlation_id": "c0ffee00-1111-2222-3333-444444444444",
    }
    params = _start_login(client, idp)
    with caplog.at_level(logging.INFO, logger="backend.entra_auth"):
        response = _callback(client, params["state"])
    assert _error_of(response) == {"auth_error": "entra_signin_failed"}
    assert "oauth_error=invalid_client" in caplog.text
    assert "aadsts_code=AADSTS7000215" in caplog.text
    assert "correlation_id=c0ffee00-1111-2222-3333-444444444444" in caplog.text
    for leaked in ("leaked", "Invalid client secret", CLIENT_SECRET, "auth-code-123"):
        assert leaked not in caplog.text


@pytest.mark.parametrize(
    "case",
    ["bad_signature", "wrong_audience", "wrong_issuer", "expired", "wrong_nonce",
     "missing_nonce", "wrong_tenant", "missing_object_id"],
)
def test_invalid_id_tokens_are_refused(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    database_url: str,
    case: str,
) -> None:
    now = int(time.time())
    if case == "bad_signature":
        idp.signing_key = _OTHER_KEY
    else:
        idp.claim_overrides = {
            "wrong_audience": {"aud": "99999999-9999-9999-9999-999999999999"},
            "wrong_issuer": {"iss": f"https://{OTHER_TENANT}.ciamlogin.com/{OTHER_TENANT}/v2.0"},
            "expired": {"iat": now - 7200, "exp": now - 3600},
            "wrong_nonce": {"nonce": "attacker-nonce"},
            "missing_nonce": {"nonce": None},
            "wrong_tenant": {"tid": OTHER_TENANT},
            "missing_object_id": {"oid": None},
        }[case]
    params = _start_login(client, idp)
    if case == "wrong_nonce":
        idp.expected_nonce = "attacker-nonce"
    response = _callback(client, params["state"])
    assert _error_of(response) == {"auth_error": "entra_signin_failed"}
    assert _user_count(database_url) == 0
    assert client.get("/auth/session").json()["authenticated"] is False


# --- Sessions, sign-out and protected APIs ------------------------------------------


def test_database_outage_at_callback_shows_unavailable(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _start_login(client, idp)

    def down(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(entra_auth, "consume_oidc_login_state", down)
    response = _callback(client, params["state"])
    assert _error_of(response) == {"auth_error": "entra_unavailable"}
    assert any(
        c.startswith(f"{entra_auth.OIDC_BINDING_COOKIE_NAME}=") and "Max-Age=0" in c
        for c in response.headers.get_list("set-cookie")
    )


def test_database_outage_while_creating_the_session_shows_unavailable(
    client: TestClient,
    entra_env: None,
    idp: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as main

    params = _start_login(client, idp)

    def down(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(main, "create_session", down)
    response = _callback(client, params["state"])
    assert _error_of(response) == {"auth_error": "entra_unavailable"}
    assert not any(
        c.startswith("pst_session=") for c in response.headers.get_list("set-cookie")
    )


def test_signing_in_again_ends_the_previous_microsoft_session(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    _sign_in(client, idp)
    first_cookie = client.cookies.get("pst_session")
    _sign_in(client, idp)
    assert client.cookies.get("pst_session") != first_cookie
    assert client.get("/auth/session").json()["authenticated"] is True

    client.cookies.set("pst_session", first_cookie)
    assert client.get("/auth/session").json()["authenticated"] is False


def test_signing_in_again_ends_the_previous_password_session(
    client: TestClient,
) -> None:
    _register_local(client)
    first_cookie = client.cookies.get("pst_session")
    login = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "password123"}
    )
    assert login.status_code == 200
    second_cookie = client.cookies.get("pst_session")
    assert second_cookie != first_cookie

    client.cookies.set("pst_session", first_cookie)
    assert client.get("/auth/session").json()["authenticated"] is False
    client.cookies.set("pst_session", second_cookie)
    assert client.get("/auth/session").json()["authenticated"] is True


def test_logout_revokes_the_session_and_returns_the_microsoft_sign_out_url(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    _sign_in(client, idp)
    session_cookie = client.cookies.get("pst_session")
    response = client.post("/auth/logout")
    logout_url = urlparse(response.json()["logout_url"])
    assert f"{logout_url.scheme}://{logout_url.netloc}{logout_url.path}" == (
        idp.metadata["end_session_endpoint"]
    )
    assert parse_qs(logout_url.query) == {
        "client_id": [CLIENT_ID],
        "post_logout_redirect_uri": [POST_LOGOUT_URI],
    }
    # The old cookie no longer works, even if a browser kept it.
    client.cookies.set("pst_session", session_cookie)
    assert client.get("/auth/session").json()["authenticated"] is False
    assert client.get("/transactions").status_code == 401


def test_local_user_logout_has_no_microsoft_sign_out_url(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider
) -> None:
    _register_local(client)
    assert client.post("/auth/logout").json()["logout_url"] is None


def test_an_expired_session_is_rejected(
    client: TestClient, entra_env: None, idp: FakeIdentityProvider, database_url: str
) -> None:
    _sign_in(client, idp)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE user_sessions SET expires_at = NOW() - INTERVAL '1 second'")
        connection.commit()
    assert client.get("/auth/session").json()["authenticated"] is False
    assert client.get("/transactions").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/transactions"),
        ("GET", "/summary"),
        ("GET", "/uploads/recent"),
        ("GET", "/ai/settings"),
        ("GET", "/batch-imports/latest"),
        ("POST", "/transactions/manual"),
        ("DELETE", "/transactions/1"),
    ],
)
def test_data_endpoints_require_a_session(
    client: TestClient, entra_env: None, method: str, path: str
) -> None:
    response = client.request(method, path, json={})
    assert response.status_code == 401


# --- Local registration switch -------------------------------------------------------


def test_local_registration_can_be_turned_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    _register_local(client)
    client.post("/auth/logout")
    monkeypatch.setenv("LOCAL_REGISTRATION_ENABLED", "false")
    assert client.get("/auth/providers").json()["local_registration_enabled"] is False
    response = client.post(
        "/auth/register",
        json={"email": "mallory", "password": "password123", "display_name": "M"},
    )
    assert response.status_code == 403
    assert _user_count(database_url) == 1
    # Existing local accounts still sign in.
    login = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "password123"}
    )
    assert login.status_code == 200

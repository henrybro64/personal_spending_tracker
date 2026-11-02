# AI Agent Handoff

_Last updated: 2026-11-02 by Claude Code (Opus 5.5), after the review fixes_

## Task objective

Implement GitHub issue **#100**: Microsoft Entra ID sign-in and HTTPS for Personal Spending
Tracker, modelled on Stock Ticker (`henrybro64/real_time_stocks_data_explorer`), on a new working
branch, double-checking the work. Owner's constraints:

- **Do not push** to GitHub until the owner says so (saves GitHub Actions minutes).
- Do not commit, merge, or deploy without explicit instruction.
- From the issue: never touch production, Entra registrations, DNS, TLS or secrets; never touch
  Postgres data or PVCs; never touch Stock Ticker. Deliver a PR plus manual setup instructions.

## Current status

**Phase 1 is done and verified locally (uncommitted, not pushed).** Microsoft sign-in is implemented,
off by default, tested, and documented. An architect review (second session) approved it with one
should-fix and minor items; **all six review findings are now fixed and re-verified** (third
session; see "Review fixes"). **Phase 2 (HTTPS) is blocked on owner input**: how Cloudflare reaches
the k3s node (see "Outstanding issues"). Nothing has been deployed.

## Git context

- Branch `feat/issue-100-entra-auth`, created from `main` at `c80992f7` (Merge PR #104).
- **No commits. All changes are uncommitted.** Check with `git status` and `git diff --stat`.
  - New: `backend/entra_auth.py`, `backend/migrations/008_entra_identities.sql`,
    `backend/tests/test_entra_auth.py`, `docs/entra-auth.md`,
    `frontend/tests/e2e/entra-sign-in.spec.ts`, `k8s/12-entra-secret.example.yaml`.
  - Modified: `backend/{auth,database,main}.py`, `backend/tests/{conftest,test_currency_hardening}.py`,
    `frontend/app/{api.ts,auth-card.tsx,auth-provider.tsx}`, `k8s/{10-configmap,30-backend}.yaml`,
    `k8s/README-k3s.md`, `requirements.txt` (`PyJWT[crypto]`), `scripts/deploy-k3s.sh`, this file.
  - Modified by the review fixes: `frontend/tests/e2e/support/session.ts` and the specs
    `{batch-review,dashboard,data-entry,sign-in,transactions}.spec.ts` (27 files in total).

## Owner decisions (2026-11-02, asked explicitly)

1. **Identity mapping: "New user, like Stock Ticker."** The first Microsoft sign-in creates a new,
   empty, isolated user. It never merges with an existing local account (not by email, not by
   username). Existing data stays reachable through the local username/password login.
2. **Who may sign in: "Linked identities only."** Combined with decision 1, this means Microsoft
   sign-in works only for Entra identities on a config **allowlist** (`ENTRA_ALLOWED_OBJECT_IDS`, the
   Entra `oid`). The allowlist is checked on **every** sign-in, so removing an id revokes access. An
   allowed identity's first sign-in creates its user. Everyone else is refused, and the refusal shows
   the person their object ID so the owner can add it.

## Audit findings

Spending Tracker before this work: local username/password with server-side sessions (`pst_session`
cookie; `user_sessions.token_hash` is the SHA-256), every data endpoint behind `require_current_user`,
open self-registration, Browser → Next.js `/api` proxy → FastAPI. In k8s, `deploy-k3s.sh` builds the
ConfigMap and Secret with `--from-literal`, and pods use explicit `configMapKeyRef`/`secretKeyRef`
(no `envFrom`). The Ingress is HTTP-only (`web`) and only applied with `INGRESS_ENABLED=1`.

Stock Ticker (read-only clone at `f776790`): **Entra External ID** (CIAM) with authority
`https://stockticker.ciamlogin.com/4661e227-6bc3-4ff3-b9c2-df41f6420014/v2.0`; PKCE + state + nonce;
PyJWT + JWKS; allowlisted error logging; state in a `SessionMiddleware` cookie (`SESSION_SECRET`); a new
user on first sign-in, never merged. **Neither repo contains TLS, cert-manager or Cloudflare
configuration**: Stock Ticker's Ingress for `stock.cloudforge64.com` uses the HTTP `web` entrypoint, so
its TLS ends outside the repo.

## Changes made (phase 1)

- **Migration `008_entra_identities.sql`** (additive only): `user_identities` (unique `provider, issuer,
  subject`; stores `tenant_id`, `object_id`) and `oidc_login_states` (state hash, browser-binding hash,
  nonce, PKCE verifier, `return_to`, expiry).
- **`backend/database.py`**: `save_oidc_login_state`, `consume_oidc_login_state` (DELETE…RETURNING, so
  single use; rejects expired), `get_user_for_identity`, `create_user_with_identity` (new user only,
  never claims legacy rows, survives a concurrent first sign-in), `user_has_identity`, and
  `EXTERNAL_SIGN_IN_ONLY_MARKER` (Entra users can never use local login).
- **`backend/entra_auth.py`**: env settings; `entra_config_problems` (never echoes secrets); the startup
  log line; `safe_return_to` (same-origin paths only); PKCE/state/nonce; token exchange; ID-token
  validation (RS256 only, aud, iss, exp/iat with 60 s leeway, nonce, `tid` == tenant, `oid` GUID); the
  allowlist; cached metadata (the issuer must name the tenant) and JWKS; allowlisted diagnostics
  (OAuth error, AADSTS code, correlation/trace ids); `end_session_url`.
- **`backend/auth.py`**: `pst_oidc` binding-cookie helpers (HttpOnly, Lax, Secure per `SESSION_COOKIE_SECURE`).
- **`backend/main.py`**: `GET /auth/providers`; `GET /auth/entra/login`; `GET /auth/entra/callback`
  (on failure redirects to `/?auth_error=<code>`, plus `entra_object_id` for `entra_not_allowed`; audits
  every outcome); `logout_url` in the `POST /auth/logout` response for Entra users; the
  `LOCAL_REGISTRATION_ENABLED` flag (403 on `/auth/register`); the startup log.
- **Frontend**: a "Sign in with Microsoft" button (only when the backend reports Entra on); messages for
  each `auth_error` (the object id is echoed only if it is a GUID; the URL is cleaned afterwards);
  "Create account" is hidden when registration is off; logout goes to Microsoft's https sign-out URL.
- **k8s/deploy**: `deploy-k3s.sh` adds the `ENTRA_*`, `LOCAL_REGISTRATION_ENABLED` and
  `SESSION_COOKIE_SECURE` variables to the ConfigMap (`AUTH_SETTING_DEFAULTS`; the defaults reproduce
  today's behaviour on a first deploy, and an unset variable keeps its live value, see "Review fixes"). It warns
  (never fails, never prints) when Entra is on without Secret `spending-tracker-entra` or Secure cookies.
  `30-backend.yaml` reads each key explicitly (`optional: true`), and `ENTRA_CLIENT_SECRET` comes only
  from `spending-tracker-entra`. `10-configmap.yaml` (reference copy) is updated, and the
  `12-entra-secret.example.yaml` template is added (never applied by the script; it uses an explicit
  file list).
- **Docs**: `docs/entra-auth.md` covers the Entra portal steps (separate registration, redirect URI,
  user-flow association), settings, creating the Secret without echoing it, rollout with a DB health
  check and backup first, verification, rollback, troubleshooting, and the phase 2 questions.

## Technical decisions

- **Login state server-side, bound by cookie**, instead of Stock Ticker's signed session cookie: no new
  `SESSION_SECRET` to manage, single-use enforced by the DB, and login CSRF blocked by the binding hash.
- **Bad Entra config never stops the app**: the backend logs why and keeps Microsoft sign-in off, while
  password sign-in keeps working. That is the rollback path.
- **Identity key `(issuer, sub)`**, allowlist on `oid`, and a tenant check on both the metadata issuer and
  the token's `tid`.
- **Entra users get a username derived from `preferred_username`**, with `-2`, `-3`… on collision (for
  example `henry@example.com-2` when a local `henry@example.com` exists), so they can never collide with
  or shadow a local login.
- **Relative redirects only** (`return_to` validated), and the redirect URI comes from config, never from
  request headers, so proxy `Host`/`X-Forwarded-*` headers cannot steer the flow.

## Testing (all run 2026-11-02, real results)

| Check | Result |
|---|---|
| `python3.12 -m pytest -q` | **362 passed** (302 before + 60 in `test_entra_auth.py`) |
| Mutation checks: each security control removed alone (browser binding, nonce, tenant, allowlist, audience, state single use, state expiry, open redirect, metadata tenant) | each one fails at least one test; files restored byte-identical |
| `npx tsc --noEmit`, `npm run test:unit` (52), `npm run build` | pass |
| `CI=true npm run test:e2e` | **42 passed** (35 + 7 in `entra-sign-in.spec.ts`) |
| e2e negative control (`main`'s `auth-card`/`auth-provider`/`api.ts`) | 6 of 7 new tests fail; 1 is a regression guard |
| Next.js proxy with a stub backend: 302 + two `Set-Cookie` headers | passed through unchanged |
| `deploy-k3s.sh` with a stub kubectl | renders all 10 new backend env keys, no unresolved `${…}`, defaults unchanged; warnings only when Entra is on. Exit 1 at the final macOS `hostname -I`, same as `main` |
| Secret scan of the diff | clean; the Stock Ticker client id is not copied |

Not run (needs the owner): a real Microsoft sign-in. It needs the Entra app registration and HTTPS.
(Counts above are from the implementation session; see "Review fixes" for the latest run.)

## Architect review (2026-11-02, second session)

**Verdict: approve phase 1, with one should-fix before the first real rollout.** Working tree
unchanged from the implementation session (21 files). No code was changed in the review.

Re-run in this session (real results): `python3.12 -m pytest -q` 362 passed; `npx tsc --noEmit` ok;
`npm run test:unit` 52/52; `npm run build` ok; `CI=true npm run test:e2e` 42 passed.

Confirmed correct, with evidence:
- **No cross-user exposure through legacy claims.** `_claim_legacy_rows` (`backend/database.py`) only
  claims when the new account is the *only* user, so a live server with users never hands legacy rows
  to a newcomer, and `create_user_with_identity` never claims.
- **Concurrent first sign-in** (the rollback branch of `create_user_with_identity`, not covered by
  the suite) was forced with a temporary probe test (deleted afterwards): the duplicate insert rolls
  back and the same user is returned (1 user, 1 identity, no orphan).
- **`NEXT_PUBLIC_API_BASE_URL` is never set** in the Dockerfile, k8s or CI, so the Microsoft button always
  uses relative `/api` (same-origin, through the proxy or Traefik).
- Redirects are relative and the redirect URI comes from config, so proxy headers cannot steer them.
  The ID token is RS256-only (no algorithm confusion). JWKS fetch errors are `PyJWTError`s and are
  handled. The allowlist, tenant, nonce and audience checks are each covered by a test that fails
  without them.
- Agree with the decisions: server-side login state with a browser binding, the allowlist on `oid`,
  never merging, bad config never stopping the app, the separate Secret for the client secret only,
  and splitting phase 2 out until the Cloudflare path is known.

Findings:
1. **Should fix (medium): a routine redeploy silently undoes the security settings.**
   `scripts/deploy-k3s.sh` lines ~27–35 default `ENTRA_ENABLED=false`, `LOCAL_REGISTRATION_ENABLED=true`
   and `SESSION_COOKIE_SECURE=false` whenever the variables are unset, and rebuild the ConfigMap from
   them on every run. Once the site is public with registration off and Secure cookies on, any later
   deploy without the env file (for example following README "Quick deploy") **reopens public
   self-registration**, drops the Secure flag, and turns Microsoft sign-in off, with no warning.
   **Fix:** for the 9 new keys, when the variable is unset, reuse the live ConfigMap value (the same
   pattern as `resolve_llm_settings_encryption_key`), print which keys were reused (values for non-secret
   keys are fine to print), and fall back to the defaults only when there is no live value. Verify with
   the stub kubectl used before (it must answer `get configmap … -o jsonpath`). Update
   `docs/entra-auth.md` § 2, which currently says to pass the same values every time.
2. **Low: unbounded writes from unauthenticated `GET /auth/entra/login`.** When Entra is on, each call
   inserts an `oidc_login_states` row (expired rows are only purged on the next login start), and the
   app has no rate limiting. **Fix:** cap unexpired states (for example refuse with
   `auth_error=entra_unavailable` above 10,000), or add rate limiting (already listed as a follow-up).
3. **Nit: a database outage during `/auth/entra/login` or the callback** shows the raw JSON error page
   (it is a top-level navigation). It could redirect with `auth_error=entra_unavailable` instead.
4. **Nit, pre-existing pattern:** signing in again in the same browser replaces the `pst_session` cookie
   but does not revoke the previous server-side session (`/auth/login` behaves the same).
5. **Nit: e2e log noise.** Specs that don't mock `/api/auth/providers` print `ECONNREFUSED` lines. A
   default mock in `frontend/tests/e2e/support/` would quieten them.
6. **Watch in CI:** there is no `sonar-project.properties`, so SonarCloud runs with defaults. The fake
   `CLIENT_SECRET` constant in `backend/tests/test_entra_auth.py` may be raised as a security hotspot
   needing review. Existing tests already use literal passwords and recent gates passed.

## Review fixes (2026-11-02, third session)

Owner asked to fix all review findings. Each fix, and how it was proven:

1. **Deploy keeps live sign-in settings** (`scripts/deploy-k3s.sh`, `resolve_auth_settings`, called just
   before "Applying config map"). For each of the 9 keys in `AUTH_SETTING_DEFAULTS`, an **unset**
   variable takes the live ConfigMap value (`-o jsonpath={.data.KEY}`), else the default; a variable
   that is set, even to empty, wins (`ENTRA_ALLOWED_OBJECT_IDS=` revokes everyone). It prints the
   reused key names and a `Sign-in settings: ENTRA_ENABLED=… LOCAL_REGISTRATION_ENABLED=…
   SESSION_COOKIE_SECURE=…` line. If the ConfigMap exists but can't be read (anything but NotFound), the
   deploy exits before changing anything. Stub-kubectl runs (macOS `/bin/bash` 3.2): first deploy →
   defaults; redeploy with nothing set → live `true/false/true` and allowlist kept; explicit
   `ENTRA_ENABLED=false ENTRA_ALLOWED_OBJECT_IDS=` → those two replaced, the rest kept; unreachable
   cluster → exit before the ConfigMap step. A full stub run reaches "Deployment complete." (exit 1
   only at the macOS `hostname -I`, as on `main`). Docs: `docs/entra-auth.md` §2 and Rollback,
   `k8s/README-k3s.md` Notes.
2. **Pending sign-ins capped**: `save_oidc_login_state(..., max_pending)` inserts only while fewer than
   `entra_auth.MAX_PENDING_LOGIN_STATES` (10,000) rows exist (after purging expired ones) and returns
   whether it stored; `begin_login` logs `entra login_state_limit` and refuses with `entra_unavailable`.
3. **Database outage → friendly redirect**: `main.py` `_entra_database_unavailable` catches
   `_DATABASE_UNAVAILABLE` (psycopg Operational/Interface errors, `PoolTimeout`) in `/auth/entra/login`
   and around the whole callback (state, user, session creation), logs `entra database_unavailable
   error_type=…` (no message text), clears `pst_oidc`, redirects to `/?auth_error=entra_unavailable`.
4. **Re-sign-in revokes the previous session**: `_revoke_previous_session(request)` in both
   `/auth/login` and the Entra callback.
5. **e2e noise gone**: `mockProviders()` in `frontend/tests/e2e/support/session.ts`, used by the shared
   and the per-spec session mocks; the Entra spec reuses it. Full e2e run prints no `ECONNREFUSED`.
6. **Sonar hotspot avoided**: the test `CLIENT_SECRET` is now `secrets.token_urlsafe(24)` per run.

New tests (`backend/tests/test_entra_auth.py`, 6): `test_pending_sign_ins_are_capped`,
`test_database_outage_at_login_start_shows_unavailable`, `…_at_callback_…`,
`…_while_creating_the_session_…`, `test_signing_in_again_ends_the_previous_{microsoft,password}_session`.
Mutation checks: removing the cap, either outage handler, or the revoke makes these fail; files were
restored byte-identical (`cmp`).

Re-run after the fixes (real results): `python3.12 -m pytest -q` **368 passed**; `npx tsc --noEmit` ok;
`npm run test:unit` **52/52**; `npm run build` ok; `CI=true npm run test:e2e` **42 passed**, no
`ECONNREFUSED` lines.

## PR #105 and the SonarCloud gate (2026-11-02)

- Phase 1 committed as `e7921192` and pushed; PR **#105** ("Part of #100"). CI: backend and frontend
  passed; **SonarCloud failed "Security Rating on New Code" (C, needs A)**. The issue list is private
  (no Sonar token here), so the exact rule was not visible.
- Fix pushed in a second commit, aimed at the likely cause (log injection taint): `allowlisted_oauth_error`
  now returns the allowlist's own string (`_OAUTH_ERROR_ALLOWLIST` is a dict mapping each code to itself),
  and `extract_aadsts_code` rebuilds the code from `int(...)`. Also `auth-card.tsx` looks up
  `ENTRA_ERROR_MESSAGES` by own key only (`?auth_error=__proto__` used to show no message; new e2e test,
  fails without the fix).
- Re-run locally: pytest 368 passed; unit 52/52; build ok; e2e 43 passed.
- That did not change the gate. The real cause, found in the Sonar check-run **annotations**
  (`gh api repos/henrybro64/personal_spending_tracker/check-runs/<id>/annotations`, readable without a
  Sonar token): rule "hard-coded credential" on `EXTERNAL_ONLY_PASSWORD_HASH` in `backend/database.py`
  (a marker, not a credential). Renamed to `EXTERNAL_SIGN_IN_ONLY_MARKER` in a third commit; also
  `Object.hasOwn` and an optional chain from Sonar's notes. The other annotations are maintainability
  only (HTTPException `responses` docs, cognitive complexity of `entra_config_problems` and
  `extract_aadsts_code`, test-assertion style) and do not fail the gate.
- **If SonarCloud still fails**, the owner must open the PR's Sonar issues page and give the rule and
  file/line: `https://sonarcloud.io/project/issues?id=henrybro64_personal_spending_tracker&pullRequest=105`.

## Outstanding issues

1. **Phase 2 (HTTPS) needs owner input**: how `stock.cloudforge64.com` reaches k3s (Cloudflare Tunnel or a
   proxied DNS record to a public IP) and whether cert-manager exists. The read-only commands are in
   `docs/entra-auth.md` → "HTTPS (phase 2)". Microsoft sign-in cannot be used in production before HTTPS.
2. The owner must confirm the hostname (`spending.cloudforge64.com` is a proposal) and the tenant (same
   External ID tenant as Stock Ticker, or another).
3. Not done (follow-ups): rate limiting of sign-in attempts (the app has none today), and `X-Forwarded-For`
   trust for audit IPs (only used for the audit log).

## Next steps

1. Done: review findings fixed (see "Review fixes"). Optionally have the owner (or a reviewer) look at
   `git diff` for the fixes.
2. Ask the owner to answer the phase 2 questions above.
3. On the go-ahead: commit phase 1, push `feat/issue-100-entra-auth`, and open a PR that says "Part of
   #100 (phase 1)", not "Closes". Deploying it is a no-op until `ENTRA_ENABLED=true`.
4. Phase 2, once answered: add a distinct Ingress/TLS (or Tunnel route) for the confirmed hostname, an
   HTTP→HTTPS redirect, and docs; then the owner registers the app and turns it on per `docs/entra-auth.md`.

## Related GitHub issues/PRs

- #100 (this task). Stock Ticker references: its #240, #299 (Entra), #273 (proxy hops).
- Previous merged work: PR #104 (#99), PR #103 (#96/#97/#98), PR #102 (#101).

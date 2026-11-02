# Microsoft Entra sign-in

Spending Tracker can offer **Sign in with Microsoft** next to the username and
password sign-in (#100). It is off unless `ENTRA_ENABLED=true`, and password
sign-in keeps working whether it is on or off.

Read this before turning it on: **Microsoft sign-in needs the app to be served
over HTTPS** (Microsoft only redirects to `https://` callbacks, except
`localhost`). HTTPS for `spending.cloudforge64.com` is phase 2 of #100; see
[HTTPS (phase 2)](#https-phase-2). Until then, leave `ENTRA_ENABLED` unset.

## How it works

- Authorization code flow with PKCE, `state` and `nonce`. The backend exchanges
  the code with Microsoft and checks the ID token's signature (RS256, keys from
  the tenant's JWKS), audience, issuer, expiry, nonce and tenant (`tid`).
- The login transaction is stored in Postgres (`oidc_login_states`, 10-minute
  expiry, single use) and tied to the browser by an HttpOnly cookie
  (`pst_oidc`), so a callback link from someone else's login cannot sign you in.
- Only Entra **object ids** listed in `ENTRA_ALLOWED_OBJECT_IDS` may sign in.
  The list is checked on every sign-in, so removing an id revokes access.
- The first sign-in of an allowed identity creates a **new, empty user**. It
  never attaches to an existing local account, even when the email matches.
  Existing data stays with its local account and is reached with that
  account's username and password.
- After sign-in the app uses its normal session cookie (`pst_session`). Any
  session the browser already had is revoked. Sign out revokes the session
  and then opens Microsoft's sign-out page.

## 1. Entra app registration (owner, in the portal)

Use a **separate app registration** for Spending Tracker. Do not reuse or edit
the Stock Ticker registration, and never copy its client secret.

Stock Ticker uses an **Entra External ID** tenant. If Spending Tracker uses the
same tenant, its authority is the same tenant URL:
`https://stockticker.ciamlogin.com/4661e227-6bc3-4ff3-b9c2-df41f6420014/v2.0`.
Confirm this in the portal before using it.

In the [Microsoft Entra admin center](https://entra.microsoft.com), switch to
that tenant, then:

1. **App registrations → New registration.**
   - Name: `Personal Spending Tracker`.
   - Supported account types: *Accounts in this organizational directory only*.
   - Redirect URI: platform **Web**,
     `https://spending.cloudforge64.com/api/auth/entra/callback`
     (use your confirmed hostname; the path must stay `/api/auth/entra/callback`).
2. **Authentication.** Add a second Web redirect URI for sign-out:
   `https://spending.cloudforge64.com/`. Leave implicit grant (access and
   ID tokens) **off**.
3. **Certificates & secrets → New client secret.** Copy the **Value** once;
   you put it in a Kubernetes Secret in step 3. Note its expiry date.
4. **API permissions.** Microsoft Graph delegated `openid`, `profile`,
   `email`. Grant admin consent if the portal asks.
5. **External ID only: User flows →** your sign-up/sign-in flow →
   **Applications → Add application** → `Personal Spending Tracker`. Without
   this, sign-in fails with an AADSTS error.
6. Note the **Application (client) ID** and **Directory (tenant) ID** from the
   app's Overview page.
7. Find the **Object ID** of each person who may sign in: **Users** → the user
   → *Object ID*. Or try signing in once; the refusal message shows it.

If your sign-up flow allows self-service sign-up, anyone can create an account
in the tenant. The allowlist is what keeps them out of Spending Tracker.

## 2. Settings

Non-secret settings, passed to `scripts/deploy-k3s.sh` and stored in the
`spending-tracker-config` ConfigMap:

| Variable | Example | Notes |
|---|---|---|
| `ENTRA_ENABLED` | `true` | Default `false` |
| `ENTRA_TENANT_ID` | `4661e227-…` | Directory (tenant) ID |
| `ENTRA_CLIENT_ID` | `…` | The **Spending Tracker** app's client ID |
| `ENTRA_AUTHORITY` | `https://stockticker.ciamlogin.com/<tenant-id>/v2.0` | Must contain the tenant ID |
| `ENTRA_REDIRECT_URI` | `https://spending.cloudforge64.com/api/auth/entra/callback` | Exactly as registered |
| `ENTRA_POST_LOGOUT_REDIRECT_URI` | `https://spending.cloudforge64.com/` | Optional; registered in step 1.2 |
| `ENTRA_ALLOWED_OBJECT_IDS` | `<oid>,<oid>` | Comma-separated. Empty means nobody |
| `SESSION_COOKIE_SECURE` | `true` | Required with HTTPS (see note below) |
| `LOCAL_REGISTRATION_ENABLED` | `false` | Recommended on a public hostname |

**Redeploys keep these settings.** For each of these variables that is not
set, the deploy script keeps the value already in the live ConfigMap (the
defaults above apply only on a first deploy). So a routine redeploy cannot
quietly reopen registration, drop Secure cookies or turn Microsoft sign-in
off. The script prints which values it kept and the resulting
`Sign-in settings: …` line; check it. Setting a variable, even to an empty
value, replaces the live value: `ENTRA_ALLOWED_OBJECT_IDS=` revokes everyone.
If the ConfigMap exists but cannot be read, the deploy stops before changing
anything.

Keeping the values in an untracked file outside the repository, for example
`~/spending-tracker-deploy.env`, sourced before deploying, is still the
clearest record of what is set. Never put the client secret in it.

`ENTRA_CLIENT_ID` belongs in the ConfigMap only. Do not add it to any Secret:
the backend reads each variable from one explicit source, and a second copy
is how Stock Ticker's `envFrom` collision happened (its #299).

**Cookie note.** With `SESSION_COOKIE_SECURE=true`, browsers only send the
session cookie over HTTPS, so signing in at `http://192.168.120.102:30081` stops
working. Switch it on only once HTTPS is verified, and decide then whether to
keep the HTTP NodePort.

## 3. Client secret (owner, on the node)

The client secret lives only in its own Secret, `spending-tracker-entra`, key
`ENTRA_CLIENT_SECRET`. The deploy script checks that it exists but never
creates, changes or prints it. Create it without the value appearing on screen,
in shell history or in process arguments:

```bash
read -rs ENTRA_CLIENT_SECRET   # paste the secret Value, press Enter (nothing is shown)
printf '%s' "$ENTRA_CLIENT_SECRET" | sudo k3s kubectl -n spending-tracker \
  create secret generic spending-tracker-entra \
  --from-file=ENTRA_CLIENT_SECRET=/dev/stdin --dry-run=client -o yaml \
  | sudo k3s kubectl apply -f -
unset ENTRA_CLIENT_SECRET
```

Check that it exists without printing it:

```bash
sudo k3s kubectl -n spending-tracker get secret spending-tracker-entra \
  -o jsonpath='{.data.ENTRA_CLIENT_SECRET}' | wc -c   # non-zero means present
```

To rotate the secret, create a new one in the portal, run the same commands,
then `sudo k3s kubectl -n spending-tracker rollout restart deployment/backend`.

## 4. Rollout

1. **Check the database first.** The node had a Postgres startup failure
   (#101); do not assume it is healthy.

   ```bash
   sudo k3s kubectl -n spending-tracker get pods
   sudo k3s kubectl -n spending-tracker exec deploy/postgres -- pg_isready -U spending_tracker
   ```

2. **Back up.** `KUBECTL='sudo k3s kubectl' ./scripts/backup-spending-tracker.sh --k3s`
   (see `docs/backup-restore.md`).
3. **Deploy.** The backend applies migration `008_entra_identities` on start.
   It only **adds** two tables (`user_identities`, `oidc_login_states`); no
   existing row or PVC is touched.

   ```bash
   source ~/spending-tracker-deploy.env   # the non-secret settings above
   LOCAL_DEPLOY=1 KUBECTL='sudo k3s kubectl' bash scripts/deploy-k3s.sh
   ```

   Warnings about a missing `spending-tracker-entra` Secret or
   `SESSION_COOKIE_SECURE` mean Microsoft sign-in will stay off; password
   sign-in is unaffected.

## 5. Verify

1. Discovery works:
   `curl -fsS "$ENTRA_AUTHORITY/.well-known/openid-configuration" | head -c 200`.
2. The backend says Microsoft sign-in is on, with no secret in the line:
   `sudo k3s kubectl -n spending-tracker logs deploy/backend | grep 'entra sign-in'`
   should show `entra sign-in enabled client_id=… redirect_host=… allowed_identities=N`.
3. `curl -fsS https://spending.cloudforge64.com/api/auth/providers` shows
   `"entra_enabled": true`.
4. Sign in with Microsoft. You land back on the page you started from. A first
   sign-in shows an **empty** account; that is expected.
5. Sign out. Microsoft's sign-out page opens, then returns to the app.
6. Sign in with an existing **local** username and password. Its transactions,
   uploads and summaries are unchanged.
7. Try a Microsoft account that is not on the allowlist. You should see
   "Microsoft account not allowed" with its object ID, and no user is created.

## Rollback

Microsoft sign-in is additive, so rollback leaves all data in place:

```bash
source ~/spending-tracker-deploy.env
ENTRA_ENABLED=false LOCAL_DEPLOY=1 KUBECTL='sudo k3s kubectl' bash scripts/deploy-k3s.sh
```

Password sign-in works throughout. Users created by Microsoft sign-in, and
their data, stay in the database and come back if Microsoft sign-in is turned
on again. The two new tables are harmless when unused; do not drop them while
any user was created by Microsoft sign-in. Redeploys keep the live
`LOCAL_REGISTRATION_ENABLED` and `SESSION_COOKIE_SECURE`; to undo them, set
them explicitly, for example `LOCAL_REGISTRATION_ENABLED=true
SESSION_COOKIE_SECURE=false`.

## Troubleshooting

Look in the backend log (`sudo k3s kubectl -n spending-tracker logs deploy/backend`).
Lines start with `entra`. They never contain codes, tokens, the client secret,
or Microsoft's error descriptions:

| Log line / page message | Meaning |
|---|---|
| `entra sign-in disabled: invalid configuration: …` | A setting is missing or malformed; the line names it |
| `…ENTRA_ALLOWED_OBJECT_IDS is empty…` | Nobody can sign in with Microsoft yet |
| `entra authorize_error oauth_error=… aadsts_code=…` | Microsoft refused before redirecting back, for example a user flow without the app (step 1.5) |
| `entra token_exchange_failed http_status=401 oauth_error=invalid_client` | Wrong or expired client secret (step 3) |
| `entra token_exchange_failed … oauth_error=invalid_grant` | Code reused or redirect URI mismatch |
| `entra id_token_rejected error_type=…` | Token failed validation (tenant, audience, nonce, expiry) |
| `entra metadata_unavailable` | The authority URL is wrong or unreachable |
| `entra database_unavailable error_type=…` | Postgres was unreachable during sign-in; the page shows "unavailable" |
| `entra login_state_limit max_pending=…` | Over 10,000 unfinished sign-ins in 10 minutes, likely scripted calls; it clears as they expire |
| "Microsoft sign-in expired" | Took over 10 minutes, or the callback opened in another browser |
| "Microsoft account not allowed" | Add the shown object ID to `ENTRA_ALLOWED_OBJECT_IDS` and redeploy |

## HTTPS (phase 2)

Not implemented yet. Neither this repository nor Stock Ticker's contains the
TLS setup: Stock Ticker's Ingress for `stock.cloudforge64.com` uses Traefik's
plain-HTTP `web` entrypoint, so its HTTPS ends somewhere outside the repository.
Before phase 2, confirm on the node (all read-only):

```bash
sudo k3s kubectl get ingress,ingressroute -A
sudo k3s kubectl get clusterissuer,certificate -A      # is cert-manager installed?
sudo k3s kubectl get pods -A | grep -i -E 'cloudflared|cert-manager'
sudo k3s kubectl -n kube-system get svc traefik         # which ports are exposed
```

And in the Cloudflare dashboard, how `stock.cloudforge64.com` is published: a
proxied A/AAAA record to your public IP, or a CNAME to `*.cfargotunnel.com`
(Cloudflare Tunnel). Phase 2 then adds a distinct Ingress, TLS Certificate and
Secret (or Tunnel route) for `spending.cloudforge64.com`, an HTTP-to-HTTPS
redirect, and the DNS step, without changing anything Stock Ticker uses.

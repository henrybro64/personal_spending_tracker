# Agent Instructions

Stable notes for AI coding agents. Current, unfinished work lives in
`docs/AI_HANDOFF.md`; read it first.

## Owner's working rules

- Work on a feature branch, never directly on `main`.
- Do not commit, push, merge, or deploy without the owner's explicit go-ahead.
  Each push runs GitHub Actions, which costs the owner minutes.
- Never record secrets (API keys, tokens, passwords) in the repository.
- Microsoft (Entra) sign-in: see `docs/entra-auth.md`. Non-secret `ENTRA_*` settings go in the
  ConfigMap only; the client secret goes only in Secret `spending-tracker-entra`. Never touch Stock
  Ticker's app registration, DNS, certificates or secrets from this repo.

## GitHub CLI

The shell may export an invalid `GH_TOKEN` that overrides the valid keyring
login. Run gh as `env -u GH_TOKEN -u GITHUB_TOKEN gh …`.

## Tests

- Backend: `python3.12 -m pytest -q` from the repo root (CI uses Python 3.12).
  `backend/tests/conftest.py` starts a throwaway Postgres with the local
  `initdb`/`pg_ctl`.
- Frontend (in `frontend/`): `npm run test:unit`, `npm run build`,
  `npm run test:e2e` (Playwright starts `next dev` on port 3000).
- CI: `.github/workflows/ci.yml` runs the backend tests against Postgres 16,
  then the frontend unit tests, build, and Playwright.

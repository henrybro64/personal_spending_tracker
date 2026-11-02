# AI Agent Handoff

_Last updated: 2026-11-02 by Claude Code (Opus 5.5)_

## Task objective

Implement GitHub issues **#98**, **#97** and **#96** (all part of #95) on a new
working branch, double-checking the work. Owner's constraints:

- **Do not push** to GitHub until the owner says so (saves GitHub Actions minutes).
- Do not commit, merge, or deploy without explicit instruction.

## Current status

**All three issues are implemented and verified locally. An independent architect review
(second session) approved them. At the owner's request ("fix all the problems"), all five
review findings were fixed in a third session; see "Review fixes".
The owner approved commit + push + PR; everything is in one commit on the branch with a PR open against `main`.** Waiting on CI.

| Issue | Status |
|---|---|
| #98 Posting date ends up in the merchant (upload vs batch double count) | Done |
| #97 Reject NaN / Infinity / oversized amounts | Done |
| #96 API proxy failure reason, hop-by-hop headers, "Server unavailable" | Done |

## Git context

- Branch `fix/issues-96-97-98`, created from `main` at `1da5e932` (Merge PR #102).
- One commit on the branch with all the work (including this file and the new `AGENTS.md`),
  pushed to `origin/fix/issues-96-97-98`. Find the PR with
  `env -u GH_TOKEN -u GITHUB_TOKEN gh pr list --head fix/issues-96-97-98`.
- Check the local state with `git log --oneline main..fix/issues-96-97-98` and `git status`.

## Changes made

### #98: posting date in the merchant
- `backend/parser.py`: `TRANSACTION_PATTERN` and `TRANSACTION_PATTERN_ISO` gained an
  optional `(?:(?P<posted_date>…)\s+)?` group between the date and the merchant
  (the same shape as `AmexStatementParser._line_pattern`). The group is optional, so
  the set of lines that match is unchanged and only the merchant text changes. That
  means `_line_looks_like_transaction` and DBS/CTBC routing are unaffected.
- `backend/batch_import/importer.py`: the per-file "bank parser + upload fallback merge"
  step is extracted, unchanged, into `parse_statement_with_fallback(...)`.
  `StatementImporter.import_transactions` calls it, and so does the new cross-path test,
  so the test cannot drift from the real code.
- `backend/check_spending_data.py`: a new **read-only** report section, "Upload rows with a
  date in the merchant". It lists rows whose merchant matches `MM/DD[/YY[YY]] ` or
  `YYYY-MM-DD ` at the start, with counts per user (`date_prefixed_merchants`,
  `date_prefixed_merchant_counts` on `SpendingDataReport`). It excludes `batch-import/%`
  rows (as the issue says) **and** `manual-entry` rows (typed by hand, not parser output).
  It handles a NULL `transactions.user_id` (the column is nullable). There is no repair.
- `docs/data-lifecycle.md`: the report description mentions the new section.
- The source key format in `backend/transaction_source.py` is **unchanged**.

### #97: non-finite and oversized amounts
- `backend/currency.py`: `MAX_TRANSACTION_AMOUNT = 1_000_000_000`,
  `MAX_EXCHANGE_RATE = 1_000_000`, `MAX_BASE_AMOUNT = 999_999_999_999.99`.
- `backend/bulk_import.py` `_parse_amount_value`: `math.isfinite` check, then the
  bound check. It also catches `OverflowError` from `float(<400-digit JSON int>)`, which
  was another 500 the issue didn't list. Messages: `Row N: amount must be a finite number, got 'NaN'.`
  and `Row N: Amount is too large.` (`Row N:` comes from the existing wrapper in `parse_import_content`).
- `backend/main.py` `_parse_manual_transaction_fields`: the finiteness check comes before the
  `<= 0` checks. `Amount must be a finite number.`, `Amount is too large.`, and
  `Exchange rate must be a finite number.` are new. The existing messages are unchanged.
  **Beyond the issue:** `Exchange rate is too large.` (rate > 1e6), and `Amount is too large.`
  when the derived base amount would overflow `base_amount NUMERIC(14,2)`. Without these, an
  absurd exchange rate still produced a 500.

### #96: API proxy
- `frontend/app/api/proxy-helpers.ts` (no `next/*` imports):
  - `HOP_BY_HOP_REQUEST_HEADERS` and `forwardableRequestHeaders`, which also drops headers named in `Connection`.
  - `describeFetchFailure(error, {timeoutMs})`: a `TimeoutError`/`AbortError` gives
    "timed out after N s". Otherwise it uses `cause.code`, then `cause.message`, then
    `error.message`, then a fallback.
  - `redactBackendHost(text, BACKEND)`: a safety net so the browser `detail` never contains the backend hostname.
- `frontend/app/api/[...path]/route.ts`: uses the helpers. Log line:
  `[api proxy] API proxy to backend failed: ECONNREFUSED (POST http://localhost:8000/auth/login)`.
  The browser `detail` is `API proxy to backend failed: ECONNREFUSED`, with no URL. The status
  stays 502. Timeouts (`BATCH_IMPORT_TIMEOUT_MS`, `uploadTimeoutMs()`) are unchanged, and there are still no retries.
- `frontend/app/auth-card.tsx`: an `ApiError` with status 502/503/504 shows "Server unavailable" /
  "The app server is not responding. Try again in a moment." Everything else keeps "Authentication error".

## Technical decisions

- The #96 strings stay inline in `auth-card.tsx`, not in `messages.ts`. The auth card doesn't use the
  localized catalogue (all its strings are inline English), so adding just two keys there would be inconsistent.
- A browser `TypeError` (network failure) is **not** mapped to "Server unavailable". The issue
  specifies a status-based rule, and a `TypeError` can also be a client bug.
- `describeFetchFailure` puts the code first: Node reports `localhost` refusals as an `AggregateError`
  with an **empty message** and `code: ECONNREFUSED` (verified on Node 20).
- The #97 API tests send raw JSON (`content=json.dumps(...)`) because **httpx 0.28 refuses NaN in `json=`**.
  The issue's suggested snippet would crash before reaching the server.

## Testing (all run 2026-11-02, real results)

| Check | Result |
|---|---|
| Baseline `python3.12 -m pytest -q` on the branch before changes | 222 passed |
| `python3.12 -m pytest -q` after all changes | **253 passed**, 13 subtests |
| Negative control #98: old `parser.py` | the 2 parser tests and the Chase cross-path case fail; the DBS/CTBC cases pass (as the issue predicted) |
| Negative control #97: old `main.py`/`bulk_import.py`/`currency.py` | all 23 new tests fail |
| `npm run test:unit` | 52 pass, 0 fail |
| `npx tsc --noEmit`, `npm run build` | pass |
| `CI=true npm run test:e2e` | **35 passed** (includes 4 new sign-in cases) |
| Negative control #96: old `auth-card.tsx` | the 3 new 502/503/504 e2e cases fail; the 401 case passes |
| Live `next dev` with backend stopped (`API_PROXY_TARGET=http://localhost:8765`) | 502 `{"detail":"API proxy to backend failed: ECONNREFUSED"}`. One log line with ECONNREFUSED and the URL. A real Chromium sign-in showed "Server unavailable" |
| Live with uvicorn + a throwaway Postgres: `curl -H 'Expect: 100-continue' …/api/auth/login` | backend 400 (reached the backend). Keep-Alive/Upgrade/TE also reached it. **Control:** the old `route.ts` returned 502 `fetch failed` |
| `grep -n "float(" backend/bulk_import.py` | both conversions flow into `math.isfinite` |

Not run: the issue's optional SQL check for already-stored NaN rows
(`SELECT id, user_id FROM transactions WHERE amount = 'NaN'::numeric OR original_amount = 'NaN'::numeric OR base_amount = 'NaN'::numeric`).
This needs the owner's real database. Out of scope per #97.

## Architect review (2026-11-02, second session)

The owner asked for an independent review. **Verdict: approve. It meets all three issues'
specs and acceptance criteria, and I'd ship it.** The working tree was unchanged from the
implementation session (same `git diff --stat`, no other agent's edits).

Re-run in this session (real results): `python3.12 -m pytest -q` 253 passed;
`npx tsc --noEmit` ok; `npm run test:unit` 52/52; `npm run build` ok;
`CI=true npm run test:e2e` 35 passed. A live `next dev` with the backend stopped
(`API_PROXY_TARGET=http://localhost:8765`, with an `Expect: 100-continue` request)
returned 502 `{"detail":"API proxy to backend failed: ECONNREFUSED"}` and logged one line
with the target URL.

Extra probes (a temporary test file was deleted afterwards):
- `POST /batch-imports/import` with NaN, `1e400` and a 16-digit amount returns **400** with the
  row message, for both `destination: batch` and `destination: dashboard`. The implementation
  tested this only at the parser level; the endpoint is now confirmed too.
- Posting-date regex edge cases all behave as #98 specifies: `01/05 01/06 42.10` (merchant stays
  `01/06`), `/YYYY` posting date, extra spaces, three dates (only one is consumed), and
  `12/25XMAS` (not consumed, since there is no space).
- No other code depends on the old proxy text or the "Authentication error" title (grep).
- The `redactBackendHost` safety net is a no-op in production (`API_PROXY_TARGET=http://backend:8000`
  in `frontend/Dockerfile`) and harmless: codes such as ECONNREFUSED never contain the host.

Findings (none blocking, no code changed in the review):
1. **Follow-up bug, same class as #97 (medium; pre-existing, out of scope).** The PDF *upload*
   parser (`backend/parser.py`, amount group `\d[\d,]*`) accepts long digit runs. For example,
   `01/05 ACCOUNT 4111111111111111` parses as amount 4.1e15, and `insert_transactions` then raises
   `psycopg NumericValueOutOfRange`, so **the whole upload returns 500**. A statement line ending
   in a reference number could trigger it. Suggest a new issue: in the upload and batch parsers,
   treat a row with `abs(amount) > MAX_TRANSACTION_AMOUNT` as not a transaction (or reject it with a
   400 naming the line).
2. **Data caveat to put in the PR (low).** Rows stored before #98 keep their old merchant/key. That
   affects batch merges (the issue's note) **and** overlapping uploads: with a known `card_last4` the
   key scope is empty, so a later daily/monthly statement with the same charge also adds a second
   copy. The new report section finds these rows, so no code change is needed. Mention it in the PR
   and optionally in `docs/data-lifecycle.md`.
3. **Nit: message casing (fixed, see "Review fixes").** Bulk import said `Row N: amount is too large.` (lowercase, matching that
   file's other row messages). #97's text says "Amount is too large." Tests don't depend on it.
   Fine as is; note it in the PR.
4. **Nit: a JSON `1e400` amount is echoed as `got inf.`** because json.loads has already converted it.
   That's acceptable.
5. **Nit, pre-existing:** sub-cent amounts such as `0.001` pass "non-zero" but round to `0.00` in
   `NUMERIC(12,2)`. Out of scope.

## Review fixes (2026-11-02, third session)

The owner said "fix all the problems". Every architect-review finding is fixed:

1. **Upload/batch amount overflow (finding 1).** New `is_plausible_statement_amount()` in
   `backend/currency.py` (finite and `abs <= MAX_TRANSACTION_AMOUNT`). Rows that fail it are
   dropped, like any other non-transaction line, at the two choke points every statement row
   passes through:
   - the end of `parse_transactions` (`backend/parser.py`), which covers upload and the batch upload fallback;
   - `parse_statement_with_fallback` (`backend/batch_import/importer.py`), which covers all batch
     parsers. The old body is now `_parse_with_upload_fallback`, unchanged.

   `parse_amount` itself is unchanged, because about a dozen callers would otherwise need new
   error handling. The only other caller, `diagnose_pdf_merchants.py`, is read-only diagnostics.
2. **Overlapping-upload caveat (finding 2).** Documented in `docs/data-lifecycle.md`.
3. **Message casing (finding 3).** Bulk import now says `Row N: Amount is too large.` (#97's exact text).
4. **`got inf` (finding 4).** `_parse_json_payload` calls `json.loads(parse_float=_json_float,
   parse_constant=…)`, so non-finite JSON numbers (`NaN`, `Infinity`, `-Infinity`, `1e400`) reach
   `_parse_amount_value` as their typed text. The error now reads `got '1e400'`. Finite floats are unchanged.
5. **Sub-cent amounts (finding 5).** Postgres rounds half away from zero, so `abs(amount) < 0.005`
   would be stored as 0.00. Bulk import rejects it with the existing `amount must be non-zero.`, and
   manual entry with the existing `Amount must be positive.` A value of `0.005` is kept (stored as 0.01).

Tests added (12): the parser and batch reference-number tests (`test_parser.py`,
`test_transaction_source.py`); an **endpoint** test,
`test_batch_import_api.py::test_upload_skips_a_line_ending_in_a_reference_number`, which
monkeypatches `_load_uploaded_statement` to run the real `parse_transactions`; JSON-echo,
round-to-zero, and half-cent tests in `test_bulk_import.py`; and a round-to-zero manual test in `test_api.py`.

Verification (real results): `python3.12 -m pytest -q` gives **265 passed** (253 + 12).
Mutation checks: each fix was reverted on its own and its tests failed, and each file was restored
byte-identical (checked with `cmp`). Without the plausibility filter, the upload endpoint test
reproduces `psycopg NumericValueOutOfRange` at `/upload`, which confirms the original bug.
The frontend was not re-run in this session because no frontend file changed since the review
(52 unit, build, 35 e2e passed then).

## Outstanding issues

- No open bugs. All review findings are fixed.
- Data, not code: rows stored before #98 keep their wrong merchant and old key. The new report
  lists them; cleanup is a separate, owner-approved step (per the issue).
- Pre-existing, not touched: `_parse_amount_value` treats JSON `true` as 1 (bool is an int in Python).

## Next steps

1. Check CI with `env -u GH_TOKEN -u GITHUB_TOKEN gh pr checks <PR#>`. CI (`.github/workflows/ci.yml`)
   runs backend pytest on Postgres 16, then the frontend unit tests, build and Playwright.
   If something fails, read the log (`gh run view <run-id> --log-failed`) and reproduce it locally
   before changing anything.
2. Read review comments with
   `env -u GH_TOKEN -u GITHUB_TOKEN gh api repos/henrybro64/personal_spending_tracker/pulls/<PR#>/comments`.
3. Any further commit or push needs the owner's explicit approval (each push costs Actions minutes).
   Merging also needs the owner's go-ahead.

## Related GitHub issues/PRs

- #96, #97, #98 (this task); parent #95. Builds on #73 (source keys) and #85 (check-spending-data).
- Previous work: #101 / PR #102, merged to `main` as `1da5e932`.

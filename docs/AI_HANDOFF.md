# AI Agent Handoff

_Last updated: 2026-11-02 by Claude Code (Opus 5.5)_

## Task objective

Implement GitHub issue **#99** ("[P2] Pasted import: amounts with a thousands separator are
silently misread", part of #95) on a new working branch, double-checking the work.
Owner's constraints:

- **Do not push** to GitHub until the owner says so (saves GitHub Actions minutes).
- Do not commit, merge, or deploy without explicit instruction.

## Current status

**Done and verified locally.** Implemented; reviewed in a second session (changes requested);
review findings 1 and 2 fixed in a third session at the owner's request ("fix all the problems").
See "Review fixes". The owner approved commit + push + PR: one commit on `fix/issue-99`, with a PR open against `main`. Waiting on CI.

## Git context

- Branch `fix/issue-99`, created from `main` at `4bf8e230` (Merge PR #103).
- One commit with all the work (including this file), pushed to `origin/fix/issue-99`.
  Find the PR with `env -u GH_TOKEN -u GITHUB_TOKEN gh pr list --head fix/issue-99`.
  Check the local state with `git log --oneline main..fix/issue-99` and `git status`.

## Reading the issue

`env -u GH_TOKEN -u GITHUB_TOKEN gh issue view 99 --json title,body` (the shell's `GH_TOKEN`
is invalid; see `AGENTS.md`).

## Changes made

`backend/bulk_import.py` (text format only; JSON import is unchanged):
- `_csv_fields(line, line_number)`: `csv.reader([line], skipinitialspace=True, strict=True)`, with
  each field stripped. A `csv.Error` becomes `BulkImportParseError("Line N has a misplaced or
  unmatched quote. Quote a whole field, for example \"IKEA, Taipei\".")`.
- `_parse_comma_separated_line` uses `_csv_fields`, so `2026-05-13,IKEA,"1,234"` gives 1234 and
  `2026-05-13,"IKEA, Taipei",Shopping,"1,234.50"` keeps the merchant's comma. It rejects lines with
  **more than 4 fields**, or **4 fields where field 3 matches `[-+]?\$?\d{1,3}` and field 4 matches
  `\d{3}(\.\d+)?`**, with
  `Line N: the amount looks like it contains a thousands separator. Quote it ("1,234") or remove the comma (1234).`
- `_split_text_fields` (header detection) uses the same CSV rules. A malformed line simply counts as
  "not a header", so the row parse reports it with the correct line number.
- Unchanged: the tab path, the space path (`_TEXT_LINE_PATTERN` needs whitespace right after the date,
  so comma lines never reach it), and `date,merchant,category,amount` with a non-numeric category.
- `N` is the line number in the pasted text, counting blank and `#` comment lines (from
  `enumerate(content.splitlines(), start=1)` in `_parse_text_payload`).

## Technical decisions

- **`strict=True` (deviation from the issue's sketch).** Non-strict `csv` silently rewrites malformed
  quoting: `"IK"EA"` becomes `IKEA"`, and `"1,234"x` becomes `1,234x`. That contradicts the issue's
  "don't guess" rule. None of the lines strict mode rejects parsed correctly before, so nothing that
  works today breaks. The trade-off: `"IKEA, Taipei" ,10` (a space *after* a closing quote) is
  rejected with the quote message instead of being accepted.
- **`$` is allowed in the head pattern (beyond the issue).** Without it, `2026-05-13,IKEA,$1,234` would
  still be silently stored as category `$1`, amount 234.
- No frontend change: the Data Entry page documents only the tab format
  (`frontend/app/data-entry/page.tsx:638`), and the in-app LLM prompt produces JSON.

## Testing (run 2026-11-02, real results)

| Check | Result |
|---|---|
| `python3.12 -m pytest -q` (repo root) | **284 passed** (265 on `main` + 19 new) |
| New tests against `main`'s `bulk_import.py` (negative control) | 15 fail; 8 pass on both, which are the intended regression guards (tab/space, plain category, unquoted headers) |
| Manual probe of the issue's table plus 15 edge cases | all as specified |
| `git diff --check` | clean |

New tests:
- `backend/tests/test_bulk_import.py`: the issue's 5 tests (the reject case is extended with `-1,234`,
  `$1,234`, `1,234,567`), plus line number + exact message, comma headers (including a quoted one),
  escaped `""` quotes, and malformed quotes giving an error.
- `backend/tests/test_api.py::test_bulk_import_rejects_an_unquoted_thousands_separator`:
  `POST /batch-imports/import` returns 400 with the exact message for `batch` and `dashboard`, and a
  quoted amount is stored as 1234.

Not run: the frontend suites, because no frontend file changed. CI will run them.

## Architect review (2026-11-02, second session)

**Verdict: request changes.** The issue's acceptance criteria are met (the issue's tests pass,
headers are skipped, and the message names the line and the fix), but the change leaves the same
class of silent misread in place, and one case is a new regression. No code was changed in the review.

Re-run in this session (real results): `python3.12 -m pytest -q` gives 284 passed. Working tree
unchanged from the implementation session.

Probe results (branch versus `main`, run with `parse_import_content(..., format="text")`):

| Input | `main` | branch |
|---|---|---|
| `2026-05-13,Cafe,"12,50"` (quoted decimal comma) | error | **silently 1250** (regression) |
| `2026-05-13<TAB>Cafe<TAB>12,50` and `2026-05-13 Cafe 12,50` | 1250 | 1250 (pre-existing) |
| `2026-05-13,Cafe,12,50` / `1,5` / `1,2345` / `1.234,56` | category `12`/`1`/`1`/`1.234`, wrong amount | same, **not caught** |
| `2026-05-13,Cafe,Food,Taipei,12` (5 fields) | category dropped | rejected (intended) |

Findings:
1. **Should fix (regression): a quoted decimal comma is multiplied by 100.** Quoting now works, and
   the new error message tells users to quote. But `_parse_amount_value` (`backend/bulk_import.py`)
   deletes *every* comma (`.replace(",", "")`), so `"12,50"` becomes 1250 and `"1,23,456"` becomes
   123456. The same root cause affects the tab/space formats and JSON string amounts.
   **Fix:** strip commas only when they form valid thousands grouping, for example
   `re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", text)`. Any other comma becomes
   `BulkImportParseError("amount '12,50' has a comma that is not a thousands separator. Use a dot
   for decimals (12.50).")`, which gets the existing `Row N:` prefix. Check the existing tests that
   use `1,234` (tab/space/quoted); they must still give 1234.
2. **Should fix (gap): the split-amount rule only catches a 3-digit tail.** On a 4-field comma line,
   a third field that is a bare number is never a real category. Broaden the rule to: field 3 matches
   `[-+]?\$?\d+(?:\.\d+)?` **and** field 4 matches `\d+(?:\.\d+)?`, then reject. Generalize the
   message so it doesn't advise "remove the comma" for a decimal comma, while keeping the word
   "Quote" (the issue's test matches `(?i)quote`). For example: `Line N: the amount looks like it
   was split at a comma. Quote it ("1,234") or remove the thousands comma (1234); use a dot for
   decimals (12.50).` Update the exact-message tests in `test_bulk_import.py` and `test_api.py`.
   Keep `2026-05-13,IKEA,Food,1234` and `Shopping,234` working.
3. **Agree with the other decisions:** `strict=True` CSV (stray quotes give an error with a line
   number), the `$` head, header detection sharing the CSV rules, the >4-field rejection (the in-app LLM
   prompt is JSON-only, and there is no CSV export users would paste back), and no frontend change.
4. **Nit, pre-existing:** text-format errors say `Line N` (the line in the paste), but row-validation errors
   say `Row N` (the data row index). Both are correct but can differ by the number of header, blank and
   comment lines. Optional; out of scope.

## Review fixes (2026-11-02, third session)

Both should-fix findings are fixed in `backend/bulk_import.py`:

1. **Decimal comma ×100 (finding 1).** In `_parse_amount_value`, a string amount containing a comma
   must fully match `_THOUSANDS_GROUPED_AMOUNT` (`[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?`). Only then are
   the commas removed. Anything else raises `Row N: amount '12,50' has a comma that is not a thousands
   separator. Use a dot for decimals (12.50).` This covers the quoted, tab, space and JSON-string paths.
2. **Narrow split rule (finding 2).** It is renamed to `_SPLIT_AMOUNT_HEAD` (`[-+]?\$?\d+(?:\.\d+)?`) and
   `_SPLIT_AMOUNT_TAIL` (`\d+(?:\.\d+)?`). A 4-field comma line whose 3rd and 4th fields are both bare
   numbers is rejected, which also catches `12,50`, `1,5`, `1,2345` and `1.234,56`. The message
   (`_SPLIT_AMOUNT_HINT`) is now `Line N: the amount looks like it was split at a comma. Quote it
   ("1,234") or remove the thousands comma (1234); use a dot for decimals (12.50).`
   It no longer advises "remove the comma" for a decimal comma.

Nit 4 (`Line N` versus `Row N`) is **deliberately not changed**. It predates this work, it's out of
scope, and changing it would alter existing user-facing messages that other tests rely on
(for example `Row 1: Currency must be one of: …`).

Tests: the two exact-message assertions are updated (`test_bulk_import.py` `SPLIT_AMOUNT_HINT`, and
`test_api.py::test_bulk_import_rejects_an_unquoted_thousands_separator`). There are 18 new cases:
4 split inputs added to the reject list; `"12,50"` rejected in the quoted, tab, space and JSON formats;
bad groupings (`1,23,456`, `12,3456`, `1,234,56`, `,123`); valid grouping (`1,234`, `-1,234.50`,
`1,234,567.89`) still parses in all four formats; and categories with digits (`Food 2`, `7-Eleven`)
are not treated as split amounts.

Verification (real results): `python3.12 -m pytest -q` gives **302 passed** (284 + 18). Mutation checks:
reverting finding 1 alone fails the 8 decimal-comma and bad-grouping cases, and reverting finding 2
alone fails the 4 new split cases. The guards pass under both mutations. The file was restored
byte-identical (`cmp`). `git diff --check` is clean. The frontend was not run because no frontend
file changed.

## Outstanding issues

- None known. (Nit 4 above is intentionally out of scope.)

## Next steps

1. Check CI with `env -u GH_TOKEN -u GITHUB_TOKEN gh pr checks <PR#>`. If something fails, read the
   log (`gh run view <run-id> --log-failed`) and reproduce it locally before changing anything.
2. Read review comments with
   `env -u GH_TOKEN -u GITHUB_TOKEN gh api repos/henrybro64/personal_spending_tracker/pulls/<PR#>/comments`.
3. Any further commit, push or merge needs the owner's explicit approval (each push costs Actions minutes).

## Related GitHub issues/PRs

- #99 (this task); parent #95.
- Previous work, merged: PR #103 (#96 proxy errors, #97 non-finite amounts, #98 posting dates)
  as `4bf8e230`; PR #102 (#101 k3s Postgres) as `1da5e932`. #97 added `MAX_TRANSACTION_AMOUNT` and
  the finiteness checks in `_parse_amount_value`, which also apply to amounts parsed here.

# MVP Roadmap And Hardening Order

This document mirrors the intent of GitHub issue
[#13](https://github.com/henrybro64/personal_spending_tracker/issues/13) so the
implementation order is visible from the local checkout.

GitHub issue titles use `[MVP First]`, `[Later Hardening]`, and `[Roadmap]`
prefixes so the backlog lanes are visible in the issue list without depending
on labels or milestones.

## Use This Roadmap For

- keeping the core MVP upload, parser, test, and deployment lane separate from
  later hardening work
- giving future implementation prompts one place to reference for intended order
- avoiding cross-repo confusion for currency-label work that belongs only to the
  spending tracker

## MVP-Stabilization Work First

These are the first tasks before the later hardening lane:

1. [#6 Separate statement upload metadata from transactions](https://github.com/henrybro64/personal_spending_tracker/issues/6)
2. [#7 Add minimal parser/API tests and CI checks for the MVP](https://github.com/henrybro64/personal_spending_tracker/issues/7)
3. [#10 Add minimal Playwright frontend E2E smoke tests](https://github.com/henrybro64/personal_spending_tracker/issues/10)
4. [#5 Set up a minimal single-node k3s deployment for the MVP](https://github.com/henrybro64/personal_spending_tracker/issues/5)
5. [#3 Implement batch statement importer with bank-specific parsers and deterministic categorization](https://github.com/henrybro64/personal_spending_tracker/issues/3)

## Later MVP Hardening Lane

These should follow the MVP-stabilization work above.

1. [#11 Introduce explicit database migrations for schema changes](https://github.com/henrybro64/personal_spending_tracker/issues/11)
   Move schema changes out of request paths before the data model grows further.
2. [#4 Clarify spending currency labels in dashboard and transaction views](https://github.com/henrybro64/personal_spending_tracker/issues/4)
   Make UI totals and transaction amounts clearly distinguish display currency
   from original statement currency.
3. [#12 Harden spending currency and exchange-rate handling](https://github.com/henrybro64/personal_spending_tracker/issues/12)
   Separate original amount/currency from display amount/currency and document
   the exchange-rate behavior explicitly.
4. [#37 Add backup, restore, and recovery workflow for PostgreSQL and uploaded statements](https://github.com/henrybro64/personal_spending_tracker/issues/37)
   Make database and uploaded-file recovery explicit before treating the app as
   a durable long-term tracker.
5. [#38 Define retention and privacy handling for uploaded statements and audit records](https://github.com/henrybro64/personal_spending_tracker/issues/38)
   Make data-lifecycle, logging, cleanup, and deletion expectations explicit
   before broader audit and ownership work grows further.
6. [#2 Support multi-user accounts with per-user data isolation and audit logging](https://github.com/henrybro64/personal_spending_tracker/issues/2)
   Treat this as a later hardening step after schema and currency behavior are
   better defined.

## Hardening Implementation Notes

The hardening lane is implemented on branch `feature-mvp-hardening-13`:

- [#11](https://github.com/henrybro64/personal_spending_tracker/issues/11):
  versioned SQL migrations in `backend/migrations/` applied by
  `python -m backend.migrate` / `scripts/migrate-db.sh`
- [#4](https://github.com/henrybro64/personal_spending_tracker/issues/4) and
  [#12](https://github.com/henrybro64/personal_spending_tracker/issues/12):
  dashboard/transactions currency labels plus `docs/currency.md`
- [#37](https://github.com/henrybro64/personal_spending_tracker/issues/37):
  `scripts/backup-spending-tracker.sh`, `scripts/restore-spending-tracker.sh`,
  `docs/backup-restore.md`
- [#38](https://github.com/henrybro64/personal_spending_tracker/issues/38):
  retention env vars, `scripts/purge-expired-data.sh`, `docs/data-lifecycle.md`
- [#2](https://github.com/henrybro64/personal_spending_tracker/issues/2):
  per-user batch source dirs, expanded audit events, `DELETE /uploads/{id}`

## Notes

- Issue [#4](https://github.com/henrybro64/personal_spending_tracker/issues/4)
  is spending-tracker-only work. Portfolio or stock-app currency-label concerns
  should stay in the separate repository.
- Issue [#13](https://github.com/henrybro64/personal_spending_tracker/issues/13)
  is the umbrella tracker for the later hardening lane, not the place to
  implement the underlying feature work directly.

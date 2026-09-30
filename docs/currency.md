# Currency And Exchange Rates

The spending tracker stores each transaction in its original statement
currency (`transactions.original_amount` / `transactions.original_currency`,
with legacy fallbacks to `transactions.amount` / `transactions.currency`).
Dashboard totals can be viewed in a selected display currency (`USD` or
`TWD`).

Manual entries and imported rows can also store a normalized base amount in
TWD (`transactions.base_amount`, `transactions.base_currency`) plus an
optional user-supplied exchange rate and rate date.

## Supported original currencies

Manual entry accepts these original currency codes:

- `TWD`, `USD`, `JPY`, `EUR`, `CNY`, `HKD`, `PHP`, `SGD`, `KRW`, `GBP`

The base currency for manual entry is currently fixed to `TWD`. When the
original currency differs from TWD, the user may provide an exchange rate
(`original_amount * exchange_rate = base_amount`). If no rate is supplied,
the row is stored with `base_amount = NULL`.

Dashboard display conversion is USD/TWD only. The ten codes above are
original currencies, not extra display currencies.

## Spending totals

`/summary` and `/dashboard/view` share one spending pipeline:

- **Model.** Transactions in the requested date range that are not bill
  payments (`繳款` or category `Payment / Credit`) and that have a USD or
  TWD display amount. `summary.total_spending`,
  `summary.number_of_transactions`, `summary.average_transaction_amount`,
  and `metrics.model_total_in_active_currency` all use this set. Inside one
  `/dashboard/view` response those totals match.
- **Selection.** The model after search, amount-direction, and merchant
  filters. `metrics.selection_*`, charts, and category/merchant/date
  breakdowns use the selection. Those filters do not change the model total.
  The requested date range does: both the model and the selection are built
  from rows inside that range.
- **Unconverted.** Spending rows that are not USD or TWD and have no USD or
  TWD base amount. They are counted in
  `summary.unconverted_transaction_count`, and their original currencies are
  listed in `summary.unconverted_currencies`. A sample is returned in
  `unconverted_transactions` with reason `unsupported_display_conversion`.
  They are left out of the money totals. They are not dropped silently. The
  dashboard shows a warning for them.

USD and TWD always convert, using the static rate below when the row has no
stored base amount. A stored USD or TWD `base_amount` is converted instead
of re-applying that rate to the original amount, so a user-supplied rate is
kept. Other declared currencies join the model only through that base.

`/summary` and `/dashboard/view` scan every row in the date range so the
model total stays complete. The scan streams one row at a time and keeps the
bill-payment rule in Python. The dashboard `limit` (default 5000) caps
`transactions`, `sorted_transactions`, and the `unconverted_transactions`
sample. Totals, metrics, charts, `unconverted_transaction_count`, and
`unconverted_currencies` still use the full scan, so a small limit cannot
drop a spending row or hide that an unconverted currency exists.

The transactions page is the original-currency ledger. It still lists bill
payments. Those payments stay out of the dashboard and summary spending totals.

AI insights use this same partition. The snapshot's display currency is USD
or TWD, whichever has the larger home-currency total. A stored USD or TWD
base amount is that home amount. Rows that cannot be converted are left out
of the insight totals and listed on
`totals.unconverted_transaction_count` and `totals.unconverted_currencies`.

## Statement count

`number_of_statements` counts rows in `statement_uploads` for the current
user. The count is all-time: it does not follow the transaction date range,
and it does not depend on transaction rows. An upload with
`parse_status = no_transactions` still counts. Other users' uploads do not.
`summary.statement_count_scope` is `all_time`.

## Display vs original currency

- Dashboard cards and charts use the active display currency. USD and TWD
  source amounts are converted for comparison. Other currencies stay out of
  those totals until they have an exchange rate into TWD (stored as a TWD
  base amount).
- The transactions page keeps row amounts in the original statement currency
  and groups mixed-currency totals by original currency.

## Exchange rate

Conversion between USD and TWD uses a single static rate:

- Default: `32.0`
- Override with `USD_TWD_EXCHANGE_RATE` in the backend environment
- API responses include `exchange_rate.usd_twd_rate` and
  `exchange_rate.rate_source` (`static_env`) on `/dashboard/view`

Manual/import exchange rates are stored per transaction and are separate
from the static USD/TWD display conversion rate. There is no historical
rate lookup and no rate table for JPY, EUR, or the other original
currencies.

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
the row is stored with `base_amount = NULL` and is excluded from converted
dashboard/summary totals until a rate is available.

## Display vs original currency

- Dashboard and `/dashboard/view` convert rows into the active display
  currency when a base amount is available, or when the original currency
  already matches the display currency. Rows without a convertible amount are
  omitted from converted totals rather than guessed.
- The transactions page keeps row amounts in the original statement currency
  and groups mixed-currency totals by original currency.

## Exchange rate

Conversion for dashboard display uses a single static USD/TWD rate:

- Default: `32.0`
- Override with `USD_TWD_EXCHANGE_RATE` in the backend environment
- API responses include `exchange_rate.usd_twd_rate` and
  `exchange_rate.rate_source` (`static_env`) on `/dashboard/view`

Manual/import exchange rates are stored per transaction and are separate
from the static USD/TWD display conversion rate.

There is no historical rate lookup. Mixed-currency summary totals convert
per currency group before summing.

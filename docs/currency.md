# Currency And Exchange Rates

The spending tracker stores each transaction in its original statement
currency (`transactions.currency` and `transactions.amount`). Dashboard
totals can be viewed in a selected display currency (`USD` or `TWD`).

## Display vs original currency

- Dashboard and `/dashboard/view` convert row amounts into the active display
  currency and return both `original_*` and `display_*` fields.
- The transactions page keeps row amounts in the original statement currency
  and groups mixed-currency totals by original currency.

## Exchange rate

Conversion uses a single static USD/TWD rate:

- Default: `32.0`
- Override with `USD_TWD_EXCHANGE_RATE` in the backend environment
- API responses include `exchange_rate.usd_twd_rate` and
  `exchange_rate.rate_source` (`static_env`) on `/dashboard/view`

There is no historical rate lookup. Mixed-currency summary totals always convert
per currency group before summing.

"use client";

export type SupportedCurrency = "USD" | "TWD";

export type CurrencyAmount = {
  amount: number;
  currency?: string;
};

export type CurrencyTotal = {
  currency: SupportedCurrency;
  total: number;
};

const currencySortOrder: Record<SupportedCurrency, number> = {
  USD: 0,
  TWD: 1,
};

export function normalizeCurrencyCode(currencyCode?: string): SupportedCurrency {
  return currencyCode?.toUpperCase() === "TWD" ? "TWD" : "USD";
}

export function currencySymbol(currencyCode?: string): string {
  return normalizeCurrencyCode(currencyCode) === "TWD" ? "NT$" : "US$";
}

export function describeCurrency(currencyCode?: string): string {
  const normalizedCurrency = normalizeCurrencyCode(currencyCode);
  return `${currencySymbol(normalizedCurrency)} (${normalizedCurrency})`;
}

export function formatCurrencyAmount(
  amount: number,
  currencyCode: string | undefined,
  intlLocale: string
): string {
  const normalizedCurrency = normalizeCurrencyCode(currencyCode);
  const formattedAmount = new Intl.NumberFormat(intlLocale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(amount));
  const prefix = currencySymbol(normalizedCurrency);
  return amount < 0 ? `-${prefix}${formattedAmount}` : `${prefix}${formattedAmount}`;
}

export function summarizeCurrencyTotals(
  amounts: CurrencyAmount[]
): CurrencyTotal[] {
  const totalsByCurrency = new Map<SupportedCurrency, number>();

  amounts.forEach(({ amount, currency }) => {
    const normalizedCurrency = normalizeCurrencyCode(currency);
    totalsByCurrency.set(
      normalizedCurrency,
      (totalsByCurrency.get(normalizedCurrency) ?? 0) + amount
    );
  });

  return Array.from(totalsByCurrency.entries())
    .map(([currency, total]) => ({ currency, total }))
    .sort(
      (left, right) =>
        currencySortOrder[left.currency] - currencySortOrder[right.currency]
    );
}

export function formatCurrencyTotals(
  totals: CurrencyTotal[],
  intlLocale: string
): string {
  const sortedTotals = [...totals].sort(
    (left, right) =>
      currencySortOrder[left.currency] - currencySortOrder[right.currency]
  );

  if (sortedTotals.length === 0) {
    return formatCurrencyAmount(0, "USD", intlLocale);
  }

  if (sortedTotals.length === 1) {
    return formatCurrencyAmount(
      sortedTotals[0].total,
      sortedTotals[0].currency,
      intlLocale
    );
  }

  return sortedTotals
    .map(
      ({ currency, total }) =>
        `${currency}: ${formatCurrencyAmount(total, currency, intlLocale)}`
    )
    .join(" · ");
}

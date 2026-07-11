"use client";

export type SupportedCurrency =
  | "TWD"
  | "USD"
  | "JPY"
  | "EUR"
  | "CNY"
  | "HKD"
  | "PHP"
  | "SGD"
  | "KRW"
  | "GBP";

export const supportedCurrencies: SupportedCurrency[] = [
  "TWD",
  "USD",
  "JPY",
  "EUR",
  "CNY",
  "HKD",
  "PHP",
  "SGD",
  "KRW",
  "GBP",
];

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
  JPY: 2,
  EUR: 3,
  CNY: 4,
  HKD: 5,
  PHP: 6,
  SGD: 7,
  KRW: 8,
  GBP: 9,
};

export function normalizeCurrencyCode(currencyCode?: string): SupportedCurrency {
  const normalizedCurrency = currencyCode?.trim().toUpperCase();
  return supportedCurrencies.includes(normalizedCurrency as SupportedCurrency)
    ? (normalizedCurrency as SupportedCurrency)
    : "TWD";
}

export function currencySymbol(currencyCode?: string): string {
  const normalizedCurrency = normalizeCurrencyCode(currencyCode);
  if (normalizedCurrency === "TWD") {
    return "NT$";
  }
  if (normalizedCurrency === "USD") {
    return "US$";
  }
  return normalizedCurrency;
}

export function describeCurrency(currencyCode?: string): string {
  const normalizedCurrency = normalizeCurrencyCode(currencyCode);
  const symbol = currencySymbol(normalizedCurrency);
  return symbol === normalizedCurrency
    ? normalizedCurrency
    : `${symbol} (${normalizedCurrency})`;
}

export function formatCurrencyAmount(
  amount: number,
  currencyCode: string | undefined,
  intlLocale: string
): string {
  const normalizedCurrency = normalizeCurrencyCode(currencyCode);
  const maximumFractionDigits =
    normalizedCurrency === "JPY" || normalizedCurrency === "KRW" ? 0 : 2;
  const formattedAmount = new Intl.NumberFormat(intlLocale, {
    minimumFractionDigits: maximumFractionDigits,
    maximumFractionDigits,
  }).format(Math.abs(amount));
  const prefix = currencySymbol(normalizedCurrency);
  const separator =
    normalizedCurrency === "USD" || normalizedCurrency === "TWD" ? "" : " ";
  return amount < 0
    ? `-${prefix}${separator}${formattedAmount}`
    : `${prefix}${separator}${formattedAmount}`;
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

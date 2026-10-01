"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Badge,
  Body1,
  Button,
  Card,
  Field,
  Input,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Title1,
  Title2,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { apiFetch, apiFetchJson } from "../api";
import {
  DashboardRequestCoordinator,
  recoverableReadFailure,
} from "../dashboard-refresh";
import { useReadRecovery } from "../read-recovery";
import { invalidateSpendingData, useSpendingRevision } from "../server-state";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import {
  describeCurrency,
  formatCurrencyAmount,
  normalizeCurrencyCode,
  summarizeCurrencyTotals,
  type CurrencyTotal,
  type SupportedCurrency,
} from "../currency";
import { useLocale } from "../locale-provider";
import { translate } from "../messages";

type Transaction = {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  category: string;
  currency?: SupportedCurrency;
  original_amount?: number;
  original_currency?: SupportedCurrency;
  base_amount?: number | null;
  base_currency?: SupportedCurrency;
  exchange_rate?: number | null;
  exchange_rate_date?: string | null;
};

type TransactionsResponse = {
  transactions: Transaction[];
};

type TransactionUpdateResponse = {
  transaction: Transaction;
};

type AmountFilter = "all" | "spend" | "refunds";
type SortOption = "amount_desc" | "amount_asc" | "merchant" | "date";
type TimeRange =
  | "week"
  | "month"
  | "quarter"
  | "6months"
  | "year"
  | "5years"
  | "all";
type MonthGroup = {
  monthKey: string;
  monthLabel: string;
  transactions: Transaction[];
  subtotal: number;
};
type YearGroup = {
  year: string;
  months: MonthGroup[];
};

const transactionCategories = [
  "Transportation",
  "Shopping / Department Store",
  "Shopping / Retail",
  "Shopping / Online Retail",
  "Digital Services",
  "Dining",
  "Dining / Food",
  "Groceries",
  "Travel",
  "Utilities / Telecom",
  "Healthcare / Medical",
  "Subscription / Membership",
  "Insurance",
  "Education / Certification",
  "Tax / Accounting",
  "Government / Consular",
  "Bank Fees / Interest",
  "Merchant Missing From PDF",
  "Cash & Fees",
  "Payment / Credit",
  "Refunds & Credits",
  "Other Spending",
] as const;

const useStyles = makeStyles({
  page: {
    minHeight: "100dvh",
    backgroundColor: "var(--page-surface)",
  },
  shell: {
    maxWidth: "1480px",
    marginLeft: "auto",
    marginRight: "auto",
    paddingTop: "max(20px, calc(env(safe-area-inset-top) + 16px))",
    paddingBottom: "max(28px, calc(env(safe-area-inset-bottom) + 20px))",
    paddingLeft: "max(20px, calc(env(safe-area-inset-left) + 16px))",
    paddingRight: "max(20px, calc(env(safe-area-inset-right) + 16px))",
    display: "grid",
    gridTemplateColumns: "280px minmax(0, 1fr)",
    gap: "16px",
    alignItems: "start",
    "@media (min-width: 1280px)": {
      gridTemplateColumns: "320px minmax(0, 1fr)",
      gap: "20px",
    },
    "@media (max-width: 959px)": {
      gridTemplateColumns: "1fr",
    },
    "@media (max-width: 639px)": {
      paddingLeft: "max(16px, calc(env(safe-area-inset-left) + 12px))",
      paddingRight: "max(16px, calc(env(safe-area-inset-right) + 12px))",
    },
  },
  sidebar: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    position: "sticky",
    top: "max(16px, calc(env(safe-area-inset-top) + 8px))",
    alignSelf: "start",
    "@media (max-width: 959px)": {
      position: "static",
    },
  },
  reportArea: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    minWidth: 0,
  },
  header: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
    marginBottom: "12px",
  },
  headerNote: {
    color: tokens.colorNeutralForeground2,
  },
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05), 0 10px 28px rgba(0, 0, 0, 0.06)",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusXLarge),
  },
  tableWrap: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  yearSection: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    paddingTop: "8px",
    paddingBottom: "8px",
  },
  yearTitle: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground1,
  },
  monthSection: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    paddingTop: "8px",
    paddingBottom: "8px",
    paddingLeft: "8px",
    ...shorthands.borderLeft("3px", "solid", tokens.colorBrandStroke1),
  },
  monthHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "8px",
    flexWrap: "wrap",
  },
  monthTitle: {
    fontSize: tokens.fontSizeBase300,
    fontWeight: tokens.fontWeightSemibold,
  },
  monthSubtotal: {
    color: tokens.colorNeutralForeground2,
    fontWeight: tokens.fontWeightSemibold,
  },
  monthTableWrap: {
    overflowX: "auto",
    WebkitOverflowScrolling: "touch",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  drilldownPanel: {
    display: "flex",
    flexDirection: "column",
    gap: "10px",
    paddingBottom: "4px",
  },
  drilldownLabel: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
  },
  tableHeaderCell: {
    backgroundColor: tokens.colorNeutralBackground2,
    fontWeight: tokens.fontWeightSemibold,
  },
  tableCell: {
    fontSize: tokens.fontSizeBase300,
  },
  amountNegative: {
    color: "#d13438",
    fontWeight: tokens.fontWeightSemibold,
  },
  amountPositive: {
    color: tokens.colorNeutralForeground1,
    fontWeight: tokens.fontWeightSemibold,
  },
  amountCell: {
    display: "flex",
    flexDirection: "column",
    gap: "2px",
  },
  amountMeta: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
  },
  rowAlt: {
    backgroundColor: tokens.colorNeutralBackground2,
  },
  sidebarPanel: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  sectionEyebrow: {
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: tokens.colorNeutralForeground3,
  },
  sidebarNavLinks: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  sidebarNavLink: {
    display: "block",
    paddingTop: "12px",
    paddingBottom: "12px",
    paddingLeft: "12px",
    paddingRight: "12px",
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground2,
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    ":hover": {
      backgroundColor: tokens.colorSubtleBackgroundHover,
      color: tokens.colorNeutralForeground1,
    },
  },
  sidebarNavLinkActive: {
    backgroundColor: tokens.colorBrandBackground2,
    color: tokens.colorBrandForeground2,
  },
  filterHint: {
    color: tokens.colorNeutralForeground3,
  },
  slicerGroup: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  pillGrid: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  slicerButtons: {
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: "8px",
  },
  emptySlicerState: {
    color: tokens.colorNeutralForeground3,
  },
  actions: {
    display: "flex",
    gap: "8px",
    alignItems: "center",
    flexWrap: "wrap",
  },
  selectControl: {
    width: "100%",
    height: "40px",
    paddingLeft: "12px",
    paddingRight: "12px",
    fontSize: tokens.fontSizeBase300,
    color: tokens.colorNeutralForeground1,
    backgroundColor: tokens.colorNeutralBackground1,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
  },
  categoryControl: {
    minWidth: "180px",
    height: "40px",
    fontSize: tokens.fontSizeBase300,
  },
  merchantControl: {
    minWidth: "200px",
    width: "100%",
    height: "40px",
    fontSize: tokens.fontSizeBase300,
  },
});

function formatCurrencySummary(
  totals: CurrencyTotal[],
  intlLocale: string
): string {
  if (totals.length === 0) {
    return formatCurrencyAmount(0, "USD", intlLocale);
  }

  if (totals.length === 1) {
    return formatCurrencyAmount(totals[0].total, totals[0].currency, intlLocale);
  }

  return totals
    .map(
      ({ currency, total }) =>
        `${currency}: ${formatCurrencyAmount(total, currency, intlLocale)}`
    )
    .join(" · ");
}

function parseIsoDate(dateValue: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateValue);
  if (!match) {
    return null;
  }
  const [, yearText, monthText, dayText] = match;
  const parsedDate = new Date(
    Number(yearText),
    Number(monthText) - 1,
    Number(dayText)
  );
  return Number.isNaN(parsedDate.getTime()) ? null : parsedDate;
}

function isInTimeRange(dateValue: string, range: TimeRange) {
  if (range === "all") {
    return true;
  }
  const parsedDate = parseIsoDate(dateValue);
  if (!parsedDate) {
    return false;
  }
  const now = new Date();
  const threshold = new Date(now);
  if (range === "week") threshold.setDate(now.getDate() - 7);
  if (range === "month") threshold.setMonth(now.getMonth() - 1);
  if (range === "quarter") threshold.setMonth(now.getMonth() - 3);
  if (range === "6months") threshold.setMonth(now.getMonth() - 6);
  if (range === "year") threshold.setFullYear(now.getFullYear() - 1);
  if (range === "5years") threshold.setFullYear(now.getFullYear() - 5);
  return parsedDate >= threshold && parsedDate <= now;
}

function buildGroupedTransactions(
  transactions: Transaction[],
  locale: string
): YearGroup[] {
  const yearsMap = new Map<string, Map<string, Transaction[]>>();

  transactions.forEach((transaction) => {
    const parsedDate = parseIsoDate(transaction.date);
    const year = parsedDate ? String(parsedDate.getFullYear()) : "Unknown";
    const monthKey = parsedDate
      ? `${parsedDate.getFullYear()}-${String(parsedDate.getMonth() + 1).padStart(2, "0")}`
      : "Unknown";
    const monthsMap = yearsMap.get(year) ?? new Map<string, Transaction[]>();
    const bucket = monthsMap.get(monthKey) ?? [];
    bucket.push(transaction);
    monthsMap.set(monthKey, bucket);
    yearsMap.set(year, monthsMap);
  });

  return Array.from(yearsMap.entries())
    .sort(([yearA], [yearB]) => yearB.localeCompare(yearA))
    .map(([year, monthsMap]) => {
      const months = Array.from(monthsMap.entries())
        .sort(([monthA], [monthB]) => monthB.localeCompare(monthA))
        .map(([monthKey, monthTransactions]) => {
          let monthLabel = monthKey;
          if (monthKey !== "Unknown") {
            const parsedYear = Number(monthKey.slice(0, 4));
            const parsedMonth = Number(monthKey.slice(5, 7));
            monthLabel = new Intl.DateTimeFormat(locale, {
              month: "long",
              year: "numeric",
            }).format(new Date(parsedYear, parsedMonth - 1, 1));
          }
          return {
            monthKey,
            monthLabel,
            transactions: monthTransactions,
            subtotal: monthTransactions.reduce((sum, item) => sum + item.amount, 0),
          };
        });
      return { year, months };
    });
}

export default function TransactionsPage() {
  const styles = useStyles();
  const { locale, intlLocale } = useLocale();
  const { refreshSession, status, user } = useAuth();
  const pathname = usePathname();
  const spendingRevision = useSpendingRevision();
  const localeRef = useRef(locale);
  localeRef.current = locale;
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const transactionsRef = useRef(transactions);
  transactionsRef.current = transactions;
  const [hasLoaded, setHasLoaded] = useState(false);
  const hasLoadedRef = useRef(false);
  const [failureId, setFailureId] = useState(0);
  const [recoveryTick, setRecoveryTick] = useState(0);
  const { retryNow, resetCycle } = useReadRecovery(
    status === "authenticated",
    failureId,
    () => setRecoveryTick((tick) => tick + 1)
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [amountFilter, setAmountFilter] = useState<AmountFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("amount_desc");
  const [selectedMerchant, setSelectedMerchant] = useState<string | null>(null);
  const [selectedYear, setSelectedYear] = useState<string | null>(null);
  const [selectedMonthKey, setSelectedMonthKey] = useState<string | null>(null);
  const [savingTransactionId, setSavingTransactionId] = useState<number | null>(null);
  const [deletingTransactionId, setDeletingTransactionId] = useState<number | null>(null);
  const [categoryDrafts, setCategoryDrafts] = useState<Record<number, string>>({});
  const [merchantDrafts, setMerchantDrafts] = useState<Record<number, string>>({});
  const requestCoordinatorRef = useRef(new DashboardRequestCoordinator());

  useEffect(() => {
    resetCycle();
  }, [resetCycle, spendingRevision, status]);

  useEffect(() => {
    if (status !== "authenticated") {
      hasLoadedRef.current = false;
      setHasLoaded(false);
      setTransactions([]);
      setFailureId(0);
      setError("");
      setLoading(status === "loading");
      return;
    }

    const coordinator = requestCoordinatorRef.current;
    const ticket = coordinator.start();
    if (!hasLoadedRef.current) {
      setLoading(true);
    }
    setMessage("");

    void (async () => {
      try {
        const payload = await apiFetchJson<TransactionsResponse>(
          "/transactions?range=all&limit=5000",
          translate(localeRef.current, "errRequestFailed"),
          { signal: ticket.signal }
        );
        if (!coordinator.isLatest(ticket.generation) || ticket.signal.aborted) {
          return;
        }
        hasLoadedRef.current = true;
        setHasLoaded(true);
        setTransactions(payload.transactions);
        setError("");
        setFailureId(0);
      } catch (loadError) {
        const outcome = recoverableReadFailure(
          loadError,
          hasLoadedRef.current,
          {
            staleData: translate(localeRef.current, "dashboardStaleData"),
            unableToRefresh: translate(localeRef.current, "errUnableToRefresh"),
          },
          {
            aborted: ticket.signal.aborted,
            latest: coordinator.isLatest(ticket.generation),
          }
        );
        if (outcome.action === "ignore") {
          return;
        }
        if (outcome.action === "unauthorized") {
          setFailureId(0);
          await refreshSession();
          return;
        }
        setError(outcome.message);
        setFailureId((id) => id + 1);
      } finally {
        if (coordinator.isLatest(ticket.generation) && !ticket.signal.aborted) {
          setLoading(false);
        }
      }
    })();

    return () => {
      ticket.abort();
    };
  }, [recoveryTick, refreshSession, spendingRevision, status]);

  const merchantCandidates = useMemo(() => {
    const deduped = Array.from(
      new Set(
        transactions
          .filter((transaction) => isInTimeRange(transaction.date, timeRange))
          .map((transaction) => transaction.merchant)
      )
    );
    deduped.sort((a, b) => a.localeCompare(b));
    return deduped.slice(0, 12);
  }, [transactions, timeRange]);

  const filteredTransactions = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase();
    const filtered = transactions.filter((transaction) => {
      const matchesTimeRange = isInTimeRange(transaction.date, timeRange);
      const matchesSearch =
        !normalizedSearch ||
        transaction.merchant.toLowerCase().includes(normalizedSearch) ||
        transaction.category.toLowerCase().includes(normalizedSearch) ||
        transaction.date.toLowerCase().includes(normalizedSearch) ||
        `${transaction.amount}`.includes(normalizedSearch);
      const matchesAmount =
        amountFilter === "all" ||
        (amountFilter === "spend" && transaction.amount >= 0) ||
        (amountFilter === "refunds" && transaction.amount < 0);
      const matchesMerchant =
        !selectedMerchant || transaction.merchant === selectedMerchant;
      return matchesTimeRange && matchesSearch && matchesAmount && matchesMerchant;
    });
    const sorted = [...filtered];
    if (sortOption === "amount_asc") {
      sorted.sort((a, b) => a.amount - b.amount);
    } else if (sortOption === "merchant") {
      sorted.sort((a, b) => a.merchant.localeCompare(b.merchant));
    } else if (sortOption === "date") {
      sorted.sort((a, b) => a.date.localeCompare(b.date));
    } else {
      sorted.sort((a, b) => b.amount - a.amount);
    }
    return sorted;
  }, [transactions, timeRange, searchQuery, amountFilter, selectedMerchant, sortOption]);

  const totalsByCurrency = useMemo(
    () => summarizeCurrencyTotals(filteredTransactions),
    [filteredTransactions]
  );
  const totalAmountSummary = useMemo(
    () => formatCurrencySummary(totalsByCurrency, intlLocale),
    [totalsByCurrency, intlLocale]
  );
  const groupedTransactions = useMemo(
    () => buildGroupedTransactions(filteredTransactions, locale === "zh-Hant" ? "zh-TW" : "en-US"),
    [filteredTransactions, locale]
  );
  const activeYearGroup = useMemo(
    () => groupedTransactions.find((group) => group.year === selectedYear) ?? null,
    [groupedTransactions, selectedYear]
  );
  const activeMonthGroup = useMemo(
    () =>
      activeYearGroup?.months.find((monthGroup) => monthGroup.monthKey === selectedMonthKey) ??
      null,
    [activeYearGroup, selectedMonthKey]
  );
  const activeMonthTotalsByCurrency = useMemo(
    () =>
      activeMonthGroup
        ? summarizeCurrencyTotals(activeMonthGroup.transactions)
        : [],
    [activeMonthGroup]
  );
  const activeMonthSubtotalSummary = useMemo(
    () => formatCurrencySummary(activeMonthTotalsByCurrency, intlLocale),
    [activeMonthTotalsByCurrency, intlLocale]
  );
  const netTotalLabel =
    totalsByCurrency.length > 1
      ? translate(locale, "transactionsNetTotalByCurrency")
      : translate(locale, "transactionsNetTotal");
  const monthSubtotalLabel =
    activeMonthTotalsByCurrency.length > 1
      ? translate(locale, "transactionsSubtotalByCurrency")
      : translate(locale, "transactionsSubtotal");

  useEffect(() => {
    if (!groupedTransactions.length) {
      setSelectedYear(null);
      setSelectedMonthKey(null);
      return;
    }

    const nextYear =
      selectedYear && groupedTransactions.some((group) => group.year === selectedYear)
        ? selectedYear
        : groupedTransactions[0].year;
    setSelectedYear(nextYear);

    const nextYearGroup = groupedTransactions.find((group) => group.year === nextYear);
    if (!nextYearGroup || !nextYearGroup.months.length) {
      setSelectedMonthKey(null);
      return;
    }

    const nextMonth =
      selectedMonthKey &&
      nextYearGroup.months.some((monthGroup) => monthGroup.monthKey === selectedMonthKey)
        ? selectedMonthKey
        : nextYearGroup.months[0].monthKey;
    setSelectedMonthKey(nextMonth);
  }, [groupedTransactions, selectedYear, selectedMonthKey]);

  function draftCategory(transaction: Transaction): string {
    return categoryDrafts[transaction.id] ?? transaction.category;
  }

  function draftMerchant(transaction: Transaction): string {
    return merchantDrafts[transaction.id] ?? transaction.merchant;
  }

  async function deleteTransaction(transaction: Transaction) {
    const confirmed = window.confirm(
      `Delete this transaction?\n\n${transaction.date} · ${transaction.merchant} · ${formatCurrencyAmount(
        transaction.amount,
        transaction.currency ?? "USD",
        intlLocale
      )}`
    );
    if (!confirmed) {
      return;
    }

    setDeletingTransactionId(transaction.id);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(`/transactions/${transaction.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        const payload = (await response.json().catch(() => ({}))) as { detail?: string };
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "Failed to delete transaction."
        );
      }
      invalidateSpendingData();
      setTransactions((current) =>
        current.filter((item) => item.id !== transaction.id)
      );
      setCategoryDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMerchantDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMessage(`Deleted ${transaction.merchant}.`);
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Failed to delete transaction."
      );
    } finally {
      setDeletingTransactionId(null);
    }
  }

  async function saveTransactionEdits(transaction: Transaction) {
    const category = draftCategory(transaction).trim();
    const merchant = draftMerchant(transaction).trim();
    if (!category) {
      setError("Category is required.");
      return;
    }
    if (!merchant) {
      setError("Merchant is required.");
      return;
    }
    const categoryChanged = category !== transaction.category;
    const merchantChanged = merchant !== transaction.merchant;
    if (!categoryChanged && !merchantChanged) {
      setMessage("No changes to save.");
      return;
    }
    setSavingTransactionId(transaction.id);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(`/transactions/${transaction.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: categoryChanged ? category : undefined,
          merchant: merchantChanged ? merchant : undefined,
        }),
      });
      const payload = (await response.json()) as
        | TransactionUpdateResponse
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to update transaction."
        );
      }
      const updated = (payload as TransactionUpdateResponse).transaction;
      invalidateSpendingData();
      setTransactions((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      setCategoryDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMerchantDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMessage(`Saved ${updated.merchant}.`);
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : "Failed to update transaction."
      );
    } finally {
      setSavingTransactionId(null);
    }
  }

  if (status === "loading") {
    return (
      <main className={styles.page}>
        <div className={styles.shell}>
          <section className={styles.reportArea}>
            <Title1>Checking session</Title1>
            <Spinner />
          </section>
        </div>
      </main>
    );
  }

  if (!user) {
    return (
      <AuthPage>
        <AuthPanel
          title="Sign in required"
          body="Use a username and password to view only your own transaction history."
        />
      </AuthPage>
    );
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell} data-testid="transactions-shell">
        <aside className={styles.sidebar}>
          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.sectionEyebrow}>{translate(locale, "navTitle")}</div>
            <Title2>{translate(locale, "navTitle")}</Title2>
            <Body1 className={styles.filterHint}>{translate(locale, "navHint")}</Body1>
            <div className={styles.sidebarNavLinks}>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/" && styles.sidebarNavLinkActive
                )}
                href="/"
              >
                {translate(locale, "navDashboard")}
              </Link>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/data-entry" && styles.sidebarNavLinkActive
                )}
                href="/data-entry"
              >
                {translate(locale, "navDataEntry")}
              </Link>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/transactions" && styles.sidebarNavLinkActive
                )}
                href="/transactions"
              >
                {translate(locale, "navTransactions")}
              </Link>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/batch-review" && styles.sidebarNavLinkActive
                )}
                href="/batch-review"
              >
                {translate(locale, "navBatchReview")}
              </Link>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/ai-insights" && styles.sidebarNavLinkActive
                )}
                href="/ai-insights"
              >
                {translate(locale, "navAiInsights")}
              </Link>
              <Link
                className={mergeClasses(
                  styles.sidebarNavLink,
                  pathname === "/settings" && styles.sidebarNavLinkActive
                )}
                href="/settings"
              >
                {translate(locale, "navSettings")}
              </Link>
            </div>
          </Card>

          <SessionCard />

          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.sectionEyebrow}>{translate(locale, "slicersEyebrow")}</div>
            <Title2>{translate(locale, "filtersTitle")}</Title2>
            <Field label={translate(locale, "searchLabel")}>
              <Input
                placeholder={translate(locale, "searchPlaceholder")}
                value={searchQuery}
                onChange={(_, data) => setSearchQuery(data.value)}
              />
            </Field>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>{translate(locale, "timeRangeLabel")}</Body1>
              <div className={styles.pillGrid}>
                <Button appearance={timeRange === "week" ? "primary" : "secondary"} onClick={() => setTimeRange("week")} size="small">{translate(locale, "rangeWeek")}</Button>
                <Button appearance={timeRange === "month" ? "primary" : "secondary"} onClick={() => setTimeRange("month")} size="small">{translate(locale, "rangeMonth")}</Button>
                <Button appearance={timeRange === "quarter" ? "primary" : "secondary"} onClick={() => setTimeRange("quarter")} size="small">{translate(locale, "rangeQuarter")}</Button>
                <Button appearance={timeRange === "6months" ? "primary" : "secondary"} onClick={() => setTimeRange("6months")} size="small">{translate(locale, "range6Months")}</Button>
                <Button appearance={timeRange === "year" ? "primary" : "secondary"} onClick={() => setTimeRange("year")} size="small">{translate(locale, "rangeYear")}</Button>
                <Button appearance={timeRange === "5years" ? "primary" : "secondary"} onClick={() => setTimeRange("5years")} size="small">{translate(locale, "range5Years")}</Button>
                <Button appearance={timeRange === "all" ? "primary" : "secondary"} onClick={() => setTimeRange("all")} size="small">{translate(locale, "rangeAll")}</Button>
              </div>
            </div>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>{translate(locale, "amountDirection")}</Body1>
              <div className={styles.slicerButtons}>
                <Button appearance={amountFilter === "all" ? "primary" : "secondary"} onClick={() => setAmountFilter("all")} size="small">{translate(locale, "amountAll")}</Button>
                <Button appearance={amountFilter === "spend" ? "primary" : "secondary"} onClick={() => setAmountFilter("spend")} size="small">{translate(locale, "amountSpend")}</Button>
                <Button appearance={amountFilter === "refunds" ? "primary" : "secondary"} onClick={() => setAmountFilter("refunds")} size="small">{translate(locale, "amountRefunds")}</Button>
              </div>
            </div>
            <Field label={translate(locale, "sortLabel")}>
              <select className={styles.selectControl} value={sortOption} onChange={(event) => setSortOption(event.target.value as SortOption)}>
                <option value="amount_desc">{translate(locale, "sortAmountDesc")}</option>
                <option value="amount_asc">{translate(locale, "sortAmountAsc")}</option>
                <option value="merchant">{translate(locale, "sortMerchant")}</option>
                <option value="date">{translate(locale, "sortDate")}</option>
              </select>
            </Field>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>
                {translate(locale, "merchantSlicerLabel")}
              </Body1>
              {merchantCandidates.length > 0 ? (
                <div className={styles.pillGrid}>
                  {merchantCandidates.map((merchant) => (
                    <Button
                      key={merchant}
                      size="small"
                      appearance={selectedMerchant === merchant ? "primary" : "secondary"}
                      onClick={() =>
                        setSelectedMerchant((current) => (current === merchant ? null : merchant))
                      }
                    >
                      {merchant}
                    </Button>
                  ))}
                </div>
              ) : hasLoaded ? (
                <Body1 className={styles.emptySlicerState}>
                  {translate(locale, "merchantSlicerEmpty")}
                </Body1>
              ) : null}
            </div>
            <div className={styles.actions}>
              <Button
                size="small"
                onClick={() => {
                  setSearchQuery("");
                  setTimeRange("all");
                  setAmountFilter("all");
                  setSortOption("amount_desc");
                  setSelectedMerchant(null);
                }}
              >
                {translate(locale, "clearSlicers")}
              </Button>
              {hasLoaded ? (
                <Badge appearance="outline" color="brand">
                  {filteredTransactions.length} {translate(locale, "rowsInViewSuffix")}
                </Badge>
              ) : null}
            </div>
          </Card>
        </aside>

        <section className={styles.reportArea}>
          <div className={styles.header}>
            <Title1>{translate(locale, "transactionsTitle")}</Title1>
            {hasLoaded ? (
              <Body1>
                {filteredTransactions.length} {translate(locale, "transactionsRecordsSuffix")} |{" "}
                {netTotalLabel}: {totalAmountSummary}
              </Body1>
            ) : null}
            <Body1 className={styles.headerNote}>
              {translate(locale, "transactionsCurrencyNote")}
            </Body1>
            {message ? <Body1>{message}</Body1> : null}
          </div>

          <Card className={styles.card}>
            {hasLoaded && error ? (
              <div data-testid="transactions-load-warning">
                <Body1>{error}</Body1>
                <Button appearance="secondary" onClick={retryNow} size="small">
                  {translate(locale, "retryNow")}
                </Button>
              </div>
            ) : null}
            {!hasLoaded && error ? (
              <div data-testid="transactions-initial-recovery">
                <Body1>{translate(locale, "initialRecovery")}</Body1>
                <Button appearance="primary" onClick={retryNow} size="small">
                  {translate(locale, "retryNow")}
                </Button>
              </div>
            ) : loading && !hasLoaded ? (
              <Spinner label="Loading transactions..." />
            ) : (
              <div className={styles.tableWrap}>
                <div className={styles.drilldownPanel}>
                  <div className={styles.drilldownLabel}>
                    {translate(locale, "transactionsYear")}
                  </div>
                  <div className={styles.pillGrid}>
                    {groupedTransactions.map((yearGroup) => (
                      <Button
                        key={yearGroup.year}
                        size="small"
                        appearance={selectedYear === yearGroup.year ? "primary" : "secondary"}
                        onClick={() => {
                          setSelectedYear(yearGroup.year);
                          setSelectedMonthKey(yearGroup.months[0]?.monthKey ?? null);
                        }}
                      >
                        {yearGroup.year}
                      </Button>
                    ))}
                  </div>
                </div>

                {activeYearGroup ? (
                  <div className={styles.drilldownPanel}>
                    <div className={styles.drilldownLabel}>
                      {translate(locale, "transactionsMonth")}
                    </div>
                    <div className={styles.pillGrid}>
                      {activeYearGroup.months.map((monthGroup) => (
                        <Button
                          key={monthGroup.monthKey}
                          size="small"
                          appearance={
                            selectedMonthKey === monthGroup.monthKey ? "primary" : "secondary"
                          }
                          onClick={() => setSelectedMonthKey(monthGroup.monthKey)}
                        >
                          {monthGroup.monthLabel}
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : null}

                {activeMonthGroup ? (
                  <div className={styles.monthSection}>
                    <div className={styles.monthHeader}>
                      <div className={styles.monthTitle}>
                        {activeYearGroup?.year} / {activeMonthGroup.monthLabel}
                      </div>
                      <div className={styles.monthSubtotal}>
                        {monthSubtotalLabel}: {activeMonthSubtotalSummary}
                      </div>
                    </div>
                    <div className={styles.monthTableWrap}>
                      <Table aria-label="Transactions table">
                        <TableHeader>
                          <TableRow>
                            <TableHeaderCell className={styles.tableHeaderCell}>Date</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Merchant</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Category</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>
                              {translate(locale, "transactionsOriginalAmount")}
                            </TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Action</TableHeaderCell>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {activeMonthGroup.transactions.map((transaction, index) => (
                            <TableRow
                              className={index % 2 ? styles.rowAlt : undefined}
                              key={transaction.id}
                            >
                              <TableCell className={styles.tableCell}>{transaction.date}</TableCell>
                              <TableCell className={styles.tableCell}>
                                <Input
                                  className={mergeClasses(
                                    styles.selectControl,
                                    styles.merchantControl
                                  )}
                                  value={draftMerchant(transaction)}
                                  onChange={(_event, data) =>
                                    setMerchantDrafts((currentDrafts) => ({
                                      ...currentDrafts,
                                      [transaction.id]: data.value,
                                    }))
                                  }
                                  aria-label={`Merchant for transaction ${transaction.id}`}
                                />
                              </TableCell>
                              <TableCell className={styles.tableCell}>
                                <select
                                  className={mergeClasses(
                                    styles.selectControl,
                                    styles.categoryControl
                                  )}
                                  value={draftCategory(transaction)}
                                  onChange={(event) =>
                                    setCategoryDrafts((currentDrafts) => ({
                                      ...currentDrafts,
                                      [transaction.id]: event.target.value,
                                    }))
                                  }
                                >
                                  {transactionCategories.map((categoryOption) => (
                                    <option key={categoryOption} value={categoryOption}>
                                      {categoryOption}
                                    </option>
                                  ))}
                                  {!transactionCategories.includes(
                                    transaction.category as (typeof transactionCategories)[number]
                                  ) ? (
                                    <option value={transaction.category}>
                                      {transaction.category}
                                    </option>
                                  ) : null}
                                </select>
                              </TableCell>
                              <TableCell className={styles.tableCell}>
                                <div className={styles.amountCell}>
                                  <div
                                    className={mergeClasses(
                                      transaction.amount < 0
                                        ? styles.amountNegative
                                        : styles.amountPositive
                                    )}
                                  >
                                    {formatCurrencyAmount(
                                      transaction.amount,
                                      transaction.currency ?? "USD",
                                      intlLocale
                                    )}
                                  </div>
                                  <div className={styles.amountMeta}>
                                    {translate(
                                      locale,
                                      "transactionsOriginalCurrencyTagTemplate",
                                      {
                                        currency: describeCurrency(
                                          normalizeCurrencyCode(transaction.currency)
                                        ),
                                      }
                                    )}
                                  </div>
                                </div>
                              </TableCell>
                              <TableCell className={styles.tableCell}>
                                <div className={styles.actions}>
                                  <Button
                                    appearance="secondary"
                                    disabled={
                                      savingTransactionId === transaction.id ||
                                      deletingTransactionId === transaction.id
                                    }
                                    onClick={() => void saveTransactionEdits(transaction)}
                                    size="small"
                                  >
                                    {savingTransactionId === transaction.id
                                      ? "Saving..."
                                      : "Save"}
                                  </Button>
                                  <Button
                                    appearance="outline"
                                    disabled={
                                      savingTransactionId === transaction.id ||
                                      deletingTransactionId === transaction.id
                                    }
                                    onClick={() => void deleteTransaction(transaction)}
                                    size="small"
                                  >
                                    {deletingTransactionId === transaction.id
                                      ? "Deleting..."
                                      : "Delete"}
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                ) : (
                  <Body1 className={styles.emptySlicerState}>
                    {translate(locale, "emptySlicerNoRows")}
                  </Body1>
                )}
              </div>
            )}
          </Card>
        </section>
      </div>
    </main>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
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
import { apiFetchJson, ApiError } from "../api";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import { useLocale } from "../locale-provider";
import { translate } from "../messages";

type Transaction = {
  date: string;
  merchant: string;
  amount: number;
  category: string;
  currency?: "USD" | "TWD";
};

type TransactionsResponse = {
  transactions: Transaction[];
};

type AmountFilter = "all" | "spend" | "refunds";
type SortOption = "amount_desc" | "amount_asc" | "merchant" | "date";
type TimeRange = "week" | "month" | "quarter" | "year" | "5years" | "all";
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

const useStyles = makeStyles({
  page: {
    minHeight: "100vh",
    backgroundColor: "var(--page-surface)",
  },
  shell: {
    maxWidth: "1480px",
    marginLeft: "auto",
    marginRight: "auto",
    paddingTop: "24px",
    paddingBottom: "32px",
    paddingLeft: "24px",
    paddingRight: "24px",
    display: "grid",
    gridTemplateColumns: "320px minmax(0, 1fr)",
    gap: "16px",
    "@media (max-width: 1120px)": {
      gridTemplateColumns: "1fr",
    },
    "@media (max-width: 640px)": {
      paddingLeft: "16px",
      paddingRight: "16px",
    },
  },
  sidebar: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  reportArea: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  header: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
    marginBottom: "12px",
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
    fontSize: tokens.fontSizeBase200,
  },
  amountNegative: {
    color: "#d13438",
    fontWeight: tokens.fontWeightSemibold,
  },
  amountPositive: {
    color: tokens.colorNeutralForeground1,
    fontWeight: tokens.fontWeightSemibold,
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
    paddingTop: "10px",
    paddingBottom: "10px",
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
});

function formatCurrency(amount: number, currencyCode: string = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currencyCode,
    maximumFractionDigits: 2,
  }).format(amount);
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
  const { locale } = useLocale();
  const { refreshSession, status, user } = useAuth();
  const pathname = usePathname();
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [amountFilter, setAmountFilter] = useState<AmountFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("amount_desc");
  const [selectedMerchant, setSelectedMerchant] = useState<string | null>(null);
  const [selectedYear, setSelectedYear] = useState<string | null>(null);
  const [selectedMonthKey, setSelectedMonthKey] = useState<string | null>(null);

  useEffect(() => {
    async function loadTransactions() {
      setLoading(true);
      setError("");
      try {
        const payload = await apiFetchJson<TransactionsResponse>(
          "/transactions?range=all&limit=5000",
          "Failed to load transactions."
        );
        setTransactions(payload.transactions);
      } catch (loadError) {
        if (loadError instanceof ApiError && loadError.status === 401) {
          await refreshSession();
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Failed to load transactions.");
      } finally {
        setLoading(false);
      }
    }

    if (status === "authenticated") {
      void loadTransactions();
      return;
    }

    setTransactions([]);
    setLoading(status === "loading");
  }, [refreshSession, status]);

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

  const totalAmount = useMemo(
    () => filteredTransactions.reduce((sum, transaction) => sum + transaction.amount, 0),
    [filteredTransactions]
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
      <div className={styles.shell}>
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
              ) : (
                <Body1 className={styles.emptySlicerState}>
                  {translate(locale, "merchantSlicerEmpty")}
                </Body1>
              )}
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
              <Badge appearance="outline" color="brand">
                {filteredTransactions.length} {translate(locale, "rowsInViewSuffix")}
              </Badge>
            </div>
          </Card>
        </aside>

        <section className={styles.reportArea}>
          <div className={styles.header}>
            <Title1>{translate(locale, "transactionsTitle")}</Title1>
            <Body1>
              {filteredTransactions.length} records | Net total: {formatCurrency(totalAmount)}
            </Body1>
          </div>

          <Card className={styles.card}>
            {loading ? (
              <Spinner label="Loading transactions..." />
            ) : error ? (
              <Body1>{error}</Body1>
            ) : (
              <div className={styles.tableWrap}>
                <div className={styles.drilldownPanel}>
                  <div className={styles.drilldownLabel}>Year</div>
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
                    <div className={styles.drilldownLabel}>Month</div>
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
                        Subtotal: {formatCurrency(activeMonthGroup.subtotal)}
                      </div>
                    </div>
                    <div className={styles.monthTableWrap}>
                      <Table aria-label="Transactions table">
                        <TableHeader>
                          <TableRow>
                            <TableHeaderCell className={styles.tableHeaderCell}>Date</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Merchant</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Category</TableHeaderCell>
                            <TableHeaderCell className={styles.tableHeaderCell}>Amount</TableHeaderCell>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {activeMonthGroup.transactions.map((transaction, index) => (
                            <TableRow
                              className={index % 2 ? styles.rowAlt : undefined}
                              key={`${transaction.date}-${transaction.merchant}-${index}`}
                            >
                              <TableCell className={styles.tableCell}>{transaction.date}</TableCell>
                              <TableCell className={styles.tableCell}>{transaction.merchant}</TableCell>
                              <TableCell className={styles.tableCell}>{transaction.category}</TableCell>
                              <TableCell
                                className={mergeClasses(
                                  styles.tableCell,
                                  transaction.amount < 0 ? styles.amountNegative : styles.amountPositive
                                )}
                              >
                                {formatCurrency(transaction.amount, transaction.currency ?? "USD")}
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

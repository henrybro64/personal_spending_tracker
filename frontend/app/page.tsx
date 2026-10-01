"use client";

import {
  Dispatch,
  ReactNode,
  SetStateAction,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  Badge,
  Body1,
  Button,
  Card,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  ProgressBar,
  Switch,
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

import { apiFetchJson } from "./api";
import {
  DashboardRequestCoordinator,
  type DashboardFilters,
  dashboardLoadKey,
  effectiveSearchQuery,
  recoverableReadFailure,
  loadDashboardSnapshot,
  reduceDashboardLoad,
  refreshAfterUpload,
  runGuardedDashboardLoad,
  shouldReleaseSearchPin,
} from "./dashboard-refresh";
import { useReadRecovery } from "./read-recovery";
import { useSpendingRevision } from "./server-state";
import { AuthPage, AuthPanel, SessionCard } from "./auth-card";
import { useAuth } from "./auth-provider";
import {
  describeCurrency,
  formatCurrencyAmount,
  formatCurrencyTotals,
  type CurrencyTotal,
  type SupportedCurrency,
} from "./currency";
import { useLocale } from "./locale-provider";
import { translate, type MessageKey } from "./messages";
import { UnconvertedTransactionsNotice } from "./unconverted-notice";


type Transaction = {
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
  display_amount?: number;
  display_currency?: "USD" | "TWD";
  is_converted?: boolean;
};

type SummaryResponse = {
  total_spending: number;
  number_of_transactions: number;
  average_transaction_amount: number;
  number_of_statements: number;
  unconverted_transaction_count?: number;
  unconverted_currencies?: string[];
  statement_count_scope?: "all_time";
};

type UnconvertedTransaction = {
  id: number;
  date: string;
  merchant: string;
  original_amount: number;
  original_currency: string;
  reason: string;
};

type RecentUploadCurrencyTotal = {
  currency: SupportedCurrency;
  total_amount: number;
  transaction_count: number;
};

type RecentUpload = {
  file_name: string;
  display_name: string;
  transaction_count: number;
  total_amount: number;
  currency_totals: RecentUploadCurrencyTotal[];
  uploaded_at: string;
  parse_status: string;
};

type RecentUploadsResponse = {
  uploads: RecentUpload[];
};

type DisplayCurrency = "USD" | "TWD";

type MerchantBreakdown = {
  merchant: string;
  total: number;
  abs_total: number;
};

type DateBreakdown = {
  date: string;
  total: number;
  abs_total: number;
};

type CategoryBreakdown = {
  category: string;
  total: number;
  share: number;
  color: string;
};

type DashboardViewResponse = {
  active_currency: DisplayCurrency;
  summary: SummaryResponse;
  transactions: Transaction[];
  sorted_transactions: Transaction[];
  merchant_breakdown: MerchantBreakdown[];
  category_breakdown: Array<{
    category: string;
    total: number;
    share: number;
  }>;
  date_breakdown: DateBreakdown[];
  unconverted_transactions?: UnconvertedTransaction[];
  metrics: {
    selection_net: number;
    selection_spend: number;
    selection_refunds: number;
    selection_average: number;
    upload_net: number;
    largest_transaction_amount: number;
    selection_coverage: number;
    model_total_in_active_currency: number;
  };
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

const dashboardTimeRangeOptions = [
  "month",
  "quarter",
  "6months",
  "year",
  "all",
] as const satisfies readonly TimeRange[];

function sortOptionMessageKey(option: SortOption): MessageKey {
  switch (option) {
    case "amount_asc":
      return "sortAmountAsc";
    case "merchant":
      return "sortMerchant";
    case "date":
      return "sortDate";
    case "amount_desc":
    default:
      return "sortAmountDesc";
  }
}

function amountFilterMessageKey(filter: AmountFilter): MessageKey {
  switch (filter) {
    case "spend":
      return "amountSpend";
    case "refunds":
      return "amountRefunds";
    case "all":
    default:
      return "amountAll";
  }
}

function timeRangeMessageKey(range: TimeRange): MessageKey {
  switch (range) {
    case "week":
      return "rangeWeek";
    case "month":
      return "rangeMonth";
    case "quarter":
      return "rangeQuarter";
    case "6months":
      return "range6Months";
    case "year":
      return "rangeYear";
    case "5years":
      return "range5Years";
    case "all":
    default:
      return "rangeAll";
  }
}

type MetricCard = {
  label: string;
  value: string;
  hint: string;
};

const emptySummary: SummaryResponse = {
  total_spending: 0,
  number_of_transactions: 0,
  average_transaction_amount: 0,
  number_of_statements: 0,
  unconverted_transaction_count: 0,
  unconverted_currencies: [],
  statement_count_scope: "all_time",
};

const categoryPalette = ["#6b7dd6", "#2f8f95", "#9a85d1", "#5f95d6", "#5ca87a", "#b38c5a", "#a776b8"];

function createFormatters(intlLocale: string) {
  const countFormatter = new Intl.NumberFormat(intlLocale);
  const percentFormatter = new Intl.NumberFormat(intlLocale, {
    style: "percent",
    maximumFractionDigits: 0,
  });
  const dateTimeFormatter = new Intl.DateTimeFormat(intlLocale, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  const transactionDateFormatter = new Intl.DateTimeFormat(intlLocale, {
    month: "numeric",
    day: "numeric",
    year: "numeric",
  });
  const transactionDateShortFormatter = new Intl.DateTimeFormat(intlLocale, {
    month: "numeric",
    day: "numeric",
    year: "2-digit",
  });

  return {
    formatCurrency(amount: number, currencyCode: string = "USD") {
      return formatCurrencyAmount(amount, currencyCode, intlLocale);
    },
    formatCount(value: number) {
      return countFormatter.format(value);
    },
    formatPercent(value: number) {
      return percentFormatter.format(value);
    },
    formatUploadDate(uploadedAt: string) {
      const parsedDate = new Date(uploadedAt);

      if (Number.isNaN(parsedDate.getTime())) {
        return uploadedAt;
      }

      return dateTimeFormatter.format(parsedDate);
    },
    formatTransactionDate(dateValue: string, shortYear = false) {
      const parsedDate = parseIsoDate(dateValue);

      if (!parsedDate) {
        return dateValue;
      }

      return shortYear
        ? transactionDateShortFormatter.format(parsedDate)
        : transactionDateFormatter.format(parsedDate);
    },
  };
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

function buildPieChartBackground(categories: CategoryBreakdown[]) {
  if (categories.length === 0) {
    return "none";
  }

  let currentStop = 0;

  const stops = categories.map((categoryItem, index) => {
    const nextStop =
      index === categories.length - 1
        ? 100
        : currentStop + categoryItem.share * 100;
    const segment = `${categoryItem.color} ${currentStop}% ${nextStop}%`;
    currentStop = nextStop;
    return segment;
  });

  return `conic-gradient(${stops.join(", ")})`;
}

const useStyles = makeStyles({
  page: {
    minHeight: "100dvh",
    backgroundColor: "var(--page-surface)",
    color: tokens.colorNeutralForeground1,
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
    gap: "16px",
    minWidth: 0,
  },
  panel: {
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow:
      "0 1px 2px rgba(0, 0, 0, 0.05), 0 10px 28px rgba(0, 0, 0, 0.06)",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusXLarge),
  },
  sidebarPanel: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
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
  reportHeader: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    paddingBottom: "24px",
    backgroundImage:
      "linear-gradient(180deg, rgba(255,255,255,0.96), rgba(247,250,255,0.98))",
    color: tokens.colorNeutralForeground1,
    ...shorthands.border("1px", "solid", tokens.colorCompoundBrandStroke),
  },
  reportHeaderTop: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "8px",
    flexWrap: "wrap",
  },
  reportHeaderTitle: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: "0.01em",
  },
  headerMeta: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
  },
  languageRow: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    flexWrap: "wrap",
  },
  languageOption: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground2,
    whiteSpace: "nowrap",
  },
  reportHeaderMain: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: "16px",
    flexWrap: "wrap",
  },
  sectionEyebrow: {
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
  },
  sectionEyebrowLight: {
    color: tokens.colorBrandForeground1,
  },
  sectionEyebrowDark: {
    color: tokens.colorNeutralForeground3,
  },
  reportTitle: {
    marginTop: "10px",
    marginBottom: "10px",
    fontSize: "34px",
    lineHeight: "38px",
    fontWeight: 650,
    letterSpacing: "-0.03em",
  },
  reportLead: {
    maxWidth: "760px",
    color: tokens.colorNeutralForeground2,
  },
  currencyContextNote: {
    maxWidth: "760px",
    color: tokens.colorNeutralForeground2,
  },
  reportTimeRangeRow: {
    display: "flex",
    flexDirection: "column",
    gap: "10px",
    paddingTop: "4px",
    ...shorthands.borderTop("1px", "solid", tokens.colorNeutralStroke2),
  },
  reportTimeRangeLabel: {
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: tokens.colorNeutralForeground3,
  },
  reportTimeRangePills: {
    display: "flex",
    flexWrap: "wrap",
    gap: "8px",
  },
  headerHighlights: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(160px, 1fr))",
    gap: "8px",
    minWidth: "300px",
    "@media (max-width: 640px)": {
      gridTemplateColumns: "1fr",
      minWidth: "100%",
    },
  },
  headerHighlight: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
    paddingTop: "12px",
    paddingBottom: "12px",
    paddingLeft: "12px",
    paddingRight: "12px",
    backgroundColor: "rgba(255,255,255,0.82)",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  headerHighlightLabel: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
  },
  headerHighlightValue: {
    fontSize: tokens.fontSizeBase500,
    fontWeight: tokens.fontWeightSemibold,
  },
  actions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
  },
  selectControl: {
    width: "100%",
    height: "40px",
    paddingLeft: "12px",
    paddingRight: "12px",
    fontFamily: "var(--font-sans)",
    fontSize: tokens.fontSizeBase300,
    color: "#201f1e",
    backgroundColor: "#ffffff",
    appearance: "none",
    ...shorthands.border("1px", "solid", "#c8c6c4"),
    ...shorthands.borderRadius("4px"),
  },
  slicerGroup: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  slicerButtons: {
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: "8px",
  },
  pillGrid: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  pillButton: {
    justifyContent: "flex-start",
  },
  emptySlicerState: {
    color: "#8a8886",
  },
  historyList: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  historyItem: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    paddingTop: "12px",
    paddingBottom: "12px",
    paddingLeft: "12px",
    paddingRight: "12px",
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  historyTop: {
    display: "flex",
    justifyContent: "space-between",
    gap: "12px",
    alignItems: "flex-start",
  },
  historyName: {
    fontWeight: tokens.fontWeightSemibold,
    wordBreak: "break-word",
  },
  historyMeta: {
    color: "#605e5c",
    fontSize: tokens.fontSizeBase200,
  },
  historyStats: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  historyStat: {
    display: "flex",
    flexDirection: "column",
    gap: "2px",
  },
  historyStatLabel: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    textTransform: "uppercase",
    letterSpacing: "0.07em",
  },
  historyStatValue: {
    fontWeight: tokens.fontWeightSemibold,
  },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: "8px",
  },
  metricCard: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    paddingTop: "14px",
    paddingBottom: "14px",
    paddingLeft: "14px",
    paddingRight: "14px",
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 0 rgba(0, 0, 0, 0.03)",
    ...shorthands.border("1px", "solid", "#edebe9"),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  metricAccent: {
    width: "48px",
    height: "4px",
    backgroundColor: tokens.colorBrandBackground,
    ...shorthands.borderRadius("999px"),
  },
  metricLabel: {
    color: "#605e5c",
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
  },
  metricValue: {
    fontSize: "30px",
    lineHeight: "32px",
    fontWeight: 650,
    letterSpacing: "-0.03em",
  },
  metricHint: {
    color: "#605e5c",
  },
  metricContext: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
  primaryVisualsGrid: {
    display: "grid",
    gridTemplateColumns:
      "minmax(0, 1.15fr) minmax(300px, 0.9fr) minmax(300px, 0.95fr)",
    gap: "12px",
    "@media (max-width: 1366px)": {
      gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    },
    "@media (max-width: 959px)": {
      gridTemplateColumns: "1fr",
    },
  },
  visualCard: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  cardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: "8px",
    flexWrap: "wrap",
  },
  cardHeaderTitle: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  cardContext: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
  pieLayout: {
    display: "grid",
    gridTemplateColumns: "1fr",
    gap: "12px",
    alignItems: "start",
  },
  pieChartWrap: {
    width: "100%",
    maxWidth: "320px",
    aspectRatio: "1 / 1",
    justifySelf: "center",
    "@media (max-width: 639px)": {
      maxWidth: "260px",
    },
  },
  pieChart: {
    width: "100%",
    height: "100%",
    position: "relative",
    backgroundColor: "#edebe9",
    ...shorthands.borderRadius("50%"),
  },
  pieChartHole: {
    position: "absolute",
    top: "24%",
    right: "24%",
    bottom: "24%",
    left: "24%",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: "4px",
    textAlign: "center",
    backgroundColor: "#ffffff",
    boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.04)",
    ...shorthands.borderRadius("50%"),
  },
  pieCenterValue: {
    fontSize: tokens.fontSizeBase500,
    fontWeight: tokens.fontWeightSemibold,
    lineHeight: "1.1",
  },
  pieCenterLabel: {
    color: "#605e5c",
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.07em",
  },
  categoryLegend: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  categoryLegendRow: {
    display: "grid",
    gridTemplateColumns: "auto minmax(0, 1fr) auto",
    gap: "10px",
    alignItems: "center",
    paddingTop: "10px",
    paddingBottom: "10px",
    paddingLeft: "10px",
    paddingRight: "10px",
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  categoryLegendSwatch: {
    width: "12px",
    height: "12px",
    ...shorthands.borderRadius("999px"),
  },
  categoryLegendMain: {
    display: "flex",
    flexDirection: "column",
    gap: "2px",
    minWidth: 0,
  },
  categoryLegendLabel: {
    fontWeight: tokens.fontWeightSemibold,
    wordBreak: "break-word",
  },
  categoryLegendMeta: {
    color: "#605e5c",
    fontSize: tokens.fontSizeBase200,
  },
  categoryLegendValue: {
    fontWeight: tokens.fontWeightSemibold,
    whiteSpace: "nowrap",
  },
  merchantRows: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  merchantRow: {
    display: "grid",
    gridTemplateColumns: "minmax(0, 1fr) auto",
    gap: "12px",
    alignItems: "center",
  },
  merchantMain: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    minWidth: 0,
  },
  merchantButton: {
    justifyContent: "flex-start",
    paddingLeft: 0,
    paddingRight: 0,
    minWidth: 0,
  },
  merchantName: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontWeight: tokens.fontWeightSemibold,
  },
  barTrack: {
    height: "10px",
    backgroundColor: tokens.colorNeutralBackground3,
    overflow: "hidden",
    ...shorthands.borderRadius("999px"),
  },
  barFill: {
    height: "100%",
    backgroundColor: tokens.colorBrandBackground,
    ...shorthands.borderRadius("999px"),
  },
  barFillSelected: {
    backgroundColor: tokens.colorPaletteMarigoldBackground2,
  },
  merchantValue: {
    fontWeight: tokens.fontWeightSemibold,
    color: "#323130",
  },
  tableCard: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  tableHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: "8px",
    flexWrap: "wrap",
  },
  tableTools: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
  },
  searchField: {
    minWidth: "240px",
    backgroundColor: "#ffffff",
  },
  tableWrap: {
    overflowX: "auto",
    overflowY: "auto",
    maxHeight: "600px",
    WebkitOverflowScrolling: "touch",
    backgroundColor: tokens.colorNeutralBackground1,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  dataTable: {
    minWidth: "100%",
  },
  tableHeaderCell: {
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: "0.02em",
    textTransform: "uppercase",
    paddingTop: "8px",
    paddingBottom: "8px",
  },
  tableCell: {
    fontSize: tokens.fontSizeBase300,
    paddingTop: "8px",
    paddingBottom: "8px",
  },
  rowAlt: {
    backgroundColor: tokens.colorNeutralBackground2,
  },
  merchantCell: {
    fontWeight: tokens.fontWeightSemibold,
  },
  amountPositive: {
    color: "#323130",
    fontWeight: tokens.fontWeightSemibold,
  },
  amountNegative: {
    color: "#d13438",
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
  emptyState: {
    color: "#605e5c",
  },
});

type DashboardStyles = ReturnType<typeof useStyles>;
type DashboardFormatters = ReturnType<typeof createFormatters>;

function DashboardCharts({
  styles,
  locale,
  fm,
  activeCurrency,
  activeCurrencyLabel,
  merchantBreakdown,
  merchantBarMaximum,
  selectedMerchant,
  setSelectedMerchant,
  categoryBreakdown,
  categoryPieBackground,
  categorySpendTotal,
}: Readonly<{
  styles: DashboardStyles;
  locale: ReturnType<typeof useLocale>["locale"];
  fm: DashboardFormatters;
  activeCurrency: DisplayCurrency;
  activeCurrencyLabel: string;
  merchantBreakdown: MerchantBreakdown[];
  merchantBarMaximum: number;
  selectedMerchant: string | null;
  setSelectedMerchant: Dispatch<SetStateAction<string | null>>;
  categoryBreakdown: CategoryBreakdown[];
  categoryPieBackground: string;
  categorySpendTotal: number;
}>) {
  return (
    <div className={styles.primaryVisualsGrid}>
      <Card className={mergeClasses(styles.panel, styles.visualCard)}>
        <div className={styles.cardHeader}>
          <div className={styles.cardHeaderTitle}>
            <Title2>{translate(locale, "merchantContributionTitle")}</Title2>
            <Body1 className={styles.cardContext}>
              {translate(locale, "chartDisplayCurrencyHintTemplate", {
                currency: activeCurrencyLabel,
              })}
            </Body1>
          </div>
        </div>

        {merchantBreakdown.length > 0 ? (
          <div className={styles.merchantRows}>
            {merchantBreakdown.map((merchantItem) => (
              <div className={styles.merchantRow} key={merchantItem.merchant}>
                <div className={styles.merchantMain}>
                  <Button
                    appearance="transparent"
                    className={styles.merchantButton}
                    onClick={() =>
                      setSelectedMerchant((currentValue) =>
                        currentValue === merchantItem.merchant
                          ? null
                          : merchantItem.merchant
                      )
                    }
                    size="small"
                  >
                    <span className={styles.merchantName}>
                      {merchantItem.merchant}
                    </span>
                  </Button>
                  <div className={styles.barTrack}>
                    <div
                      className={mergeClasses(
                        styles.barFill,
                        selectedMerchant === merchantItem.merchant &&
                          styles.barFillSelected
                      )}
                      style={{
                        width: `${
                          merchantBarMaximum > 0
                            ? (merchantItem.abs_total / merchantBarMaximum) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                </div>
                <div className={styles.merchantValue}>
                  {fm.formatCurrency(merchantItem.total, activeCurrency)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Body1 className={styles.emptyState}>
            {translate(locale, "emptyMerchantVisual")}
          </Body1>
        )}
      </Card>

      <Card className={mergeClasses(styles.panel, styles.visualCard)}>
        <div className={styles.cardHeader}>
          <div className={styles.cardHeaderTitle}>
            <Title2>{translate(locale, "spendingByCategoryTitle")}</Title2>
            <Body1 className={styles.cardContext}>
              {translate(locale, "chartDisplayCurrencyHintTemplate", {
                currency: activeCurrencyLabel,
              })}
            </Body1>
          </div>
          <Badge appearance="outline" color="warning">
            {translate(locale, "badgePositiveOnly")}
          </Badge>
        </div>

        {categoryBreakdown.length > 0 ? (
          <div className={styles.pieLayout}>
            <div className={styles.pieChartWrap}>
              <div
                className={styles.pieChart}
                style={{ backgroundImage: categoryPieBackground }}
              >
                <div className={styles.pieChartHole}>
                  <div className={styles.pieCenterValue}>
                    {fm.formatCurrency(categorySpendTotal, activeCurrency)}
                  </div>
                  <div className={styles.pieCenterLabel}>
                    {translate(locale, "pieCenterSpendInView")}
                  </div>
                </div>
              </div>
            </div>

            <div className={styles.categoryLegend}>
              {categoryBreakdown.map((categoryItem) => (
                <div className={styles.categoryLegendRow} key={categoryItem.category}>
                  <div
                    className={styles.categoryLegendSwatch}
                    style={{ backgroundColor: categoryItem.color }}
                  />
                  <div className={styles.categoryLegendMain}>
                    <div className={styles.categoryLegendLabel}>
                      {categoryItem.category}
                    </div>
                    <div className={styles.categoryLegendMeta}>
                      {fm.formatPercent(categoryItem.share)}{" "}
                      {translate(locale, "shareOfVisibleSpend")}
                    </div>
                  </div>
                  <div className={styles.categoryLegendValue}>
                    {fm.formatCurrency(categoryItem.total, activeCurrency)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <Body1 className={styles.emptyState}>
            {translate(locale, "emptyCategoryVisual")}
          </Body1>
        )}
      </Card>
    </div>
  );
}

function DashboardTransactionTable({
  styles,
  locale,
  fm,
  activeCurrency,
  activeCurrencyLabel,
  visibleRowsCount,
  sortedTransactions,
  hasSortedTransactions,
  recentUploads,
  statementCount,
}: Readonly<{
  styles: DashboardStyles;
  locale: ReturnType<typeof useLocale>["locale"];
  fm: DashboardFormatters;
  activeCurrency: DisplayCurrency;
  activeCurrencyLabel: string;
  visibleRowsCount: number;
  sortedTransactions: Transaction[];
  hasSortedTransactions: boolean;
  recentUploads: RecentUpload[];
  statementCount: number;
}>) {
  let emptyMessageKey: MessageKey = "emptyNoUpload";
  if (recentUploads.length > 0 || statementCount > 0) {
    emptyMessageKey = "emptySlicerNoRows";
  }

  return (
    <Card className={mergeClasses(styles.panel, styles.tableCard)}>
      <div className={styles.tableHeader}>
        <div className={styles.cardHeaderTitle}>
          <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
            {translate(locale, "detailMatrixEyebrow")}
          </div>
          <Title2>{translate(locale, "transactionsTitle")}</Title2>
          <Body1 className={styles.cardContext}>
            {translate(locale, "dashboardTableCurrencyNoteTemplate", {
              currency: activeCurrencyLabel,
            })}
          </Body1>
        </div>
        <div className={styles.tableTools}>
          <Badge appearance="outline" color="brand">
            {fm.formatCount(visibleRowsCount)} {translate(locale, "visibleSuffix")}
          </Badge>
        </div>
      </div>

      {hasSortedTransactions ? (
        <div className={styles.tableWrap}>
          <Table aria-label={translate(locale, "tableAriaLabel")} className={styles.dataTable}>
            <TableHeader>
              <TableRow>
                <TableHeaderCell className={styles.tableHeaderCell}>
                  {translate(locale, "colDate")}
                </TableHeaderCell>
                <TableHeaderCell className={styles.tableHeaderCell}>
                  {translate(locale, "colMerchant")}
                </TableHeaderCell>
                <TableHeaderCell className={styles.tableHeaderCell}>
                  {translate(locale, "colCategory")}
                </TableHeaderCell>
                <TableHeaderCell className={styles.tableHeaderCell}>
                  {translate(locale, "colAmount")}
                </TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedTransactions.map((transaction, index) => (
                <TransactionAmountRow
                  activeCurrency={activeCurrency}
                  fm={fm}
                  index={index}
                  key={`${transaction.date}-${transaction.merchant}-${index}`}
                  locale={locale}
                  styles={styles}
                  transaction={transaction}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <Body1 className={styles.emptyState}>
          {translate(locale, emptyMessageKey)}
        </Body1>
      )}
    </Card>
  );
}

function TransactionAmountRow({
  styles,
  locale,
  fm,
  activeCurrency,
  transaction,
  index,
}: Readonly<{
  styles: DashboardStyles;
  locale: ReturnType<typeof useLocale>["locale"];
  fm: DashboardFormatters;
  activeCurrency: DisplayCurrency;
  transaction: Transaction;
  index: number;
}>) {
  const amountClass =
    transaction.amount < 0 ? styles.amountNegative : styles.amountPositive;
  const amountCurrency = transaction.currency ?? activeCurrency;
  const originalCurrency =
    transaction.original_currency ?? transaction.currency ?? activeCurrency;
  const showsConvertedOriginal =
    transaction.is_converted &&
    transaction.original_amount !== undefined &&
    Boolean(transaction.original_currency);

  return (
    <TableRow className={index % 2 === 1 ? styles.rowAlt : undefined}>
      <TableCell className={styles.tableCell}>
        {fm.formatTransactionDate(transaction.date, true)}
      </TableCell>
      <TableCell className={mergeClasses(styles.tableCell, styles.merchantCell)}>
        {transaction.merchant}
      </TableCell>
      <TableCell className={styles.tableCell}>{transaction.category}</TableCell>
      <TableCell className={styles.tableCell}>
        <div className={styles.amountCell}>
          <div className={amountClass}>
            {fm.formatCurrency(transaction.amount, amountCurrency)}
          </div>
          <div className={styles.amountMeta}>
            {showsConvertedOriginal
              ? translate(locale, "dashboardConvertedFromTemplate", {
                  amount: fm.formatCurrency(
                    transaction.original_amount ?? 0,
                    transaction.original_currency ?? amountCurrency
                  ),
                })
              : translate(locale, "dashboardOriginalCurrencyTemplate", {
                  currency: describeCurrency(originalCurrency),
                })}
          </div>
        </div>
      </TableCell>
    </TableRow>
  );
}

function RecentStatementsCard({
  styles,
  locale,
  fm,
  intlLocale,
  isDashboardLoading,
  hasSnapshot,
  recentUploads,
}: Readonly<{
  styles: DashboardStyles;
  locale: ReturnType<typeof useLocale>["locale"];
  fm: DashboardFormatters;
  intlLocale: string;
  isDashboardLoading: boolean;
  hasSnapshot: boolean;
  recentUploads: RecentUpload[];
}>) {
  let historyBody: ReactNode = null;
  if (isDashboardLoading && recentUploads.length === 0) {
    historyBody = (
      <Body1 className={styles.emptyState}>
        {translate(locale, "loadingHistoryLabel")}
      </Body1>
    );
  } else if (recentUploads.length > 0) {
    historyBody = (
      <div className={styles.historyList}>
        {recentUploads.map((upload) => (
          <RecentStatementRow
            fm={fm}
            intlLocale={intlLocale}
            key={upload.file_name}
            locale={locale}
            styles={styles}
            upload={upload}
          />
        ))}
      </div>
    );
  } else if (hasSnapshot) {
    historyBody = (
      <Body1 className={styles.emptyState}>{translate(locale, "noUploadsYet")}</Body1>
    );
  }

  return (
    <Card className={mergeClasses(styles.panel, styles.sidebarPanel)}>
      <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
        {translate(locale, "historyEyebrow")}
      </div>
      <Title2>{translate(locale, "recentStatementsTitle")}</Title2>
      {historyBody}
    </Card>
  );
}

function RecentStatementRow({
  styles,
  locale,
  fm,
  intlLocale,
  upload,
}: Readonly<{
  styles: DashboardStyles;
  locale: ReturnType<typeof useLocale>["locale"];
  fm: DashboardFormatters;
  intlLocale: string;
  upload: RecentUpload;
}>) {
  const totalsLabel = translate(
    locale,
    isRecentUploadMixedCurrency(upload)
      ? "recentUploadOriginalTotals"
      : "recentUploadOriginalTotal"
  );

  return (
    <div className={styles.historyItem}>
      <div className={styles.historyTop}>
        <div className={styles.historyName}>{upload.display_name}</div>
        <div className={styles.historyMeta}>{fm.formatUploadDate(upload.uploaded_at)}</div>
      </div>
      <div className={styles.historyStats}>
        <div className={styles.historyStat}>
          <div className={styles.historyStatLabel}>
            {translate(locale, "recentUploadRowsLabel")}
          </div>
          <div className={styles.historyStatValue}>
            {fm.formatCount(upload.transaction_count)}{" "}
            {translate(locale, "historyRowsWord")}
          </div>
        </div>
        {upload.currency_totals.length > 0 ? (
          <div className={styles.historyStat}>
            <div className={styles.historyStatLabel}>{totalsLabel}</div>
            <div className={styles.historyStatValue}>
              {formatCurrencyTotals(toCurrencyTotals(upload.currency_totals), intlLocale)}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function isRecentUploadMixedCurrency(upload: RecentUpload): boolean {
  return upload.currency_totals.length > 1;
}

function toCurrencyTotals(
  totals: RecentUploadCurrencyTotal[]
): CurrencyTotal[] {
  return totals.map((item) => ({
    currency: item.currency,
    total: item.total_amount,
  }));
}

export default function HomePage() {
  const styles = useStyles();
  const { locale, setLocale, intlLocale } = useLocale();
  const { refreshSession, status: authStatus, user } = useAuth();
  const fm = useMemo(() => createFormatters(intlLocale), [intlLocale]);
  const [recentUploads, setRecentUploads] = useState<RecentUpload[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("year");
  const [displayCurrency, setDisplayCurrency] = useState<DisplayCurrency>("USD");
  const [selectedMerchant, setSelectedMerchant] = useState<string | null>(null);
  const [amountFilter, setAmountFilter] = useState<AmountFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("amount_desc");
  const [error, setError] = useState("");
  const [dashboardError, setDashboardError] = useState("");
  const [dashboardStale, setDashboardStale] = useState(false);
  const [isDashboardLoading, setIsDashboardLoading] = useState(true);
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const [pinnedSearch, setPinnedSearch] = useState<string | null>(null);
  const requestCoordinatorRef = useRef(new DashboardRequestCoordinator());
  const localeRef = useRef(locale);
  localeRef.current = locale;
  const searchForRequest = effectiveSearchQuery(
    searchQuery,
    deferredSearchQuery,
    pinnedSearch
  );
  const requestFilters = useMemo<DashboardFilters>(
    () => ({
      timeRange,
      searchQuery: searchForRequest,
      amountFilter,
      sortOption,
      selectedMerchant,
      displayCurrency,
    }),
    [
      amountFilter,
      displayCurrency,
      searchForRequest,
      selectedMerchant,
      sortOption,
      timeRange,
    ]
  );
  const latestFiltersRef = useRef<DashboardFilters>(requestFilters);
  latestFiltersRef.current = {
    timeRange,
    searchQuery,
    amountFilter,
    sortOption,
    selectedMerchant,
    displayCurrency,
  };
  const spendingRevision = useSpendingRevision();
  const dashboardLoadToken = dashboardLoadKey(requestFilters, spendingRevision);
  const [failureId, setFailureId] = useState(0);
  const [recoveryTick, setRecoveryTick] = useState(0);
  const { retryNow, resetCycle } = useReadRecovery(
    authStatus === "authenticated",
    failureId,
    () => setRecoveryTick((tick) => tick + 1)
  );
  const [dashboardView, setDashboardView] = useState<DashboardViewResponse | null>(null);
  const dashboardViewRef = useRef(dashboardView);
  dashboardViewRef.current = dashboardView;
  const recentUploadsRef = useRef(recentUploads);
  recentUploadsRef.current = recentUploads;
  // Summary and charts come from this dashboard response. Recent uploads stay
  // separate because they are loaded from /uploads/recent.
  const summary = dashboardView?.summary ?? emptySummary;
  const sortedTransactions = dashboardView?.sorted_transactions ?? [];
  const merchantBreakdown = dashboardView?.merchant_breakdown ?? [];
  const categoryBreakdown = useMemo(
    () =>
      (dashboardView?.category_breakdown ?? []).map((item, index) => ({
        ...item,
        color: categoryPalette[index % categoryPalette.length],
      })),
    [dashboardView]
  );
  const categorySpendTotal = categoryBreakdown.reduce(
    (runningTotal, categoryItem) => runningTotal + categoryItem.total,
    0
  );
  const categoryPieBackground = buildPieChartBackground(categoryBreakdown);
  const merchantBarMaximum = merchantBreakdown[0]?.abs_total ?? 0;
  const activeCurrency = dashboardView?.active_currency ?? displayCurrency;
  const activeCurrencyLabel = describeCurrency(activeCurrency);
  const visibleRowsCount = sortedTransactions.length;
  const metrics = dashboardView?.metrics;
  const latestUpload = recentUploads[0];
  const hasSortedTransactions = sortedTransactions.length > 0;
  const unconvertedTransactions = dashboardView?.unconverted_transactions ?? [];
  const unconvertedCount = dashboardView
    ? (summary.unconverted_transaction_count ?? 0)
    : 0;
  const listedCurrencies = dashboardView
    ? (summary.unconverted_currencies ??
      [
        ...new Set(
          unconvertedTransactions.map((item) => item.original_currency)
        ),
      ])
    : [];
  const unconvertedCurrencies =
    [...listedCurrencies]
      .sort((left, right) => left.localeCompare(right))
      .join(", ") ||
    translate(locale, "unconvertedTransactionsCurrencyFallback");

  const metricCards: MetricCard[] = useMemo(
    () => [
      {
        label: translate(locale, "metricNetInView"),
        value: fm.formatCurrency(metrics?.selection_net ?? 0, activeCurrency),
        hint: translate(locale, "metricNetInViewHint"),
      },
      {
        label: translate(locale, "metricSpendOnly"),
        value: fm.formatCurrency(metrics?.selection_spend ?? 0, activeCurrency),
        hint: translate(locale, "metricSpendOnlyHint"),
      },
      {
        label: translate(locale, "metricRefunds"),
        value: fm.formatCurrency(metrics?.selection_refunds ?? 0, activeCurrency),
        hint: translate(locale, "metricRefundsHint"),
      },
      {
        label: translate(locale, "metricRowsInView"),
        value: fm.formatCount(visibleRowsCount),
        hint: translate(locale, "metricRowsHintTemplate", {
          pct: fm.formatPercent(metrics?.selection_coverage ?? 0),
        }),
      },
      {
        label: translate(locale, "metricModelTotal"),
        value: fm.formatCurrency(
          metrics?.model_total_in_active_currency ?? 0,
          activeCurrency
        ),
        hint: translate(locale, "metricModelTotalHintTemplate", {
          n: fm.formatCount(summary.number_of_statements),
        }),
      },
    ],
    [
      locale,
      fm,
      metrics?.selection_net,
      metrics?.selection_spend,
      metrics?.selection_refunds,
      activeCurrency,
      visibleRowsCount,
      metrics?.selection_coverage,
      metrics?.model_total_in_active_currency,
      summary.number_of_statements,
    ]
  );

  useEffect(() => {
    if (!shouldReleaseSearchPin(searchQuery, deferredSearchQuery, pinnedSearch)) {
      return;
    }
    setPinnedSearch(null);
  }, [deferredSearchQuery, pinnedSearch, searchQuery]);

  useEffect(() => {
    resetCycle();
  }, [dashboardLoadToken, resetCycle]);

  useEffect(() => {
    if (authStatus !== "authenticated") {
      setDashboardView(null);
      setRecentUploads([]);
      setError("");
      setDashboardError("");
      setDashboardStale(false);
      setFailureId(0);
      setIsDashboardLoading(authStatus === "loading");
      return;
    }

    const coordinator = requestCoordinatorRef.current;
    const ticket = coordinator.start();
    const filters = requestFilters;
    setIsDashboardLoading(true);

    void runGuardedDashboardLoad({
      coordinator,
      generation: ticket.generation,
      signal: ticket.signal,
      load: () =>
        loadDashboardSnapshot<RecentUploadsResponse, DashboardViewResponse>({
          filters,
          signal: ticket.signal,
          fetchJson: (path, signal) =>
            apiFetchJson(
              path,
              translate(localeRef.current, "errRequestFailed"),
              { signal }
            ),
        }),
      apply: (snapshot) => {
        const next = reduceDashboardLoad(
          {
            view: dashboardViewRef.current,
            uploads: recentUploadsRef.current,
          },
          snapshot,
          {
            staleData: translate(localeRef.current, "dashboardStaleData"),
            unableToRefresh: translate(localeRef.current, "errUnableToRefresh"),
          }
        );
        if (next.unauthorized) {
          setFailureId(0);
          void refreshSession();
        } else if (next.error || next.uploadsError) {
          setFailureId((id) => id + 1);
        } else {
          setFailureId(0);
        }
        setDashboardView(next.view);
        setRecentUploads(next.uploads);
        setError(next.error);
        setDashboardError(next.uploadsError);
        setDashboardStale(next.stale);
      },
      onError: (loadError) => {
        const hasView = dashboardViewRef.current !== null;
        const outcome = recoverableReadFailure(
          loadError,
          hasView,
          {
            staleData: translate(localeRef.current, "dashboardStaleData"),
            unableToRefresh: translate(localeRef.current, "errUnableToRefresh"),
          },
          { aborted: ticket.signal.aborted, latest: true }
        );
        if (outcome.action === "ignore") {
          return;
        }
        if (outcome.action === "unauthorized") {
          setFailureId(0);
          void refreshSession();
          return;
        }
        setError(outcome.message);
        setDashboardStale(hasView);
        setFailureId((id) => id + 1);
      },
      onSettled: () => {
        setIsDashboardLoading(false);
      },
    });

    return () => {
      ticket.abort();
    };
  }, [authStatus, dashboardLoadToken, recoveryTick, refreshSession, requestFilters]);

  function applyClearedSlicers(filters: DashboardFilters) {
    const nextFilters = refreshAfterUpload(filters);
    setSearchQuery(nextFilters.searchQuery);
    setSelectedMerchant(nextFilters.selectedMerchant);
    setAmountFilter(nextFilters.amountFilter as AmountFilter);
    setSortOption(nextFilters.sortOption as SortOption);
    setPinnedSearch(nextFilters.searchQuery);
    return nextFilters;
  }

  function clearSlicers() {
    applyClearedSlicers(latestFiltersRef.current);
  }

  if (authStatus === "loading") {
    return (
      <main className={styles.page}>
        <div className={styles.shell}>
          <section className={styles.reportArea}>
            <Title1>Checking session</Title1>
            <ProgressBar />
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
          body="Use a username and password to view only your own uploads, summaries, and dashboard data."
        />
      </AuthPage>
    );
  }

  return (
    <main className={styles.page}>
      <div
        className={styles.shell}
        data-dashboard-loading={isDashboardLoading ? "true" : "false"}
        data-testid="dashboard-shell"
      >
        <aside className={styles.sidebar}>
          <Card className={mergeClasses(styles.panel, styles.sidebarPanel)}>
            <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
              {translate(locale, "navTitle")}
            </div>
            <Title2>{translate(locale, "navTitle")}</Title2>
            <Body1 className={styles.emptyState}>{translate(locale, "navHint")}</Body1>
            <div className={styles.sidebarNavLinks}>
              <Link className={styles.sidebarNavLink} href="/">
                {translate(locale, "navDashboard")}
              </Link>
              <Link className={styles.sidebarNavLink} href="/data-entry">
                {translate(locale, "navDataEntry")}
              </Link>
              <Link className={styles.sidebarNavLink} href="/transactions">
                {translate(locale, "navTransactions")}
              </Link>
              <Link className={styles.sidebarNavLink} href="/batch-review">
                {translate(locale, "navBatchReview")}
              </Link>
              <Link className={styles.sidebarNavLink} href="/ai-insights">
                {translate(locale, "navAiInsights")}
              </Link>
              <Link className={styles.sidebarNavLink} href="/settings">
                {translate(locale, "navSettings")}
              </Link>
            </div>
          </Card>

          <SessionCard />

          <Card className={mergeClasses(styles.panel, styles.sidebarPanel)}>
            <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
              {translate(locale, "slicersEyebrow")}
            </div>
            <Title2>{translate(locale, "filtersTitle")}</Title2>

            <Field label={translate(locale, "searchLabel")}>
              <Input
                className={styles.searchField}
                onChange={(_, data) => setSearchQuery(data.value)}
                placeholder={translate(locale, "searchPlaceholder")}
                value={searchQuery}
              />
            </Field>

            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptyState}>
                {translate(locale, "timeRangeLabel")}
              </Body1>
              <div className={styles.pillGrid}>
                {dashboardTimeRangeOptions.map((rangeValue) => (
                  <Button
                    appearance={timeRange === rangeValue ? "primary" : "secondary"}
                    className={styles.pillButton}
                    key={rangeValue}
                    onClick={() => setTimeRange(rangeValue)}
                    size="small"
                  >
                    {translate(locale, timeRangeMessageKey(rangeValue))}
                  </Button>
                ))}
              </div>
            </div>

            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptyState}>
                {translate(locale, "amountDirection")}
              </Body1>
              <div className={styles.slicerButtons}>
                <Button
                  appearance={amountFilter === "all" ? "primary" : "secondary"}
                  onClick={() => setAmountFilter("all")}
                  size="small"
                >
                  {translate(locale, "amountAll")}
                </Button>
                <Button
                  appearance={amountFilter === "spend" ? "primary" : "secondary"}
                  onClick={() => setAmountFilter("spend")}
                  size="small"
                >
                  {translate(locale, "amountSpend")}
                </Button>
                <Button
                  appearance={amountFilter === "refunds" ? "primary" : "secondary"}
                  onClick={() => setAmountFilter("refunds")}
                  size="small"
                >
                  {translate(locale, "amountRefunds")}
                </Button>
              </div>
            </div>

            <Field label={translate(locale, "sortLabel")}>
              <select
                className={styles.selectControl}
                onChange={(event) => setSortOption(event.target.value as SortOption)}
                value={sortOption}
              >
                <option value="amount_desc">
                  {translate(locale, "sortAmountDesc")}
                </option>
                <option value="amount_asc">
                  {translate(locale, "sortAmountAsc")}
                </option>
                <option value="merchant">
                  {translate(locale, "sortMerchant")}
                </option>
                <option value="date">{translate(locale, "sortDate")}</option>
              </select>
            </Field>

            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptyState}>
                {translate(locale, "merchantSlicerLabel")}
              </Body1>
              {merchantBreakdown.length > 0 ? (
                <div className={styles.pillGrid}>
                  {merchantBreakdown.map((merchantItem) => (
                    <Button
                      appearance={
                        selectedMerchant === merchantItem.merchant
                          ? "primary"
                          : "secondary"
                      }
                      className={styles.pillButton}
                      key={merchantItem.merchant}
                      onClick={() =>
                        setSelectedMerchant((currentValue) =>
                          currentValue === merchantItem.merchant
                            ? null
                            : merchantItem.merchant
                        )
                      }
                      size="small"
                    >
                      {merchantItem.merchant}
                    </Button>
                  ))}
                </div>
              ) : dashboardView ? (
                <Body1 className={styles.emptySlicerState}>
                  {translate(locale, "merchantSlicerEmpty")}
                </Body1>
              ) : null}
            </div>

            <div className={styles.actions}>
              <Button appearance="secondary" onClick={clearSlicers} size="small">
                {translate(locale, "clearSlicers")}
              </Button>
              {dashboardView ? (
                <Badge appearance="outline" color="brand">
                  {fm.formatCount(visibleRowsCount)}{" "}
                  {translate(locale, "rowsInViewSuffix")}
                </Badge>
              ) : null}
            </div>
          </Card>

          <RecentStatementsCard
            fm={fm}
            hasSnapshot={dashboardView !== null}
            intlLocale={intlLocale}
            isDashboardLoading={isDashboardLoading}
            locale={locale}
            recentUploads={recentUploads}
            styles={styles}
          />

        </aside>

        <section className={styles.reportArea}>
          {dashboardView && dashboardStale && error ? (
            <div data-testid="dashboard-stale-warning">
              <MessageBar intent="warning">
                <MessageBarBody>{error}</MessageBarBody>
              </MessageBar>
              <Button appearance="secondary" onClick={retryNow} size="small">
                {translate(locale, "retryNow")}
              </Button>
            </div>
          ) : null}
          {dashboardView && !dashboardStale && error ? (
            <MessageBar intent="error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
          ) : null}
          {dashboardView && dashboardError ? (
            <div>
              <MessageBar intent="error">
                <MessageBarBody>{dashboardError}</MessageBarBody>
              </MessageBar>
              {!error && failureId > 0 ? (
                <Button appearance="secondary" onClick={retryNow} size="small">
                  {translate(locale, "retryNow")}
                </Button>
              ) : null}
            </div>
          ) : null}
          {unconvertedCount > 0 ? (
            <UnconvertedTransactionsNotice
              countLabel={fm.formatCount(unconvertedCount)}
              currencies={unconvertedCurrencies}
            />
          ) : null}
          <Card className={mergeClasses(styles.panel, styles.reportHeader)}>
            <div className={styles.reportHeaderTop}>
              <div className={styles.reportHeaderTitle}>
                {translate(locale, "appTitle")}
              </div>
              <div className={styles.headerMeta}>
                <div className={styles.languageRow}>
                  <Body1 className={styles.languageOption}>
                    {translate(locale, "currencyUsd")}
                  </Body1>
                  <Switch
                    aria-label={translate(locale, "currencyLabel")}
                    checked={displayCurrency === "TWD"}
                    onChange={(_, data) =>
                      setDisplayCurrency(data.checked ? "TWD" : "USD")
                    }
                  />
                  <Body1 className={styles.languageOption}>
                    {translate(locale, "currencyTwd")}
                  </Body1>
                </div>
                <div className={styles.languageRow}>
                  <Body1 className={styles.languageOption}>
                    {translate(locale, "languageEnglish")}
                  </Body1>
                  <Switch
                    aria-label={translate(locale, "languageLabel")}
                    checked={locale === "zh-Hant"}
                    onChange={(_, data) =>
                      setLocale(data.checked ? "zh-Hant" : "en")
                    }
                  />
                  <Body1 className={styles.languageOption}>
                    {translate(locale, "languageTraditionalChinese")}
                  </Body1>
                </div>
              </div>
            </div>

            <div className={styles.reportHeaderMain}>
              <div>
                <div
                  className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowLight)}
                >
                  {translate(locale, "overviewEyebrow")}
                </div>
                <Title1 className={styles.reportTitle}>
                  {translate(locale, "spendingOverviewTitle")}
                </Title1>
                <Body1 className={styles.reportLead}>
                  {translate(locale, "reportLeadBody")}
                </Body1>
                <Body1 className={styles.currencyContextNote}>
                  {translate(locale, "dashboardConvertedTotalsNoteTemplate", {
                    currency: activeCurrencyLabel,
                  })}
                </Body1>
                {dashboardView ? (
                  <Body1 className={styles.currencyContextNote}>
                    {translate(locale, "metricModelTotalHintTemplate", {
                      n: fm.formatCount(summary.number_of_statements),
                    })}
                  </Body1>
                ) : null}
              </div>

              <div className={styles.headerHighlights}>
                <div className={styles.headerHighlight}>
                  <div className={styles.headerHighlightLabel}>
                    {translate(locale, "storedModelLabel")}
                  </div>
                  <div className={styles.headerHighlightValue}>
                    {dashboardView
                      ? fm.formatCurrency(
                          metrics?.model_total_in_active_currency ?? 0,
                          activeCurrency
                        )
                      : "—"}
                  </div>
                </div>
                <div className={styles.headerHighlight}>
                  <div className={styles.headerHighlightLabel}>
                    {translate(locale, "lastRefreshLabel")}
                  </div>
                  <div className={styles.headerHighlightValue}>
                    {latestUpload
                      ? fm.formatUploadDate(latestUpload.uploaded_at)
                      : dashboardView
                        ? translate(locale, "waiting")
                        : "—"}
                  </div>
                </div>
              </div>
            </div>
            <div className={styles.reportTimeRangeRow}>
              <div className={styles.reportTimeRangeLabel}>
                {translate(locale, "timeRangeLabel")}
              </div>
              <div className={styles.reportTimeRangePills}>
                {dashboardTimeRangeOptions.map((rangeValue) => (
                  <Button
                    appearance={timeRange === rangeValue ? "primary" : "secondary"}
                    className={styles.pillButton}
                    key={rangeValue}
                    onClick={() => setTimeRange(rangeValue)}
                    size="small"
                  >
                    {translate(locale, timeRangeMessageKey(rangeValue))}
                  </Button>
                ))}
              </div>
            </div>
          </Card>

          {dashboardView === null ? (
            <Card
              className={mergeClasses(styles.panel, styles.reportHeader)}
              data-testid="dashboard-initial-recovery"
            >
              {error ? (
                <>
                  <Body1>{translate(locale, "initialRecovery")}</Body1>
                  <Button appearance="primary" onClick={retryNow} size="small">
                    {translate(locale, "retryNow")}
                  </Button>
                </>
              ) : (
                <ProgressBar />
              )}
            </Card>
          ) : (
          <>
          <div className={styles.metricGrid} data-testid="dashboard-metric-grid">
            {metricCards.slice(0, 3).map((metricCard) => (
              <div className={styles.metricCard} key={metricCard.label}>
                <div className={styles.metricAccent} />
                <div className={styles.metricLabel}>{metricCard.label}</div>
                <div className={styles.metricValue}>{metricCard.value}</div>
                <div className={styles.metricContext}>
                  {translate(locale, "metricDisplayCurrencyHintTemplate", {
                    currency: activeCurrencyLabel,
                  })}
                </div>
                <Body1 className={styles.metricHint}>{metricCard.hint}</Body1>
              </div>
            ))}
          </div>

          <DashboardCharts
            activeCurrency={activeCurrency}
            activeCurrencyLabel={activeCurrencyLabel}
            categoryBreakdown={categoryBreakdown}
            categoryPieBackground={categoryPieBackground}
            categorySpendTotal={categorySpendTotal}
            fm={fm}
            locale={locale}
            merchantBarMaximum={merchantBarMaximum}
            merchantBreakdown={merchantBreakdown}
            selectedMerchant={selectedMerchant}
            setSelectedMerchant={setSelectedMerchant}
            styles={styles}
          />

          <DashboardTransactionTable
            activeCurrency={activeCurrency}
            activeCurrencyLabel={activeCurrencyLabel}
            fm={fm}
            hasSortedTransactions={hasSortedTransactions}
            locale={locale}
            recentUploads={recentUploads}
            sortedTransactions={sortedTransactions}
            statementCount={summary.number_of_statements}
            styles={styles}
            visibleRowsCount={visibleRowsCount}
          />
          </>
          )}

        </section>
      </div>
    </main>
  );
}

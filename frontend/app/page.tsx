"use client";

import {
  ChangeEvent,
  DragEvent,
  KeyboardEvent,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import {
  Badge,
  Body1,
  Button,
  Card,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  ProgressBar,
  Spinner,
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

import { useLocale } from "./locale-provider";
import { translate, type MessageKey } from "./messages";


type Transaction = {
  date: string;
  merchant: string;
  amount: number;
  category: string;
};

type UploadResponse = {
  file_path: string;
  original_file_name: string;
  transactions: Transaction[];
  parse_reason?: string | null;
};

type SummaryResponse = {
  total_spending: number;
  number_of_transactions: number;
  average_transaction_amount: number;
  number_of_statements: number;
};

type RecentUpload = {
  file_name: string;
  display_name: string;
  transaction_count: number;
  total_amount: number;
  uploaded_at: string;
};

type RecentUploadsResponse = {
  uploads: RecentUpload[];
};

type TransactionsApiResponse = {
  transactions: Transaction[];
};

type AmountFilter = "all" | "spend" | "refunds";
type SortOption = "amount_desc" | "amount_asc" | "merchant" | "date";
type TimeRange = "week" | "month" | "quarter" | "year" | "5years" | "all";

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
    case "year":
      return "rangeYear";
    case "5years":
      return "range5Years";
    case "all":
    default:
      return "rangeAll";
  }
}

type MerchantBreakdown = {
  merchant: string;
  total: number;
  absTotal: number;
};

type DateBreakdown = {
  date: string;
  label: string;
  total: number;
  absTotal: number;
};

type CategoryBreakdown = {
  category: string;
  total: number;
  share: number;
  color: string;
};

type MetricCard = {
  label: string;
  value: string;
  hint: string;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const emptySummary: SummaryResponse = {
  total_spending: 0,
  number_of_transactions: 0,
  average_transaction_amount: 0,
  number_of_statements: 0,
};

const categoryPalette = [
  "#6b7dd6",
  "#2f8f95",
  "#9a85d1",
  "#5f95d6",
  "#5ca87a",
  "#b38c5a",
  "#a776b8",
];

function createFormatters(intlLocale: string) {
  const currencyFormatter = new Intl.NumberFormat(intlLocale, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
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
    formatCurrency(amount: number) {
      return currencyFormatter.format(amount);
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

function buildMerchantBreakdown(
  transactions: Transaction[],
  limit = 8
): MerchantBreakdown[] {
  const totalsByMerchant = new Map<string, number>();

  for (const transaction of transactions) {
    const currentTotal = totalsByMerchant.get(transaction.merchant) ?? 0;
    totalsByMerchant.set(transaction.merchant, currentTotal + transaction.amount);
  }

  return Array.from(totalsByMerchant.entries())
    .map(([merchant, total]) => ({
      merchant,
      total,
      absTotal: Math.abs(total),
    }))
    .sort((left, right) => right.absTotal - left.absTotal)
    .slice(0, limit);
}

function buildDateBreakdown(
  transactions: Transaction[],
  formatTransactionDateShort: (dateValue: string) => string
): DateBreakdown[] {
  const totalsByDate = new Map<string, number>();

  for (const transaction of transactions) {
    const currentTotal = totalsByDate.get(transaction.date) ?? 0;
    totalsByDate.set(transaction.date, currentTotal + transaction.amount);
  }

  return Array.from(totalsByDate.entries())
    .map(([date, total]) => ({
      date,
      label: formatTransactionDateShort(date),
      total,
      absTotal: Math.abs(total),
    }))
    .sort((left, right) => left.date.localeCompare(right.date));
}

function buildCategoryBreakdown(
  transactions: Transaction[],
  limit = 6,
  otherCategoryLabel = "Other"
): CategoryBreakdown[] {
  const totalsByCategory = new Map<string, number>();

  for (const transaction of transactions) {
    if (transaction.amount <= 0) {
      continue;
    }

    const currentTotal = totalsByCategory.get(transaction.category) ?? 0;
    totalsByCategory.set(transaction.category, currentTotal + transaction.amount);
  }

  const sortedCategories = Array.from(totalsByCategory.entries()).sort(
    (left, right) => right[1] - left[1]
  );
  const topCategories = sortedCategories.slice(0, limit);
  const remainingTotal = sortedCategories
    .slice(limit)
    .reduce((runningTotal, [, total]) => runningTotal + total, 0);
  const normalizedCategories =
    remainingTotal > 0
      ? [...topCategories, [otherCategoryLabel, remainingTotal] as const]
      : topCategories;
  const totalSpend = normalizedCategories.reduce(
    (runningTotal, [, total]) => runningTotal + total,
    0
  );

  return normalizedCategories.map(([category, total], index) => ({
    category,
    total,
    share: totalSpend > 0 ? total / totalSpend : 0,
    color: categoryPalette[index % categoryPalette.length],
  }));
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

function getLargestAbsoluteTransaction(transactions: Transaction[]) {
  return transactions.reduce((largestValue, transaction) => {
    const currentMagnitude = Math.abs(transaction.amount);
    return currentMagnitude > largestValue ? currentMagnitude : largestValue;
  }, 0);
}

function sortTransactions(
  transactions: Transaction[],
  sortOption: SortOption
): Transaction[] {
  const sortedTransactions = [...transactions];

  sortedTransactions.sort((left, right) => {
    switch (sortOption) {
      case "amount_asc":
        return left.amount - right.amount;
      case "merchant":
        return left.merchant.localeCompare(right.merchant);
      case "date":
        return left.date.localeCompare(right.date);
      case "amount_desc":
      default:
        return right.amount - left.amount;
    }
  });

  return sortedTransactions;
}

const useStyles = makeStyles({
  page: {
    minHeight: "100vh",
    backgroundColor: tokens.colorNeutralBackground2,
    backgroundImage:
      "radial-gradient(circle at 20% 0%, rgba(77, 105, 205, 0.14), rgba(77, 105, 205, 0) 45%), radial-gradient(circle at 100% 10%, rgba(0, 120, 212, 0.1), rgba(0, 120, 212, 0) 42%)",
    color: tokens.colorNeutralForeground1,
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
    gap: "16px",
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
  uploadCard: {
    backgroundColor: tokens.colorNeutralBackground1,
  },
  dropZone: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    paddingTop: "16px",
    paddingBottom: "16px",
    paddingLeft: "16px",
    paddingRight: "16px",
    cursor: "pointer",
    backgroundColor: tokens.colorNeutralBackground2,
    boxShadow: "inset 0 0 0 1px rgba(50, 49, 48, 0.08)",
    transitionDuration: "140ms",
    transitionProperty: "box-shadow, background-color, transform",
    transitionTimingFunction: "ease",
    ...shorthands.border("1px", "dashed", tokens.colorNeutralStrokeAccessible),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
    ":hover": {
      transform: "translateY(-1px)",
      backgroundColor: tokens.colorSubtleBackgroundHover,
      boxShadow: "inset 0 0 0 1px rgba(50, 49, 48, 0.16)",
    },
  },
  dropZoneActive: {
    backgroundColor: "rgba(237, 242, 255, 0.9)",
    ...shorthands.borderColor(tokens.colorBrandStroke1),
  },
  dropZoneTitle: {
    fontWeight: tokens.fontWeightSemibold,
  },
  dropZoneHint: {
    color: "#605e5c",
  },
  hiddenInput: {
    display: "none",
  },
  actions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
  },
  helperRow: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  uploadNextBar: {
    marginTop: "4px",
  },
  processActions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
    marginTop: "8px",
  },
  processPrimaryButton: {
    minWidth: "200px",
    fontWeight: tokens.fontWeightSemibold,
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
  filterSummary: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    flexWrap: "wrap",
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
  primaryVisualsGrid: {
    display: "grid",
    gridTemplateColumns:
      "minmax(0, 1.15fr) minmax(300px, 0.9fr) minmax(300px, 0.95fr)",
    gap: "8px",
    "@media (max-width: 1400px)": {
      gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    },
    "@media (max-width: 840px)": {
      gridTemplateColumns: "1fr",
    },
  },
  visualsGrid: {
    display: "grid",
    gridTemplateColumns: "minmax(0, 1.15fr) minmax(320px, 0.85fr)",
    gap: "8px",
    "@media (max-width: 1200px)": {
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
  pieLayout: {
    display: "grid",
    gridTemplateColumns: "minmax(220px, 260px) minmax(0, 1fr)",
    gap: "16px",
    alignItems: "center",
    "@media (max-width: 720px)": {
      gridTemplateColumns: "1fr",
    },
  },
  pieChartWrap: {
    width: "100%",
    maxWidth: "260px",
    aspectRatio: "1 / 1",
    justifySelf: "center",
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
  columnsCard: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  columnChart: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(48px, 1fr))",
    gap: "8px",
    minHeight: "230px",
    alignItems: "end",
  },
  columnItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "8px",
    minWidth: 0,
  },
  columnTrack: {
    width: "100%",
    height: "164px",
    display: "flex",
    alignItems: "flex-end",
    backgroundColor: tokens.colorNeutralBackground3,
    paddingLeft: "4px",
    paddingRight: "4px",
    ...shorthands.borderRadius("6px"),
  },
  columnBar: {
    width: "100%",
    backgroundColor: tokens.colorBrandBackground,
    minHeight: "8px",
    ...shorthands.borderRadius("4px 4px 0 0"),
  },
  columnBarNegative: {
    backgroundColor: "#d13438",
  },
  columnLabel: {
    fontSize: tokens.fontSizeBase200,
    color: "#605e5c",
  },
  columnValue: {
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
    color: "#323130",
  },
  insightsList: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "8px",
    "@media (max-width: 520px)": {
      gridTemplateColumns: "1fr",
    },
  },
  insightCell: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
    paddingTop: "12px",
    paddingBottom: "12px",
    paddingLeft: "12px",
    paddingRight: "12px",
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  insightLabel: {
    color: "#605e5c",
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.07em",
  },
  insightValue: {
    fontWeight: tokens.fontWeightSemibold,
    wordBreak: "break-word",
  },
  coverageBlock: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  coverageTrack: {
    height: "10px",
    backgroundColor: tokens.colorNeutralBackground3,
    overflow: "hidden",
    ...shorthands.borderRadius("999px"),
  },
  coverageFill: {
    height: "100%",
    backgroundColor: tokens.colorBrandBackground2,
    ...shorthands.borderRadius("999px"),
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
  filePath: {
    color: "#605e5c",
    wordBreak: "break-all",
  },
  tableWrap: {
    overflowX: "auto",
    overflowY: "auto",
    maxHeight: "600px",
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
    fontSize: tokens.fontSizeBase200,
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
  emptyState: {
    color: "#605e5c",
  },
});

async function fetchJson<T>(
  path: string,
  requestFailedFallback: string
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    cache: "no-store",
  });

  let payload: unknown = null;

  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof payload.detail === "string"
        ? payload.detail
        : requestFailedFallback;
    throw new Error(detail);
  }

  return payload as T;
}

export default function HomePage() {
  const styles = useStyles();
  const { locale, setLocale, intlLocale } = useLocale();
  const fm = useMemo(() => createFormatters(intlLocale), [intlLocale]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [filePath, setFilePath] = useState("");
  const [uploadedFileName, setUploadedFileName] = useState("");
  const [summary, setSummary] = useState<SummaryResponse>(emptySummary);
  const [recentUploads, setRecentUploads] = useState<RecentUpload[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [selectedMerchant, setSelectedMerchant] = useState<string | null>(null);
  const [amountFilter, setAmountFilter] = useState<AmountFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("amount_desc");
  const [pdfPassword, setPdfPassword] = useState("");
  const [lastUploadParseReason, setLastUploadParseReason] = useState<
    "empty_text" | "no_matching_lines" | null
  >(null);
  const [error, setError] = useState("");
  const [dashboardError, setDashboardError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isDashboardLoading, setIsDashboardLoading] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [isTransitionPending, startTransition] = useTransition();
  const deferredSearchQuery = useDeferredValue(searchQuery);

  const normalizedSearchQuery = deferredSearchQuery.trim().toLowerCase();

  const baseFilteredTransactions = transactions.filter((transaction) => {
    const matchesSearch =
      normalizedSearchQuery.length === 0 ||
      transaction.merchant.toLowerCase().includes(normalizedSearchQuery) ||
      transaction.category.toLowerCase().includes(normalizedSearchQuery) ||
      transaction.date.toLowerCase().includes(normalizedSearchQuery) ||
      transaction.amount.toFixed(2).includes(normalizedSearchQuery);

    const matchesAmountFilter =
      amountFilter === "all" ||
      (amountFilter === "spend" && transaction.amount >= 0) ||
      (amountFilter === "refunds" && transaction.amount < 0);

    return matchesSearch && matchesAmountFilter;
  });

  useEffect(() => {
    if (
      selectedMerchant &&
      !baseFilteredTransactions.some(
        (transaction) => transaction.merchant === selectedMerchant
      )
    ) {
      setSelectedMerchant(null);
    }
  }, [baseFilteredTransactions, selectedMerchant]);

  const filteredTransactions = selectedMerchant
    ? baseFilteredTransactions.filter(
        (transaction) => transaction.merchant === selectedMerchant
      )
    : baseFilteredTransactions;

  const sortedTransactions = sortTransactions(filteredTransactions, sortOption);
  const merchantBreakdown = buildMerchantBreakdown(baseFilteredTransactions, 7);
  const categoryBreakdown = buildCategoryBreakdown(
    filteredTransactions,
    6,
    translate(locale, "categoryOther")
  );
  const dateBreakdown = buildDateBreakdown(filteredTransactions, (dateValue) =>
    fm.formatTransactionDate(dateValue, true)
  ).slice(-10);
  const merchantBarMaximum = merchantBreakdown[0]?.absTotal ?? 0;
  const categorySpendTotal = categoryBreakdown.reduce(
    (runningTotal, categoryItem) => runningTotal + categoryItem.total,
    0
  );
  const categoryPieBackground = buildPieChartBackground(categoryBreakdown);
  const dateBarMaximum = dateBreakdown.reduce(
    (currentMaximum, currentItem) =>
      currentItem.absTotal > currentMaximum ? currentItem.absTotal : currentMaximum,
    0
  );

  const selectionNet = filteredTransactions.reduce(
    (runningTotal, transaction) => runningTotal + transaction.amount,
    0
  );
  const selectionSpend = filteredTransactions.reduce(
    (runningTotal, transaction) =>
      runningTotal + (transaction.amount > 0 ? transaction.amount : 0),
    0
  );
  const selectionRefunds = filteredTransactions.reduce(
    (runningTotal, transaction) =>
      runningTotal + (transaction.amount < 0 ? Math.abs(transaction.amount) : 0),
    0
  );
  const selectionAverage =
    filteredTransactions.length > 0 ? selectionNet / filteredTransactions.length : 0;
  const uploadNet = transactions.reduce(
    (runningTotal, transaction) => runningTotal + transaction.amount,
    0
  );
  const largestTransactionAmount = getLargestAbsoluteTransaction(filteredTransactions);
  const selectionCoverage =
    transactions.length > 0 ? filteredTransactions.length / transactions.length : 0;
  const currentStatementLabel =
    uploadedFileName ||
    selectedFile?.name ||
    translate(locale, "noStatementSelected");
  const latestUpload = recentUploads[0];

  const metricCards: MetricCard[] = useMemo(
    () => [
      {
        label: translate(locale, "metricNetInView"),
        value: fm.formatCurrency(selectionNet),
        hint: translate(locale, "metricNetInViewHint"),
      },
      {
        label: translate(locale, "metricSpendOnly"),
        value: fm.formatCurrency(selectionSpend),
        hint: translate(locale, "metricSpendOnlyHint"),
      },
      {
        label: translate(locale, "metricRefunds"),
        value: fm.formatCurrency(selectionRefunds),
        hint: translate(locale, "metricRefundsHint"),
      },
      {
        label: translate(locale, "metricRowsInView"),
        value: fm.formatCount(filteredTransactions.length),
        hint: translate(locale, "metricRowsHintTemplate", {
          pct: fm.formatPercent(selectionCoverage),
        }),
      },
      {
        label: translate(locale, "metricModelTotal"),
        value: fm.formatCurrency(summary.total_spending),
        hint: translate(locale, "metricModelTotalHintTemplate", {
          n: fm.formatCount(summary.number_of_statements),
        }),
      },
    ],
    [
      locale,
      fm,
      selectionNet,
      selectionSpend,
      selectionRefunds,
      filteredTransactions.length,
      selectionCoverage,
      summary.total_spending,
      summary.number_of_statements,
    ]
  );

  async function loadDashboardData(showLoadingState: boolean) {
    if (showLoadingState) {
      setIsDashboardLoading(true);
    }

    try {
      const [summaryData, recentUploadsData] = await Promise.all([
        fetchJson<SummaryResponse>(
          `/summary?range=${encodeURIComponent(timeRange)}`,
          translate(locale, "errRequestFailed")
        ),
        fetchJson<RecentUploadsResponse>(
          "/uploads/recent?limit=6",
          translate(locale, "errRequestFailed")
        ),
      ]);

      setSummary(summaryData);
      setRecentUploads(recentUploadsData.uploads);
      setDashboardError("");
    } catch (dashboardLoadError) {
      setDashboardError(
        dashboardLoadError instanceof Error
          ? dashboardLoadError.message
          : translate(locale, "errDashboardUnavailable")
      );
    } finally {
      if (showLoadingState) {
        setIsDashboardLoading(false);
      }
    }
  }

  async function loadTransactionsData(showLoadingState: boolean) {
    if (showLoadingState) {
      setIsDashboardLoading(true);
    }

    try {
      const transactionData = await fetchJson<TransactionsApiResponse>(
        `/transactions?range=${encodeURIComponent(timeRange)}&limit=5000`,
        translate(locale, "errRequestFailed")
      );
      setTransactions(transactionData.transactions);
      setError("");
    } catch (transactionLoadError) {
      setTransactions([]);
      setError(
        transactionLoadError instanceof Error
          ? transactionLoadError.message
          : translate(locale, "errRequestFailed")
      );
    } finally {
      if (showLoadingState) {
        setIsDashboardLoading(false);
      }
    }
  }

  useEffect(() => {
    void Promise.all([loadDashboardData(true), loadTransactionsData(true)]);
  }, [timeRange]);

  function handleNextFile(nextFile: File | null) {
    if (!nextFile) {
      return;
    }

    const isPdfFile =
      nextFile.type === "application/pdf" ||
      nextFile.name.toLowerCase().endsWith(".pdf");

    if (!isPdfFile) {
      setSelectedFile(null);
      setError(translate(locale, "errOnlyPdf"));
      return;
    }

    setSelectedFile(nextFile);
    setError("");
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    handleNextFile(event.target.files?.[0] ?? null);
  }

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    handleNextFile(event.dataTransfer.files?.[0] ?? null);
  }

  function handleDropZoneKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openFilePicker();
    }
  }

  function clearSlicers() {
    setSearchQuery("");
    setSelectedMerchant(null);
    setAmountFilter("all");
    setSortOption("amount_desc");
  }

  async function handleUpload() {
    if (!selectedFile) {
      setError(translate(locale, "errSelectPdf"));
      return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("password", pdfPassword);

    setIsLoading(true);
    setError("");
    setLastUploadParseReason(null);

    try {
      const response = await fetch(`${apiBaseUrl}/upload`, {
        method: "POST",
        body: formData,
      });

      const data = (await response.json()) as UploadResponse | { detail?: string };

      if (!response.ok) {
        const detail =
          "detail" in data && typeof data.detail === "string"
            ? data.detail
            : translate(locale, "errUploadFailed");
        throw new Error(detail);
      }

      const uploadData = data as UploadResponse;

      startTransition(() => {
        setFilePath(uploadData.file_path);
        setUploadedFileName(uploadData.original_file_name);
        const reason = uploadData.parse_reason;
        setLastUploadParseReason(
          reason === "empty_text" || reason === "no_matching_lines"
            ? reason
            : null
        );
        setSelectedFile(null);
        setPdfPassword("");
        clearSlicers();
      });

      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      await Promise.all([loadDashboardData(false), loadTransactionsData(false)]);
    } catch (uploadError) {
      setTransactions([]);
      setFilePath("");
      setUploadedFileName("");
      setLastUploadParseReason(null);
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : translate(locale, "errUnexpectedUpload")
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <aside className={styles.sidebar}>
          <Card className={mergeClasses(styles.panel, styles.sidebarPanel, styles.uploadCard)}>
            <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
              {translate(locale, "reportPaneEyebrow")}
            </div>
            <Title2>{translate(locale, "statementImportTitle")}</Title2>
            <Body1 className={styles.emptyState}>
              {translate(locale, "statementImportBody")}
            </Body1>

            <Field
              label={translate(locale, "fieldStatementPdfLabel")}
              hint={translate(locale, "fieldStatementPdfHint")}
            >
              <>
                <input
                  accept="application/pdf,.pdf"
                  className={styles.hiddenInput}
                  onChange={handleFileChange}
                  ref={fileInputRef}
                  type="file"
                />
                <div
                  className={mergeClasses(
                    styles.dropZone,
                    isDragging && styles.dropZoneActive
                  )}
                  onClick={openFilePicker}
                  onDragEnter={() => setIsDragging(true)}
                  onDragLeave={() => setIsDragging(false)}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setIsDragging(true);
                  }}
                  onDrop={handleDrop}
                  onKeyDown={handleDropZoneKeyDown}
                  role="button"
                  tabIndex={0}
                >
                  <div className={styles.dropZoneTitle}>
                    {selectedFile
                      ? selectedFile.name
                      : translate(locale, "dropZoneEmptyTitle")}
                  </div>
                  <Body1 className={styles.dropZoneHint}>
                    {selectedFile
                      ? translate(locale, "dropZoneReadyHint")
                      : translate(locale, "dropZoneIdleHint")}
                  </Body1>
                </div>
              </>
            </Field>

            {selectedFile && !isLoading ? (
              <MessageBar className={styles.uploadNextBar} intent="info">
                <MessageBarBody>
                  <MessageBarTitle>
                    {translate(locale, "uploadNextStepTitle")}
                  </MessageBarTitle>
                  {translate(locale, "uploadNextStepBody")}
                </MessageBarBody>
              </MessageBar>
            ) : null}

            <Field
              hint={translate(locale, "fieldPdfPasswordHint")}
              label={translate(locale, "fieldPdfPasswordLabel")}
            >
              <Input
                appearance="outline"
                autoComplete="off"
                className={styles.searchField}
                onChange={(_, data) => setPdfPassword(data.value)}
                placeholder={translate(locale, "placeholderPdfPassword")}
                type="password"
                value={pdfPassword}
              />
            </Field>

            {isLoading ? <ProgressBar /> : null}

            <div className={styles.processActions}>
              <Button
                appearance="primary"
                className={styles.processPrimaryButton}
                disabled={isLoading}
                onClick={handleUpload}
                size="large"
              >
                {isLoading
                  ? translate(locale, "btnRefreshing")
                  : translate(locale, "btnProcessPdf")}
              </Button>
              <Button
                appearance="secondary"
                disabled={isLoading}
                onClick={openFilePicker}
                size="large"
              >
                {translate(locale, "btnBrowse")}
              </Button>
              {isLoading ? (
                <Spinner label={translate(locale, "spinnerParsingLabel")} size="tiny" />
              ) : null}
            </div>

            <div className={styles.helperRow}>
              <Badge appearance="filled" color="warning">
                {translate(locale, "badgeManualRefresh")}
              </Badge>
              <Badge appearance="outline" color="brand">
                {translate(locale, "badgePdfSource")}
              </Badge>
              <Badge appearance="outline" color="informative">
                {translate(locale, "badgePostgresModel")}
              </Badge>
            </div>

            {error ? (
              <MessageBar intent="error">
                <MessageBarBody>
                  <MessageBarTitle>
                    {translate(locale, "msgRefreshFailedTitle")}
                  </MessageBarTitle>
                  {error}
                </MessageBarBody>
              </MessageBar>
            ) : null}
          </Card>

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
                {(
                  [
                    "week",
                    "month",
                    "quarter",
                    "year",
                    "5years",
                    "all",
                  ] as const
                ).map((rangeValue) => (
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
              ) : (
                <Body1 className={styles.emptySlicerState}>
                  {translate(locale, "merchantSlicerEmpty")}
                </Body1>
              )}
            </div>

            <div className={styles.actions}>
              <Button appearance="secondary" onClick={clearSlicers} size="small">
                {translate(locale, "clearSlicers")}
              </Button>
              <Badge appearance="outline" color="brand">
                {fm.formatCount(filteredTransactions.length)}{" "}
                {translate(locale, "rowsInViewSuffix")}
              </Badge>
            </div>
          </Card>

          <Card className={mergeClasses(styles.panel, styles.sidebarPanel)}>
            <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
              {translate(locale, "historyEyebrow")}
            </div>
            <Title2>{translate(locale, "recentStatementsTitle")}</Title2>

            {dashboardError ? (
              <MessageBar intent="warning">
                <MessageBarBody>
                  <MessageBarTitle>
                    {translate(locale, "dashboardUnavailableTitle")}
                  </MessageBarTitle>
                  {dashboardError}
                </MessageBarBody>
              </MessageBar>
            ) : null}

            {isDashboardLoading ? (
              <Spinner label={translate(locale, "loadingHistoryLabel")} />
            ) : recentUploads.length > 0 ? (
              <div className={styles.historyList}>
                {recentUploads.map((upload) => (
                  <div className={styles.historyItem} key={upload.file_name}>
                    <div className={styles.historyTop}>
                      <div>
                        <div className={styles.historyName}>{upload.display_name}</div>
                        <div className={styles.historyMeta}>
                          {fm.formatUploadDate(upload.uploaded_at)}
                        </div>
                      </div>
                      <Badge appearance="outline" color="brand">
                        {fm.formatCount(upload.transaction_count)}{" "}
                        {translate(locale, "historyRowsWord")}
                      </Badge>
                    </div>
                    <div className={styles.historyStats}>
                      <Badge appearance="filled" color="informative">
                        {fm.formatCurrency(upload.total_amount)}
                      </Badge>
                      <Badge appearance="outline" color="success">
                        {upload.file_name}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Body1 className={styles.emptyState}>
                {translate(locale, "noUploadsYet")}
              </Body1>
            )}
          </Card>
        </aside>

        <section className={styles.reportArea}>
          <Card className={mergeClasses(styles.panel, styles.reportHeader)}>
            <div className={styles.reportHeaderTop}>
              <div className={styles.reportHeaderTitle}>
                {translate(locale, "appTitle")}
              </div>
              <div className={styles.headerMeta}>
                <Badge appearance="outline" color="informative">
                  {translate(locale, "badgeCurrentFilePrefix")}{" "}
                  {currentStatementLabel}
                </Badge>
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
              </div>

              <div className={styles.headerHighlights}>
                <div className={styles.headerHighlight}>
                  <div className={styles.headerHighlightLabel}>
                    {translate(locale, "storedModelLabel")}
                  </div>
                  <div className={styles.headerHighlightValue}>
                    {fm.formatCurrency(summary.total_spending)}
                  </div>
                </div>
                <div className={styles.headerHighlight}>
                  <div className={styles.headerHighlightLabel}>
                    {translate(locale, "lastRefreshLabel")}
                  </div>
                  <div className={styles.headerHighlightValue}>
                    {latestUpload
                      ? fm.formatUploadDate(latestUpload.uploaded_at)
                      : translate(locale, "waiting")}
                  </div>
                </div>
              </div>
            </div>
          </Card>

          <div className={styles.filterSummary}>
            <Badge appearance="outline" color="brand">
              {translate(locale, "merchantSummaryPrefix")}{" "}
              {selectedMerchant ?? translate(locale, "merchantAll")}
            </Badge>
            <Badge appearance="outline" color="informative">
              {translate(locale, "amountSummaryPrefix")}{" "}
              {translate(locale, amountFilterMessageKey(amountFilter))}
            </Badge>
            <Badge appearance="outline" color="success">
              {translate(locale, "sortSummaryPrefix")}{" "}
              {translate(locale, sortOptionMessageKey(sortOption))}
            </Badge>
            {searchQuery ? (
              <Badge appearance="outline" color="brand">
                {translate(locale, "searchSummaryPrefix")} {searchQuery}
              </Badge>
            ) : null}
          </div>

          <div className={styles.metricGrid}>
            {metricCards.map((metricCard) => (
              <div className={styles.metricCard} key={metricCard.label}>
                <div className={styles.metricAccent} />
                <div className={styles.metricLabel}>{metricCard.label}</div>
                <div className={styles.metricValue}>{metricCard.value}</div>
                <Body1 className={styles.metricHint}>{metricCard.hint}</Body1>
              </div>
            ))}
          </div>

          <div className={styles.primaryVisualsGrid}>
            <Card className={mergeClasses(styles.panel, styles.visualCard)}>
              <div className={styles.cardHeader}>
                <div className={styles.cardHeaderTitle}>
                  <Title2>{translate(locale, "merchantContributionTitle")}</Title2>
                </div>
                <Badge appearance="outline" color="brand">
                  {translate(locale, "badgeCrossFilter")}
                </Badge>
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
                                  ? (merchantItem.absTotal / merchantBarMaximum) * 100
                                  : 0
                              }%`,
                            }}
                          />
                        </div>
                      </div>
                      <div className={styles.merchantValue}>
                        {fm.formatCurrency(merchantItem.total)}
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
                          {fm.formatCurrency(categorySpendTotal)}
                        </div>
                        <div className={styles.pieCenterLabel}>
                          {translate(locale, "pieCenterSpendInView")}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className={styles.categoryLegend}>
                    {categoryBreakdown.map((categoryItem) => (
                      <div
                        className={styles.categoryLegendRow}
                        key={categoryItem.category}
                      >
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
                          {fm.formatCurrency(categoryItem.total)}
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

            <Card className={mergeClasses(styles.panel, styles.columnsCard)}>
              <div className={styles.cardHeader}>
                <div className={styles.cardHeaderTitle}>
                  <Title2>{translate(locale, "dailyNetTitle")}</Title2>
                </div>
                <Badge appearance="outline" color="informative">
                  {translate(locale, "badgeBasedOnSelection")}
                </Badge>
              </div>

              {dateBreakdown.length > 0 ? (
                <div className={styles.columnChart}>
                  {dateBreakdown.map((dateItem) => (
                    <div className={styles.columnItem} key={dateItem.date}>
                      <div className={styles.columnValue}>
                        {fm.formatCurrency(dateItem.total)}
                      </div>
                      <div className={styles.columnTrack}>
                        <div
                          className={mergeClasses(
                            styles.columnBar,
                            dateItem.total < 0 && styles.columnBarNegative
                          )}
                          style={{
                            height: `${
                              dateBarMaximum > 0
                                ? Math.max(
                                    (dateItem.absTotal / dateBarMaximum) * 100,
                                    6
                                  )
                                : 6
                            }%`,
                          }}
                        />
                      </div>
                      <div className={styles.columnLabel}>{dateItem.label}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <Body1 className={styles.emptyState}>
                  {translate(locale, "emptyDateBuckets")}
                </Body1>
              )}
            </Card>
          </div>

          <div className={styles.visualsGrid}>
            <Card className={mergeClasses(styles.panel, styles.visualCard)}>
              <div className={styles.cardHeader}>
                <div className={styles.cardHeaderTitle}>
                  <div
                    className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}
                  >
                    {translate(locale, "visual4Eyebrow")}
                  </div>
                  <Title2>{translate(locale, "selectionDetailsTitle")}</Title2>
                </div>
                <Badge appearance="outline" color="warning">
                  {translate(locale, "badgeReactiveSlicers")}
                </Badge>
              </div>

              <div className={styles.insightsList}>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightCurrentStatement")}
                  </div>
                  <div className={styles.insightValue}>{currentStatementLabel}</div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightAvgTransaction")}
                  </div>
                  <div className={styles.insightValue}>
                    {fm.formatCurrency(selectionAverage)}
                  </div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightLargestAmount")}
                  </div>
                  <div className={styles.insightValue}>
                    {fm.formatCurrency(largestTransactionAmount)}
                  </div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightUploadNet")}
                  </div>
                  <div className={styles.insightValue}>{fm.formatCurrency(uploadNet)}</div>
                </div>
              </div>

              <div className={styles.coverageBlock}>
                <div className={styles.cardHeader}>
                  <Body1>{translate(locale, "visibleShareTitle")}</Body1>
                  <Body1>{fm.formatPercent(selectionCoverage)}</Body1>
                </div>
                <div className={styles.coverageTrack}>
                  <div
                    className={styles.coverageFill}
                    style={{ width: `${selectionCoverage * 100}%` }}
                  />
                </div>
              </div>
            </Card>

            <Card className={mergeClasses(styles.panel, styles.visualCard)}>
              <div className={styles.cardHeader}>
                <div className={styles.cardHeaderTitle}>
                  <div
                    className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}
                  >
                    {translate(locale, "modelContextEyebrow")}
                  </div>
                  <Title2>{translate(locale, "datasetSnapshotTitle")}</Title2>
                </div>
                <Badge appearance="outline" color="brand">
                  {translate(locale, "badgeBackendAggregate")}
                </Badge>
              </div>

              <div className={styles.insightsList}>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightStatementsStored")}
                  </div>
                  <div className={styles.insightValue}>
                    {fm.formatCount(summary.number_of_statements)}
                  </div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightRowsStored")}
                  </div>
                  <div className={styles.insightValue}>
                    {fm.formatCount(summary.number_of_transactions)}
                  </div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightAllTimeAvg")}
                  </div>
                  <div className={styles.insightValue}>
                    {fm.formatCurrency(summary.average_transaction_amount)}
                  </div>
                </div>
                <div className={styles.insightCell}>
                  <div className={styles.insightLabel}>
                    {translate(locale, "insightMostRecentLoad")}
                  </div>
                  <div className={styles.insightValue}>
                    {latestUpload
                      ? latestUpload.display_name
                      : translate(locale, "waitingForData")}
                  </div>
                </div>
              </div>
            </Card>
          </div>

          <Card className={mergeClasses(styles.panel, styles.tableCard)}>
            <div className={styles.tableHeader}>
              <div className={styles.cardHeaderTitle}>
                <div className={mergeClasses(styles.sectionEyebrow, styles.sectionEyebrowDark)}>
                  {translate(locale, "detailMatrixEyebrow")}
                </div>
                <Title2>{translate(locale, "transactionsTitle")}</Title2>
              </div>
              <div className={styles.tableTools}>
                <Badge appearance="filled" color="brand">
                  {fm.formatCount(sortedTransactions.length)}{" "}
                  {translate(locale, "visibleSuffix")}
                </Badge>
                <Badge appearance="outline" color="informative">
                  {translate(locale, "merchantSummaryPrefix")}{" "}
                  {selectedMerchant ?? translate(locale, "merchantAll")}
                </Badge>
              </div>
            </div>

            {filePath ? (
              <Body1 className={styles.filePath}>
                {translate(locale, "storedFilePrefix")} {filePath}
              </Body1>
            ) : null}

            {isTransitionPending ? <ProgressBar /> : null}

            {transactions.length > 0 ? (
              sortedTransactions.length > 0 ? (
                <div className={styles.tableWrap}>
                  <Table
                    aria-label={translate(locale, "tableAriaLabel")}
                    className={styles.dataTable}
                  >
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
                        <TableRow
                          className={index % 2 === 1 ? styles.rowAlt : undefined}
                          key={`${transaction.date}-${transaction.merchant}-${index}`}
                        >
                          <TableCell className={styles.tableCell}>
                            {fm.formatTransactionDate(transaction.date)}
                          </TableCell>
                          <TableCell
                            className={mergeClasses(styles.tableCell, styles.merchantCell)}
                          >
                            {transaction.merchant}
                          </TableCell>
                          <TableCell className={styles.tableCell}>
                            {transaction.category}
                          </TableCell>
                          <TableCell
                            className={mergeClasses(
                              styles.tableCell,
                              transaction.amount < 0
                                ? styles.amountNegative
                                : styles.amountPositive
                            )}
                          >
                            {fm.formatCurrency(transaction.amount)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <Body1 className={styles.emptyState}>
                  {translate(locale, "emptySlicerNoRows")}
                </Body1>
              )
            ) : filePath ? (
              <>
                <MessageBar intent="warning">
                  <MessageBarBody>
                    <MessageBarTitle>
                      {translate(locale, "parseWarningTitle")}
                    </MessageBarTitle>
                    {lastUploadParseReason === "empty_text"
                      ? translate(locale, "parseReasonEmptyText")
                      : translate(locale, "parseReasonNoMatchingLines")}
                  </MessageBarBody>
                </MessageBar>
                <Body1 className={styles.emptyState}>
                  {translate(locale, "emptyParsedZeroRows")}
                </Body1>
              </>
            ) : (
              <Body1 className={styles.emptyState}>
                {translate(locale, "emptyNoUpload")}
              </Body1>
            )}
          </Card>
        </section>
      </div>
    </main>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Badge,
  Body1,
  Button,
  Card,
  Input,
  MessageBar,
  MessageBarBody,
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

import { apiFetch, ApiError, apiFetchJson } from "../api";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import { useLocale } from "../locale-provider";
import { translate } from "../messages";

type BatchImportRun = {
  id: number;
  imported_at: string;
  statement_count: number;
  transaction_count: number;
  needs_review_count: number;
  csv_output_path: string;
  sqlite_output_path: string;
  pdf_source_dir: string;
  text_source_dir: string;
};

type BatchTransaction = {
  id: number;
  import_run_id: number;
  statement_file: string;
  bank: string;
  card_last4: string | null;
  transaction_date: string;
  posted_date: string | null;
  merchant_raw: string;
  amount: number;
  currency: string;
  transaction_type: string;
  category: string;
  exclude_from_spending: boolean;
  needs_review: boolean;
};

type BatchImportLatestResponse = {
  run: BatchImportRun | null;
};

type SkippedFile = {
  statement_file: string;
  reason: string;
  detail?: string;
};

type BatchImportRunPayload = BatchImportRun & {
  run_id?: number;
  skipped_files?: SkippedFile[];
};

const SKIP_REASON_LABELS: Record<string, string> = {
  password_required: "Password required",
  decrypt_error: "Could not decrypt PDF",
  pdf_read_error: "PDF read error",
  read_error: "File read error",
  processing_error: "Processing error",
  unsupported_format: "Unsupported format",
  no_transactions_found: "No transactions found",
};

function normalizeBatchRun(run: BatchImportRunPayload): BatchImportRun {
  return {
    ...run,
    id: run.id ?? run.run_id ?? 0,
  };
}

function groupSkippedFiles(skipped: SkippedFile[]) {
  const groups = new Map<string, SkippedFile[]>();
  for (const item of skipped) {
    const bucket = groups.get(item.reason) ?? [];
    bucket.push(item);
    groups.set(item.reason, bucket);
  }
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
}

type BatchTransactionsResponse = {
  transactions: BatchTransaction[];
};

type NeedsReviewMerchantSummary = {
  merchant_raw: string;
  transaction_count: number;
  sample_category: string;
  sample_excluded: boolean;
  suggested_category: string;
  can_auto_apply: boolean;
};

type NeedsReviewMerchantsResponse = {
  merchants: NeedsReviewMerchantSummary[];
};

type DraftState = {
  merchant_raw: string;
  category: string;
  exclude_from_spending: boolean;
};

const reviewCategories = [
  "Payment / Credit",
  "Education / Certification",
  "Tax / Accounting",
  "Shopping / Department Store",
  "Shopping / Retail",
  "Shopping / Online Retail",
  "Subscription / Membership",
  "Government / Consular",
  "Insurance",
  "Groceries",
  "Dining / Food",
  "Transportation",
  "Travel",
  "Utilities / Telecom",
  "Healthcare / Medical",
  "Bank Fees / Interest",
  "Cash Advance / ATM",
  "Merchant Missing From PDF",
  "Other Spending",
] as const;

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
  content: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05), 0 10px 28px rgba(0, 0, 0, 0.06)",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusXLarge),
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
  actions: {
    display: "flex",
    gap: "10px",
    alignItems: "center",
    flexWrap: "wrap",
  },
  metaGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
    gap: "12px",
  },
  metaPaths: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    marginTop: "12px",
    paddingTop: "12px",
    ...shorthands.borderTop("1px", "solid", tokens.colorNeutralStroke2),
  },
  metaItem: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
    minWidth: 0,
  },
  metaLabel: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
  },
  metaPathValue: {
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase300,
    wordBreak: "break-all",
    overflowWrap: "anywhere",
  },
  tableWrap: {
    overflowX: "auto",
  },
  tableHeaderCell: {
    backgroundColor: tokens.colorNeutralBackground2,
    fontWeight: tokens.fontWeightSemibold,
  },
  merchantInput: {
    width: "100%",
    minWidth: "200px",
    height: "36px",
    paddingLeft: "10px",
    paddingRight: "10px",
    fontSize: tokens.fontSizeBase200,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    backgroundColor: tokens.colorNeutralBackground1,
    marginBottom: "6px",
  },
  selectControl: {
    width: "220px",
    height: "36px",
    paddingLeft: "10px",
    paddingRight: "10px",
    fontSize: tokens.fontSizeBase200,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    backgroundColor: tokens.colorNeutralBackground1,
  },
  excludeToggle: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    whiteSpace: "nowrap",
  },
  quietText: {
    color: tokens.colorNeutralForeground3,
  },
  merchantCell: {
    minWidth: "220px",
  },
  merchantRuleRow: {
    display: "grid",
    gridTemplateColumns: "minmax(200px, 1fr) auto auto",
    gap: "10px",
    alignItems: "center",
  },
  cleanupRow: {
    display: "flex",
    flexWrap: "wrap",
    gap: "10px",
    alignItems: "center",
    justifyContent: "space-between",
  },
});

function formatCurrency(amount: number, currencyCode: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currencyCode,
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatDateTime(timestamp: string) {
  const parsedDate = new Date(timestamp);
  if (Number.isNaN(parsedDate.getTime())) {
    return timestamp;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsedDate);
}

export default function BatchReviewPage() {
  const styles = useStyles();
  const pathname = usePathname();
  const { locale } = useLocale();
  const { refreshSession, status, user } = useAuth();

  const [run, setRun] = useState<BatchImportRun | null>(null);
  const [transactions, setTransactions] = useState<BatchTransaction[]>([]);
  const [drafts, setDrafts] = useState<Record<number, DraftState>>({});
  const [needsReviewOnly, setNeedsReviewOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [skippedFiles, setSkippedFiles] = useState<SkippedFile[]>([]);
  const [needsReviewMerchants, setNeedsReviewMerchants] = useState<
    NeedsReviewMerchantSummary[]
  >([]);
  const [merchantCategoryDrafts, setMerchantCategoryDrafts] = useState<
    Record<string, string>
  >({});
  const [bulkSavingMerchant, setBulkSavingMerchant] = useState<string | null>(null);
  const [digitNoisePendingCount, setDigitNoisePendingCount] = useState(0);
  const [dismissingDigitNoise, setDismissingDigitNoise] = useState(false);
  const [autoCategorizing, setAutoCategorizing] = useState(false);

  async function loadBatchState(reviewOnly: boolean) {
    setLoading(true);
    setError("");
    try {
      const latestPayload = await apiFetchJson<BatchImportLatestResponse>(
        "/batch-imports/latest",
        "Failed to load batch-import status."
      );
      const activeRunId = latestPayload.run?.id ?? null;
      const [transactionsPayload, merchantsPayload, digitNoisePayload] =
        await Promise.all([
          apiFetchJson<BatchTransactionsResponse>(
            `/batch-imports/transactions?needs_review_only=${reviewOnly}&limit=500`,
            "Failed to load batch-import transactions."
          ),
          activeRunId
            ? apiFetchJson<NeedsReviewMerchantsResponse>(
                `/batch-imports/needs-review-merchants?run_id=${activeRunId}&limit=20`,
                "Failed to load review merchants."
              )
            : Promise.resolve({ merchants: [] }),
          activeRunId
            ? apiFetchJson<{ pending_count: number }>(
                `/batch-imports/digit-noise-pending-count?run_id=${activeRunId}`,
                "Failed to load junk merchant count."
              )
            : Promise.resolve({ pending_count: 0 }),
        ]);
      setRun(latestPayload.run);
      setTransactions(transactionsPayload.transactions);
      setNeedsReviewMerchants(merchantsPayload.merchants);
      setDigitNoisePendingCount(digitNoisePayload.pending_count);
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 401) {
        await refreshSession();
        return;
      }
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Failed to load batch-review data."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (status === "authenticated") {
      void loadBatchState(needsReviewOnly);
      return;
    }

    setLoading(status === "loading");
    setRun(null);
    setTransactions([]);
  }, [needsReviewOnly, refreshSession, status]);

  const activeReviewCount = useMemo(
    () => transactions.filter((transaction) => transaction.needs_review).length,
    [transactions]
  );

  function getDraft(transaction: BatchTransaction): DraftState {
    return (
      drafts[transaction.id] ?? {
        merchant_raw: transaction.merchant_raw,
        category: transaction.category,
        exclude_from_spending: transaction.exclude_from_spending,
      }
    );
  }

  function updateDraft(transactionId: number, nextDraft: DraftState) {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [transactionId]: nextDraft,
    }));
  }

  function merchantDraftCategory(item: NeedsReviewMerchantSummary): string {
    if (merchantCategoryDrafts[item.merchant_raw]) {
      return merchantCategoryDrafts[item.merchant_raw];
    }
    if (item.can_auto_apply && item.suggested_category) {
      return item.suggested_category;
    }
    return item.sample_category;
  }

  const autoApplicableMerchantCount = useMemo(
    () => needsReviewMerchants.filter((item) => item.can_auto_apply).length,
    [needsReviewMerchants]
  );

  async function onAutoCategorizeKnown() {
    if (!run) {
      return;
    }
    setAutoCategorizing(true);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch("/batch-imports/review/auto-categorize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: run.id }),
      });
      const payload = (await response.json()) as
        | { updated_count: number; needs_review_count: number }
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to auto-categorize known merchants."
        );
      }
      const result = payload as {
        updated_count: number;
        needs_review_count: number;
      };
      setMessage(
        result.updated_count > 0
          ? `Auto-categorized ${result.updated_count} row(s). ${result.needs_review_count} still need review.`
          : "No rows matched built-in category rules."
      );
      await loadBatchState(needsReviewOnly);
    } catch (autoError) {
      setError(
        autoError instanceof Error
          ? autoError.message
          : "Failed to auto-categorize known merchants."
      );
    } finally {
      setAutoCategorizing(false);
    }
  }

  async function onDismissDigitNoise() {
    if (!run) {
      return;
    }
    setDismissingDigitNoise(true);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(
        "/batch-imports/review/dismiss-digit-noise",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_id: run.id }),
        }
      );
      const payload = (await response.json()) as
        | { updated_count: number }
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to exclude junk numeric merchants."
        );
      }
      const updatedCount = (payload as { updated_count: number }).updated_count;
      setMessage(
        updatedCount > 0
          ? `Excluded ${updatedCount} junk row(s) with bogus merchant names (not real shops).`
          : "No junk parse-noise rows were pending review."
      );
      await loadBatchState(needsReviewOnly);
    } catch (dismissError) {
      setError(
        dismissError instanceof Error
          ? dismissError.message
          : "Failed to exclude junk numeric merchants."
      );
    } finally {
      setDismissingDigitNoise(false);
    }
  }

  async function onBulkSaveMerchant(item: NeedsReviewMerchantSummary) {
    if (!run) {
      return;
    }
    const category = merchantDraftCategory(item);
    setBulkSavingMerchant(item.merchant_raw);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch("/batch-imports/review/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: run.id,
          merchant_raw: item.merchant_raw,
          category,
          exclude_from_spending: false,
          save_rule: true,
        }),
      });
      const payload = (await response.json()) as
        | { updated_count: number }
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to apply merchant review rule."
        );
      }
      const updatedCount = (payload as { updated_count: number }).updated_count;
      setMessage(
        `Applied ${category} to ${updatedCount} row(s) for ${item.merchant_raw}.`
      );
      await loadBatchState(needsReviewOnly);
    } catch (bulkSaveError) {
      setError(
        bulkSaveError instanceof Error
          ? bulkSaveError.message
          : "Failed to apply merchant review rule."
      );
    } finally {
      setBulkSavingMerchant(null);
    }
  }

  async function onSyncToDashboard() {
    if (!run) {
      setError("Run a batch import before syncing to the dashboard.");
      return;
    }

    setRunning(true);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch("/batch-imports/merge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: run.id }),
      });
      const payload = (await response.json()) as
        | { merged_count: number; skipped_count: number }
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to sync batch import to the dashboard."
        );
      }

      const mergePayload = payload as { merged_count: number; skipped_count: number };
      setMessage(
        `Synced ${mergePayload.merged_count} transaction(s) to the dashboard.`
      );
    } catch (syncError) {
      setError(
        syncError instanceof Error
          ? syncError.message
          : "Failed to sync batch import to the dashboard."
      );
    } finally {
      setRunning(false);
    }
  }

  async function onRunImport() {
    setRunning(true);
    setError("");
    setMessage("");
    setSkippedFiles([]);
    try {
      const payload = await apiFetchJson<{ run: BatchImportRunPayload }>(
        "/batch-imports/run",
        "Batch import failed.",
        { method: "POST" }
      );

      const runPayload = normalizeBatchRun(payload.run);
      const skipped = payload.run.skipped_files ?? [];
      setRun(runPayload);
      setSkippedFiles(skipped);

      const passwordSkipped = skipped.filter(
        (item) => item.reason === "password_required"
      ).length;
      let summary = `Imported ${runPayload.transaction_count} transactions from ${runPayload.statement_count} statements.`;
      if (skipped.length > 0) {
        summary += ` Skipped ${skipped.length} file(s) — see details below.`;
      }
      if (passwordSkipped > 0) {
        summary += ` ${passwordSkipped} need a PDF password — decrypt with scripts/decrypt-pdfs.sh first.`;
      }
      setMessage(summary);
      await loadBatchState(needsReviewOnly);
    } catch (runError) {
      if (runError instanceof ApiError && runError.status === 401) {
        await refreshSession();
        return;
      }
      setSkippedFiles([]);
      const detail =
        runError instanceof Error ? runError.message : "Batch import failed.";
      setError(
        `${detail} Check backend logs on the cluster for the full traceback.`
      );
    } finally {
      setRunning(false);
    }
  }

  async function onDelete(transaction: BatchTransaction) {
    const confirmed = window.confirm(
      `Delete this row from the import queue?\n\n${transaction.transaction_date} · ${transaction.merchant_raw} · ${formatCurrency(transaction.amount, transaction.currency)}`
    );
    if (!confirmed) {
      return;
    }

    setDeletingId(transaction.id);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(
        `/batch-imports/transactions/${transaction.id}`,
        { method: "DELETE" }
      );
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
      setTransactions((currentTransactions) =>
        currentTransactions.filter((item) => item.id !== transaction.id)
      );
      setDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMessage(`Deleted ${transaction.merchant_raw}.`);
      await loadBatchState(needsReviewOnly);
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Failed to delete transaction."
      );
    } finally {
      setDeletingId(null);
    }
  }

  async function onSave(transaction: BatchTransaction) {
    const draft = getDraft(transaction);
    const merchantRaw = draft.merchant_raw.trim();
    if (!merchantRaw) {
      setError("Merchant is required.");
      return;
    }
    setSavingId(transaction.id);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(
        `/batch-imports/transactions/${transaction.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            merchant_raw: merchantRaw,
            category: draft.category,
            exclude_from_spending: draft.exclude_from_spending,
            save_rule: true,
          }),
        }
      );
      const payload = (await response.json()) as
        | { transaction: BatchTransaction }
        | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Failed to save review decision."
        );
      }

      const updated = (payload as { transaction: BatchTransaction }).transaction;
      setTransactions((currentTransactions) => {
        if (needsReviewOnly && !updated.needs_review) {
          return currentTransactions.filter((item) => item.id !== updated.id);
        }
        return currentTransactions.map((item) =>
          item.id === updated.id ? updated : item
        );
      });
      setDrafts((currentDrafts) => {
        const nextDrafts = { ...currentDrafts };
        delete nextDrafts[transaction.id];
        return nextDrafts;
      });
      setMessage(`Saved review rule for ${updated.merchant_raw}.`);
      await loadBatchState(needsReviewOnly);
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Failed to save review decision."
      );
    } finally {
      setSavingId(null);
    }
  }

  if (status === "loading") {
    return (
      <main className={styles.page}>
        <div className={styles.shell}>
          <section className={styles.content}>
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
          body="Use a username and password to review only your own staged batch-import data."
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
            <Body1 className={styles.quietText}>{translate(locale, "navHint")}</Body1>
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
        </aside>

        <section className={styles.content}>
          <Title1>Batch Statement Review</Title1>
          <Body1 className={styles.quietText}>
            Run the offline batch importer, export the normalized dataset, and
            resolve unknown merchants into reusable deterministic rules.
          </Body1>

          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.actions}>
              <Button appearance="primary" disabled={running} onClick={onRunImport}>
                {running ? "Running import..." : "Run batch import"}
              </Button>
              <Button appearance="secondary" disabled={running || !run} onClick={onSyncToDashboard}>
                Sync to dashboard
              </Button>
              <Badge appearance="outline" color="brand">
                {run ? `${run.transaction_count} normalized rows` : "No run yet"}
              </Badge>
              {loading ? <Spinner size="tiny" label="Refreshing queue" /> : null}
            </div>

            {run ? (
              <>
                <div className={styles.metaGrid}>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>Imported</div>
                    <Body1>{formatDateTime(run.imported_at)}</Body1>
                  </div>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>Statements</div>
                    <Body1>{run.statement_count}</Body1>
                  </div>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>Needs review</div>
                    <Body1>{run.needs_review_count}</Body1>
                  </div>
                </div>
                <div className={styles.metaPaths}>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>CSV output</div>
                    <Body1
                      className={styles.metaPathValue}
                      title={run.csv_output_path}
                    >
                      {run.csv_output_path}
                    </Body1>
                  </div>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>SQLite output</div>
                    <Body1
                      className={styles.metaPathValue}
                      title={run.sqlite_output_path}
                    >
                      {run.sqlite_output_path}
                    </Body1>
                  </div>
                  <div className={styles.metaItem}>
                    <div className={styles.metaLabel}>Source directories</div>
                    <Body1
                      className={styles.metaPathValue}
                      title={run.pdf_source_dir}
                    >
                      {run.pdf_source_dir}
                    </Body1>
                    <Body1
                      className={styles.metaPathValue}
                      title={run.text_source_dir}
                    >
                      {run.text_source_dir}
                    </Body1>
                  </div>
                </div>
              </>
            ) : (
              <Body1 className={styles.quietText}>
                No batch import has been run yet on the default SQLite store.
              </Body1>
            )}
          </Card>

          {error ? (
            <MessageBar intent="error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
          ) : null}
          {message ? (
            <MessageBar intent="success">
              <MessageBarBody>{message}</MessageBarBody>
            </MessageBar>
          ) : null}

          {skippedFiles.length > 0 ? (
            <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
              <Title2>Skipped statements ({skippedFiles.length})</Title2>
              <Body1 className={styles.quietText}>
                These files were not imported. Fix the issue and run batch import
                again.
              </Body1>
              {groupSkippedFiles(skippedFiles).map(([reason, files]) => (
                <div key={reason} className={styles.metaItem}>
                  <div className={styles.metaLabel}>
                    {SKIP_REASON_LABELS[reason] ?? reason} ({files.length})
                  </div>
                  <Body1>
                    {files
                      .slice(0, 8)
                      .map((file) => file.statement_file)
                      .join(", ")}
                    {files.length > 8
                      ? `, and ${files.length - 8} more`
                      : ""}
                  </Body1>
                  {files[0]?.detail ? (
                    <Body1 className={styles.quietText}>
                      Example: {files[0].detail}
                    </Body1>
                  ) : null}
                </div>
              ))}
            </Card>
          ) : null}

          {digitNoisePendingCount > 0 ? (
            <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
              <div className={styles.cleanupRow}>
                <div>
                  <Title2>Junk rows from statement parsing</Title2>
                  <Body1 className={styles.quietText}>
                    {digitNoisePendingCount} row(s) have junk merchant names (e.g.
                    500, -62, CTBC)—usually CTBC PDF table or header fragments, not
                    real shops. Exclude them from spending and clear review.
                  </Body1>
                </div>
                <Button
                  appearance="secondary"
                  disabled={dismissingDigitNoise || !run}
                  onClick={() => void onDismissDigitNoise()}
                >
                  {dismissingDigitNoise
                    ? "Excluding..."
                    : `Exclude ${digitNoisePendingCount} junk row(s)`}
                </Button>
              </div>
            </Card>
          ) : null}

          {needsReviewMerchants.length > 0 ? (
            <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
              <div className={styles.cleanupRow}>
                <div>
                  <Title2>Top merchants needing review</Title2>
                  <Body1 className={styles.quietText}>
                    Apply one category + rule to clear repeated unknown merchants.
                    Known merchants are pre-filled from built-in rules.
                  </Body1>
                </div>
                {autoApplicableMerchantCount > 0 ? (
                  <Button
                    appearance="primary"
                    disabled={autoCategorizing || !run}
                    onClick={() => void onAutoCategorizeKnown()}
                  >
                    {autoCategorizing
                      ? "Applying..."
                      : `Auto-categorize known (${autoApplicableMerchantCount})`}
                  </Button>
                ) : null}
              </div>
              {needsReviewMerchants.map((item) => (
                <div className={styles.merchantRuleRow} key={item.merchant_raw}>
                  <div>
                    <div>{item.merchant_raw}</div>
                    <Body1 className={styles.quietText}>
                      {item.transaction_count} row(s)
                      {item.can_auto_apply
                        ? ` · suggested: ${item.suggested_category}`
                        : ""}
                    </Body1>
                  </div>
                  <select
                    className={styles.selectControl}
                    value={merchantDraftCategory(item)}
                    onChange={(event) =>
                      setMerchantCategoryDrafts((currentDrafts) => ({
                        ...currentDrafts,
                        [item.merchant_raw]: event.target.value,
                      }))
                    }
                  >
                    {reviewCategories.map((categoryOption) => (
                      <option key={categoryOption} value={categoryOption}>
                        {categoryOption}
                      </option>
                    ))}
                  </select>
                  <Button
                    appearance="secondary"
                    disabled={bulkSavingMerchant === item.merchant_raw}
                    onClick={() => void onBulkSaveMerchant(item)}
                  >
                    {bulkSavingMerchant === item.merchant_raw
                      ? "Applying..."
                      : "Apply to all"}
                  </Button>
                </div>
              ))}
            </Card>
          ) : null}

          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.actions}>
              <Title2>Review queue</Title2>
              <Badge appearance="filled" color="danger">
                {activeReviewCount} open
              </Badge>
              <Switch
                checked={needsReviewOnly}
                label="Show only flagged rows"
                onChange={(_, data) => setNeedsReviewOnly(Boolean(data.checked))}
              />
            </div>

            {!transactions.length && !loading ? (
              <Body1 className={styles.quietText}>
                {run
                  ? "No transactions match the current review filter."
                  : "Run the batch import to populate the review queue."}
              </Body1>
            ) : null}

            <div className={styles.tableWrap}>
              <Table aria-label="Batch import review queue">
                <TableHeader>
                  <TableRow>
                    <TableHeaderCell className={styles.tableHeaderCell}>Date</TableHeaderCell>
                    <TableHeaderCell className={styles.tableHeaderCell}>Merchant</TableHeaderCell>
                    <TableHeaderCell className={styles.tableHeaderCell}>Bank</TableHeaderCell>
                    <TableHeaderCell className={styles.tableHeaderCell}>Amount</TableHeaderCell>
                    <TableHeaderCell className={styles.tableHeaderCell}>Current</TableHeaderCell>
                    <TableHeaderCell className={styles.tableHeaderCell}>Review</TableHeaderCell>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {transactions.map((transaction) => {
                    const draft = getDraft(transaction);
                    return (
                      <TableRow key={transaction.id}>
                        <TableCell>{transaction.transaction_date}</TableCell>
                        <TableCell className={styles.merchantCell}>
                          <Input
                            className={styles.merchantInput}
                            value={draft.merchant_raw}
                            onChange={(_event, data) =>
                              updateDraft(transaction.id, {
                                ...draft,
                                merchant_raw: data.value,
                              })
                            }
                            aria-label={`Merchant for transaction ${transaction.id}`}
                          />
                          <Body1 className={styles.quietText}>
                            {transaction.statement_file}
                            {transaction.card_last4 ? ` • ${transaction.card_last4}` : ""}
                          </Body1>
                        </TableCell>
                        <TableCell>{transaction.bank}</TableCell>
                        <TableCell>
                          {formatCurrency(transaction.amount, transaction.currency)}
                        </TableCell>
                        <TableCell>
                          <div>{transaction.category}</div>
                          <Body1 className={styles.quietText}>
                            {transaction.transaction_type}
                            {transaction.exclude_from_spending ? " • excluded" : ""}
                          </Body1>
                        </TableCell>
                        <TableCell>
                          <div className={styles.actions}>
                            <select
                              className={styles.selectControl}
                              value={draft.category}
                              onChange={(event) =>
                                updateDraft(transaction.id, {
                                  ...draft,
                                  category: event.target.value,
                                })
                              }
                            >
                              {reviewCategories.map((categoryOption) => (
                                <option key={categoryOption} value={categoryOption}>
                                  {categoryOption}
                                </option>
                              ))}
                            </select>
                            <label className={styles.excludeToggle}>
                              <input
                                type="checkbox"
                                checked={draft.exclude_from_spending}
                                onChange={(event) =>
                                  updateDraft(transaction.id, {
                                    ...draft,
                                    exclude_from_spending: event.target.checked,
                                  })
                                }
                              />
                              Exclude
                            </label>
                            <Button
                              appearance="secondary"
                              disabled={
                                savingId === transaction.id ||
                                deletingId === transaction.id
                              }
                              onClick={() => void onSave(transaction)}
                            >
                              {savingId === transaction.id ? "Saving..." : "Save"}
                            </Button>
                            <Button
                              appearance="outline"
                              disabled={
                                savingId === transaction.id ||
                                deletingId === transaction.id
                              }
                              onClick={() => void onDelete(transaction)}
                            >
                              {deletingId === transaction.id ? "Deleting..." : "Delete"}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </Card>
        </section>
      </div>
    </main>
  );
}

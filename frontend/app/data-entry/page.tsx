"use client";

import { ChangeEvent, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
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
  Title1,
  Title2,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { apiFetch } from "../api";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { supportedCurrencies, type SupportedCurrency } from "../currency";
import { useAuth } from "../auth-provider";
import { useLocale } from "../locale-provider";
import { buildBulkImportLlmPrompt } from "../bulk-import-llm-prompt";
import { translate, type MessageKey } from "../messages";

type UploadResponse = {
  file_path: string;
  original_file_name: string;
};

const manualPaymentMethods = [
  "Cash",
  "PayPal",
  "EasyCard (悠遊卡)",
  "iCash",
  "Bank transfer",
  "Other",
] as const;

const BULK_IMPORT_EXAMPLE = `{
  "source": "CTBC 2026-05",
  "bank": "CTBC",
  "currency": "TWD",
  "transactions": [
    {"date": "2026-05-13", "merchant": "IKEA", "amount": 813.0, "category": "Shopping / Retail"},
    {"date": "2026-05-14", "merchant": "Uber", "amount": 186.0, "category": "Transportation"}
  ]
}`;

const manualCategories = [
  "Auto",
  "Dining / Food",
  "Groceries",
  "Transportation",
  "Shopping / Retail",
  "Shopping / Online Retail",
  "Subscription / Membership",
  "Utilities / Telecom",
  "Healthcare / Medical",
  "Travel",
  "Education / Certification",
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
    gap: "16px",
    minWidth: 0,
  },
  cards: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  cardsTop: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "16px",
    "@media (max-width: 959px)": {
      gridTemplateColumns: "1fr",
    },
  },
  card: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
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
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "12px",
    "@media (max-width: 900px)": {
      gridTemplateColumns: "1fr",
    },
  },
  selectControl: {
    width: "100%",
    height: "40px",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    paddingLeft: "12px",
    paddingRight: "12px",
    fontSize: tokens.fontSizeBase300,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  hiddenInput: {
    display: "none",
  },
  slicerGroup: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  slicerButtons: {
    display: "flex",
    flexWrap: "wrap",
    gap: "8px",
  },
  pillGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "8px",
  },
  emptySlicerState: {
    color: tokens.colorNeutralForeground3,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    flexWrap: "wrap",
  },
  exampleBlock: {
    margin: 0,
    maxHeight: "180px",
    padding: "12px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: tokens.fontSizeBase200,
    lineHeight: "1.45",
    whiteSpace: "pre-wrap",
    overflowY: "auto",
    resize: "vertical",
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
  },
  stepTitle: {
    fontWeight: tokens.fontWeightSemibold,
    fontSize: tokens.fontSizeBase400,
  },
});

function getTodayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function parseManualExchangeRate(
  currency: SupportedCurrency,
  exchangeRateInput: string
): number | null {
  if (currency === "TWD" || !exchangeRateInput.trim()) {
    return null;
  }
  return Number(exchangeRateInput);
}

function validateManualSpendingForm(options: {
  date: string;
  merchant: string;
  amount: number;
  exchangeRate: number | null;
  translate: (key: MessageKey) => string;
}): string | null {
  if (!options.date) {
    return options.translate("errManualDateRequired");
  }
  if (!options.merchant) {
    return options.translate("errManualMerchantRequired");
  }
  if (!Number.isFinite(options.amount) || options.amount <= 0) {
    return options.translate("errManualAmountInvalid");
  }
  if (
    options.exchangeRate !== null &&
    (!Number.isFinite(options.exchangeRate) || options.exchangeRate <= 0)
  ) {
    return options.translate("errManualExchangeRateInvalid");
  }
  return null;
}

function buildManualTransactionPayload(options: {
  date: string;
  merchant: string;
  amount: number;
  currency: SupportedCurrency;
  exchangeRate: number | null;
  category: string;
  paymentMethod: string;
}) {
  return {
    date: options.date,
    merchant: options.merchant,
    amount: options.amount,
    currency: options.currency,
    original_amount: options.amount,
    original_currency: options.currency,
    base_currency: "TWD" as const,
    exchange_rate: options.exchangeRate,
    exchange_rate_date: options.exchangeRate === null ? null : options.date,
    category: options.category === "Auto" ? null : options.category,
    payment_method: options.paymentMethod || null,
  };
}

function navLinkClass(
  styles: ReturnType<typeof useStyles>,
  pathname: string,
  href: string
) {
  return mergeClasses(
    styles.sidebarNavLink,
    pathname === href && styles.sidebarNavLinkActive
  );
}

function StatementUploadCard({
  styles,
  locale,
  refreshSession,
}: Readonly<{
  styles: ReturnType<typeof useStyles>;
  locale: ReturnType<typeof useLocale>["locale"];
  refreshSession: () => Promise<void>;
}>) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pdfPassword, setPdfPassword] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [uploadError, setUploadError] = useState("");

  async function onUploadStatement() {
    if (!selectedFile) {
      setUploadError(translate(locale, "errSelectPdfFirst"));
      return;
    }
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("password", pdfPassword);
    setUploading(true);
    setUploadError("");
    setUploadMessage("");
    try {
      const response = await apiFetch("/upload", { method: "POST", body: formData });
      const payload = (await response.json()) as UploadResponse | { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(
          "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : translate(locale, "errUploadFailed")
        );
      }
      const uploadData = payload as UploadResponse;
      setUploadMessage(
        translate(locale, "uploadDoneMessageTemplate", {
          name: uploadData.original_file_name,
        })
      );
      setSelectedFile(null);
      setPdfPassword("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      setUploadError(
        error instanceof Error ? error.message : translate(locale, "errUploadFailed")
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card className={styles.card}>
      <Title2>{translate(locale, "statementImportTitle")}</Title2>
      <Field label={translate(locale, "fieldStatementPdfLabel")}>
        <>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            className={styles.hiddenInput}
            aria-label={translate(locale, "fieldStatementPdfLabel")}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              setSelectedFile(event.target.files?.[0] ?? null);
              setUploadError("");
              setUploadMessage("");
            }}
          />
          <Button onClick={() => fileInputRef.current?.click()} appearance="secondary">
            {selectedFile ? selectedFile.name : translate(locale, "choosePdf")}
          </Button>
        </>
      </Field>
      <Field label={translate(locale, "fieldPdfPasswordLabel")}>
        <Input
          value={pdfPassword}
          onChange={(_, data) => setPdfPassword(data.value)}
          type="password"
        />
      </Field>
      {uploading ? <ProgressBar /> : null}
      <Button appearance="primary" onClick={() => void onUploadStatement()} disabled={uploading}>
        {uploading ? translate(locale, "uploading") : translate(locale, "uploadStatement")}
      </Button>
      {uploadError ? (
        <MessageBar intent="error">
          <MessageBarBody>{uploadError}</MessageBarBody>
        </MessageBar>
      ) : null}
      {uploadMessage ? (
        <MessageBar intent="success">
          <MessageBarBody>
            <MessageBarTitle>{translate(locale, "uploadDoneTitle")}</MessageBarTitle>
            {uploadMessage}
          </MessageBarBody>
        </MessageBar>
      ) : null}
    </Card>
  );
}

function BulkImportCard({
  styles,
  locale,
  refreshSession,
}: Readonly<{
  styles: ReturnType<typeof useStyles>;
  locale: ReturnType<typeof useLocale>["locale"];
  refreshSession: () => Promise<void>;
}>) {
  const bulkFileInputRef = useRef<HTMLInputElement>(null);
  const [bulkContent, setBulkContent] = useState("");
  const [bulkSourceName, setBulkSourceName] = useState("ctbc-import");
  const [bulkBank, setBulkBank] = useState("CTBC");
  const [bulkCurrency, setBulkCurrency] = useState("TWD");
  const [bulkFormat, setBulkFormat] = useState<"auto" | "json" | "text">("auto");
  const [bulkDestination, setBulkDestination] = useState<"batch" | "dashboard">("batch");
  const [bulkImporting, setBulkImporting] = useState(false);
  const [bulkError, setBulkError] = useState("");
  const [bulkMessage, setBulkMessage] = useState("");
  const [promptCopied, setPromptCopied] = useState(false);

  const llmConversionPrompt = useMemo(
    () =>
      buildBulkImportLlmPrompt({
        bank: bulkBank,
        currency: bulkCurrency,
        source: bulkSourceName,
        locale,
      }),
    [bulkBank, bulkCurrency, bulkSourceName, locale]
  );

  async function onPickBulkFile(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    if (!nextFile) {
      return;
    }
    setBulkError("");
    setBulkMessage("");
    try {
      const text = await nextFile.text();
      setBulkContent(text);
      const baseName = nextFile.name.replace(/\.[^.]+$/, "");
      if (baseName) {
        setBulkSourceName(baseName);
      }
      if (nextFile.name.endsWith(".json")) {
        setBulkFormat("json");
      } else if (nextFile.name.endsWith(".txt")) {
        setBulkFormat("text");
      }
    } catch {
      setBulkError(translate(locale, "errBulkImportFailed"));
    }
  }

  async function onBulkImport() {
    const content = bulkContent.trim();
    if (!content) {
      setBulkError(translate(locale, "errBulkImportEmpty"));
      return;
    }
    setBulkImporting(true);
    setBulkError("");
    setBulkMessage("");
    try {
      const response = await apiFetch("/batch-imports/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          format: bulkFormat,
          bank: bulkBank.trim() || "Manual",
          currency: bulkCurrency.trim() || "TWD",
          source_name: bulkSourceName.trim() || "import",
          destination: bulkDestination,
        }),
      });
      const payload = (await response.json()) as {
        detail?: string;
        imported_count?: number;
        destination?: string;
      };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(payload.detail ?? translate(locale, "errBulkImportFailed"));
      }
      const count = payload.imported_count ?? 0;
      setBulkMessage(
        payload.destination === "dashboard"
          ? translate(locale, "bulkImportSuccessDashboard", { count: String(count) })
          : translate(locale, "bulkImportSuccessBatch", { count: String(count) })
      );
    } catch (error) {
      setBulkError(
        error instanceof Error ? error.message : translate(locale, "errBulkImportFailed")
      );
    } finally {
      setBulkImporting(false);
    }
  }

  return (
    <Card className={styles.card}>
      <Title2>{translate(locale, "bulkImportTitle")}</Title2>
      <Body1>{translate(locale, "bulkImportBody")}</Body1>
      <div className={styles.formGrid}>
        <Field label={translate(locale, "bulkImportSourceLabel")}>
          <Input value={bulkSourceName} onChange={(_, data) => setBulkSourceName(data.value)} />
        </Field>
        <Field label={translate(locale, "bulkImportBankLabel")}>
          <Input value={bulkBank} onChange={(_, data) => setBulkBank(data.value)} />
        </Field>
        <Field label={translate(locale, "bulkImportCurrencyLabel")}>
          <Input value={bulkCurrency} onChange={(_, data) => setBulkCurrency(data.value)} />
        </Field>
      </div>
      <div className={styles.stepTitle}>{translate(locale, "bulkImportLlmStepTitle")}</div>
      <Body1>
        {translate(
          locale,
          bulkBank.trim().toUpperCase() === "CTBC"
            ? "bulkImportLlmStepBodyCtbc"
            : "bulkImportLlmStepBody"
        )}
      </Body1>
      <pre className={styles.exampleBlock}>{llmConversionPrompt}</pre>
      <Button
        appearance="secondary"
        onClick={() => {
          void navigator.clipboard.writeText(llmConversionPrompt).then(
            () => {
              setPromptCopied(true);
              window.setTimeout(() => setPromptCopied(false), 2000);
            },
            () => setBulkError(translate(locale, "errBulkImportFailed"))
          );
        }}
      >
        {promptCopied
          ? translate(locale, "bulkImportCopiedPrompt")
          : translate(locale, "bulkImportCopyPrompt")}
      </Button>
      <div className={styles.stepTitle}>{translate(locale, "bulkImportPasteStepTitle")}</div>
      <Field label={translate(locale, "bulkImportPasteLabel")}>
        <textarea
          className={styles.exampleBlock}
          value={bulkContent}
          onChange={(event) => setBulkContent(event.target.value)}
          placeholder={translate(locale, "bulkImportPastePlaceholder")}
        />
      </Field>
      <Field label={translate(locale, "bulkImportFileLabel")}>
        <>
          <input
            ref={bulkFileInputRef}
            type="file"
            accept=".json,.txt,text/plain,application/json"
            className={styles.hiddenInput}
            aria-label={translate(locale, "bulkImportFileLabel")}
            onChange={(event) => void onPickBulkFile(event)}
          />
          <Button appearance="secondary" onClick={() => bulkFileInputRef.current?.click()}>
            {translate(locale, "bulkImportChooseFile")}
          </Button>
        </>
      </Field>
      <div className={styles.formGrid}>
        <Field label={translate(locale, "bulkImportFormatLabel")}>
          <select
            className={styles.selectControl}
            value={bulkFormat}
            onChange={(event) => setBulkFormat(event.target.value as "auto" | "json" | "text")}
          >
            <option value="auto">{translate(locale, "bulkImportFormatAuto")}</option>
            <option value="json">{translate(locale, "bulkImportFormatJson")}</option>
            <option value="text">{translate(locale, "bulkImportFormatText")}</option>
          </select>
        </Field>
        <Field label={translate(locale, "bulkImportDestinationLabel")}>
          <select
            className={styles.selectControl}
            value={bulkDestination}
            onChange={(event) =>
              setBulkDestination(event.target.value as "batch" | "dashboard")
            }
          >
            <option value="batch">{translate(locale, "bulkImportDestinationBatch")}</option>
            <option value="dashboard">{translate(locale, "bulkImportDestinationDashboard")}</option>
          </select>
        </Field>
      </div>
      <Body1 className={styles.filterHint}>{translate(locale, "bulkImportExampleTitle")}</Body1>
      <pre className={styles.exampleBlock}>{BULK_IMPORT_EXAMPLE}</pre>
      <Body1 className={styles.filterHint}>
        Text lines (tab-separated): 2026-05-13{"\t"}IKEA{"\t"}813.00
      </Body1>
      <Button appearance="primary" disabled={bulkImporting} onClick={() => void onBulkImport()}>
        {bulkImporting
          ? translate(locale, "bulkImportImporting")
          : translate(locale, "bulkImportButton")}
      </Button>
      {bulkDestination === "batch" && bulkMessage ? (
        <Link className={styles.sidebarNavLink} href="/batch-review">
          {translate(locale, "navBatchReview")}
        </Link>
      ) : null}
      {bulkError ? (
        <MessageBar intent="error">
          <MessageBarBody>{bulkError}</MessageBarBody>
        </MessageBar>
      ) : null}
      {bulkMessage ? (
        <MessageBar intent="success">
          <MessageBarBody>{bulkMessage}</MessageBarBody>
        </MessageBar>
      ) : null}
    </Card>
  );
}

function ManualSpendingCard({
  styles,
  locale,
  refreshSession,
}: Readonly<{
  styles: ReturnType<typeof useStyles>;
  locale: ReturnType<typeof useLocale>["locale"];
  refreshSession: () => Promise<void>;
}>) {
  const [manualDate, setManualDate] = useState(getTodayIsoDate);
  const [manualMerchant, setManualMerchant] = useState("");
  const [manualAmount, setManualAmount] = useState("");
  const [manualCurrency, setManualCurrency] = useState<SupportedCurrency>("TWD");
  const [manualExchangeRate, setManualExchangeRate] = useState("");
  const [manualCategory, setManualCategory] = useState("Auto");
  const [manualPaymentMethod, setManualPaymentMethod] = useState("Cash");
  const [manualSaving, setManualSaving] = useState(false);
  const [manualError, setManualError] = useState("");
  const [manualMessage, setManualMessage] = useState("");

  async function onAddManualSpending() {
    const merchant = manualMerchant.trim();
    const amount = Number(manualAmount);
    const exchangeRate = parseManualExchangeRate(manualCurrency, manualExchangeRate);
    const validationError = validateManualSpendingForm({
      date: manualDate,
      merchant,
      amount,
      exchangeRate,
      translate: (key) => translate(locale, key),
    });
    if (validationError) {
      setManualError(validationError);
      return;
    }

    setManualSaving(true);
    setManualError("");
    setManualMessage("");
    try {
      const response = await apiFetch("/transactions/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildManualTransactionPayload({
            date: manualDate,
            merchant,
            amount,
            currency: manualCurrency,
            exchangeRate,
            category: manualCategory,
            paymentMethod: manualPaymentMethod,
          })
        ),
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) {
        if (response.status === 401) {
          await refreshSession();
          return;
        }
        throw new Error(payload.detail ?? translate(locale, "errAddManualFailed"));
      }
      setManualMessage(translate(locale, "dataEntryManualSuccess"));
      setManualMerchant("");
      setManualAmount("");
      setManualCurrency("TWD");
      setManualExchangeRate("");
      setManualCategory("Auto");
      setManualPaymentMethod("Cash");
      setManualDate(getTodayIsoDate());
    } catch (error) {
      setManualError(
        error instanceof Error ? error.message : translate(locale, "errAddManualFailed")
      );
    } finally {
      setManualSaving(false);
    }
  }

  return (
    <Card className={mergeClasses(styles.card)}>
      <Title2>{translate(locale, "dataEntryManualTitle")}</Title2>
      <div className={styles.formGrid} data-testid="manual-entry-primary-grid">
        <Field label={translate(locale, "manualDateLabel")}>
          <Input type="date" value={manualDate} onChange={(_, data) => setManualDate(data.value)} />
        </Field>
        <Field label={translate(locale, "manualAmountLabel")}>
          <Input
            type="number"
            step="0.01"
            placeholder="0.00"
            value={manualAmount}
            onChange={(_, data) => setManualAmount(data.value)}
          />
        </Field>
        <Field label={translate(locale, "manualCurrencyLabel")}>
          <select
            aria-label={translate(locale, "manualCurrencyLabel")}
            className={styles.selectControl}
            value={manualCurrency}
            onChange={(event) => setManualCurrency(event.target.value as SupportedCurrency)}
          >
            {supportedCurrencies.map((currencyOption) => (
              <option key={currencyOption} value={currencyOption}>
                {currencyOption}
              </option>
            ))}
          </select>
        </Field>
        {manualCurrency !== "TWD" ? (
          <Field label={translate(locale, "manualExchangeRateLabel")}>
            <Input
              type="number"
              step="0.0001"
              placeholder="32.40"
              value={manualExchangeRate}
              onChange={(_, data) => setManualExchangeRate(data.value)}
            />
          </Field>
        ) : null}
      </div>
      <Field label={translate(locale, "manualMerchantLabel")}>
        <Input
          value={manualMerchant}
          onChange={(_, data) => setManualMerchant(data.value)}
          placeholder={translate(locale, "manualMerchantPlaceholder")}
        />
      </Field>
      <div className={styles.formGrid}>
        <Field label={translate(locale, "manualCategoryLabel")}>
          <select
            className={styles.selectControl}
            value={manualCategory}
            onChange={(event) => setManualCategory(event.target.value)}
          >
            {manualCategories.map((categoryOption) => (
              <option key={categoryOption} value={categoryOption}>
                {categoryOption === "Auto"
                  ? translate(locale, "manualCategoryAuto")
                  : categoryOption}
              </option>
            ))}
          </select>
        </Field>
        <Field label={translate(locale, "manualPaymentMethodLabel")}>
          <select
            className={styles.selectControl}
            value={manualPaymentMethod}
            onChange={(event) => setManualPaymentMethod(event.target.value)}
          >
            {manualPaymentMethods.map((methodOption) => (
              <option key={methodOption} value={methodOption}>
                {methodOption}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Button appearance="primary" onClick={() => void onAddManualSpending()} disabled={manualSaving}>
        {manualSaving ? translate(locale, "saving") : translate(locale, "addSpending")}
      </Button>
      {manualError ? (
        <MessageBar intent="error">
          <MessageBarBody>{manualError}</MessageBarBody>
        </MessageBar>
      ) : null}
      {manualMessage ? (
        <MessageBar intent="success">
          <MessageBarBody>{manualMessage}</MessageBarBody>
        </MessageBar>
      ) : null}
    </Card>
  );
}

export default function DataEntryPage() {
  const styles = useStyles();
  const { locale } = useLocale();
  const { refreshSession, status, user } = useAuth();
  const pathname = usePathname();

  if (status === "loading") {
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
          body="Use a username and password to upload statements and add manual transactions."
        />
      </AuthPage>
    );
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell} data-testid="data-entry-shell">
        <aside className={styles.sidebar}>
          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.sectionEyebrow}>{translate(locale, "navTitle")}</div>
            <Title2>{translate(locale, "navTitle")}</Title2>
            <Body1 className={styles.filterHint}>{translate(locale, "navHint")}</Body1>
            <div className={styles.sidebarNavLinks}>
              <Link className={navLinkClass(styles, pathname, "/")} href="/">
                {translate(locale, "navDashboard")}
              </Link>
              <Link
                className={navLinkClass(styles, pathname, "/data-entry")}
                href="/data-entry"
              >
                {translate(locale, "navDataEntry")}
              </Link>
              <Link
                className={navLinkClass(styles, pathname, "/transactions")}
                href="/transactions"
              >
                {translate(locale, "navTransactions")}
              </Link>
              <Link
                className={navLinkClass(styles, pathname, "/batch-review")}
                href="/batch-review"
              >
                {translate(locale, "navBatchReview")}
              </Link>
              <Link
                className={navLinkClass(styles, pathname, "/ai-insights")}
                href="/ai-insights"
              >
                {translate(locale, "navAiInsights")}
              </Link>
              <Link
                className={navLinkClass(styles, pathname, "/settings")}
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
              <Input disabled placeholder={translate(locale, "searchPlaceholder")} value="" />
            </Field>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>{translate(locale, "timeRangeLabel")}</Body1>
              <div className={styles.pillGrid}>
                <Button disabled size="small">{translate(locale, "rangeWeek")}</Button>
                <Button disabled size="small">{translate(locale, "rangeMonth")}</Button>
                <Button disabled size="small">{translate(locale, "rangeQuarter")}</Button>
                <Button disabled size="small">{translate(locale, "rangeYear")}</Button>
                <Button disabled size="small">{translate(locale, "range5Years")}</Button>
                <Button disabled size="small">{translate(locale, "rangeAll")}</Button>
              </div>
            </div>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>{translate(locale, "amountDirection")}</Body1>
              <div className={styles.slicerButtons}>
                <Button disabled size="small">{translate(locale, "amountAll")}</Button>
                <Button disabled size="small">{translate(locale, "amountSpend")}</Button>
                <Button disabled size="small">{translate(locale, "amountRefunds")}</Button>
              </div>
            </div>
            <Field label={translate(locale, "sortLabel")}>
              <select className={styles.selectControl} disabled value="amount_desc">
                <option value="amount_desc">{translate(locale, "sortAmountDesc")}</option>
              </select>
            </Field>
            <div className={styles.slicerGroup}>
              <Body1 className={styles.emptySlicerState}>
                {translate(locale, "merchantSlicerLabel")}
              </Body1>
              <Body1 className={styles.emptySlicerState}>
                {translate(locale, "merchantSlicerEmpty")}
              </Body1>
            </div>
            <div className={styles.actions}>
              <Button disabled size="small">{translate(locale, "clearSlicers")}</Button>
              <Badge appearance="outline" color="brand">
                0 {translate(locale, "rowsInViewSuffix")}
              </Badge>
            </div>
            <Body1 className={styles.filterHint}>{translate(locale, "filtersPinnedHint")}</Body1>
          </Card>
        </aside>

        <section className={styles.reportArea}>
          <Title1>{translate(locale, "dataEntryTitle")}</Title1>
          <Body1>{translate(locale, "dataEntryBody")}</Body1>
          <div className={styles.cards}>
            <div className={styles.cardsTop}>
              <StatementUploadCard
                styles={styles}
                locale={locale}
                refreshSession={refreshSession}
              />
              <BulkImportCard
                styles={styles}
                locale={locale}
                refreshSession={refreshSession}
              />
            </div>
            <ManualSpendingCard
              styles={styles}
              locale={locale}
              refreshSession={refreshSession}
            />
          </div>
        </section>
      </div>
    </main>
  );
}

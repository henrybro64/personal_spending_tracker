"use client";

import { ChangeEvent, useRef, useState } from "react";
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
import { AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import { useLocale } from "../locale-provider";
import { translate } from "../messages";

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

const manualCategories = [
  "Auto",
  "Dining",
  "Groceries",
  "Transport",
  "Shopping",
  "Bills & Utilities",
  "Entertainment",
  "Health & Wellness",
  "Travel",
  "Income & Transfers",
  "Cash & Fees",
  "Other Spending",
  "Refunds & Credits",
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
  reportArea: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  cards: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
    gap: "12px",
  },
  card: {
    display: "flex",
    flexDirection: "column",
    gap: "10px",
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
  hiddenInput: {
    display: "none",
  },
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "10px",
    "@media (max-width: 520px)": {
      gridTemplateColumns: "1fr",
    },
  },
  selectControl: {
    width: "100%",
    height: "40px",
    paddingLeft: "12px",
    paddingRight: "12px",
    fontSize: tokens.fontSizeBase300,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
  },
});

function getTodayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

export default function DataEntryPage() {
  const styles = useStyles();
  const { locale } = useLocale();
  const { refreshSession, status, user } = useAuth();
  const pathname = usePathname();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pdfPassword, setPdfPassword] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [uploadError, setUploadError] = useState("");

  const [manualDate, setManualDate] = useState(getTodayIsoDate);
  const [manualMerchant, setManualMerchant] = useState("");
  const [manualAmount, setManualAmount] = useState("");
  const [manualCategory, setManualCategory] = useState("Auto");
  const [manualPaymentMethod, setManualPaymentMethod] = useState("Cash");
  const [manualSaving, setManualSaving] = useState(false);
  const [manualError, setManualError] = useState("");
  const [manualMessage, setManualMessage] = useState("");

  function onPickFile(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setSelectedFile(nextFile);
    setUploadError("");
    setUploadMessage("");
  }

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
      const response = await apiFetch("/upload", {
        method: "POST",
        body: formData,
      });
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

  async function onAddManualSpending() {
    const merchant = manualMerchant.trim();
    const amount = Number(manualAmount);
    if (!manualDate) {
      setManualError(translate(locale, "errManualDateRequired"));
      return;
    }
    if (!merchant) {
      setManualError(translate(locale, "errManualMerchantRequired"));
      return;
    }
    if (!Number.isFinite(amount) || amount === 0) {
      setManualError(translate(locale, "errManualAmountInvalid"));
      return;
    }

    setManualSaving(true);
    setManualError("");
    setManualMessage("");
    try {
      const response = await apiFetch("/transactions/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date: manualDate,
          merchant,
          amount,
          category: manualCategory === "Auto" ? null : manualCategory,
          payment_method: manualPaymentMethod || null,
        }),
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
      <main className={styles.page}>
        <div className={styles.shell}>
          <section className={styles.reportArea}>
            <AuthPanel
              title="Sign in required"
              body="Use an email and password to upload statements and add manual transactions."
            />
          </section>
        </div>
      </main>
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
            <Card className={styles.card}>
          <Title2>{translate(locale, "statementImportTitle")}</Title2>
          <Field label={translate(locale, "fieldStatementPdfLabel")}>
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                className={styles.hiddenInput}
                onChange={onPickFile}
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
          <Button appearance="primary" onClick={onUploadStatement} disabled={uploading}>
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

            <Card className={mergeClasses(styles.card)}>
              <Title2>{translate(locale, "dataEntryManualTitle")}</Title2>
          <div className={styles.formGrid}>
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
          <Button appearance="primary" onClick={onAddManualSpending} disabled={manualSaving}>
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
          </div>
        </section>
      </div>
    </main>
  );
}

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
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Spinner,
  Title1,
  Title2,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";

import { apiFetchJson } from "../api";
import {
  type AiInsightResponse,
  type AiProvider,
  type AiSettingsResponse,
} from "../ai-types";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import { describeCurrency, formatCurrencyAmount } from "../currency";
import { useLocale } from "../locale-provider";
import { useAppShellStyles } from "../app-shell-styles";
import { translate } from "../messages";
import { UnconvertedTransactionsNotice } from "../unconverted-notice";

type InsightRange = "month" | "quarter" | "6months" | "year";

const useStyles = makeStyles({
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
  sidebarNavLinkActive: {
    backgroundColor: tokens.colorBrandBackground2,
    color: tokens.colorBrandForeground2,
  },
  quietText: {
    color: tokens.colorNeutralForeground3,
  },
  controls: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: "12px",
    alignItems: "end",
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
  actions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
    alignItems: "center",
  },
  preformatted: {
    whiteSpace: "pre-wrap",
    lineHeight: 1.55,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    backgroundColor: "rgba(15, 23, 42, 0.04)",
    paddingTop: "14px",
    paddingBottom: "14px",
    paddingLeft: "14px",
    paddingRight: "14px",
    ...shorthands.borderRadius(tokens.borderRadiusLarge),
  },
  snapshotGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: "12px",
  },
  list: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  inlineList: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  sourceList: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
});

function navigationLinks(locale: ReturnType<typeof useLocale>["locale"]) {
  return [
    { href: "/", label: translate(locale, "navDashboard") },
    { href: "/data-entry", label: translate(locale, "navDataEntry") },
    { href: "/transactions", label: translate(locale, "navTransactions") },
    { href: "/batch-review", label: translate(locale, "navBatchReview") },
    { href: "/ai-insights", label: translate(locale, "navAiInsights") },
    { href: "/settings", label: translate(locale, "navSettings") },
  ];
}

function intlLocale(locale: ReturnType<typeof useLocale>["locale"]) {
  return locale === "zh-Hant" ? "zh-TW" : "en-US";
}

function rangeLabel(
  locale: ReturnType<typeof useLocale>["locale"],
  range: InsightRange
) {
  switch (range) {
    case "month":
      return translate(locale, "rangeMonth");
    case "6months":
      return translate(locale, "range6Months");
    case "year":
      return translate(locale, "rangeYear");
    case "quarter":
    default:
      return translate(locale, "rangeQuarter");
  }
}

function unconvertedInsightNotice(
  insight: AiInsightResponse,
  locale: ReturnType<typeof useLocale>["locale"]
) {
  const count = insight.snapshot.totals.unconverted_transaction_count ?? 0;
  if (count <= 0) {
    return null;
  }
  const currencies =
    insight.snapshot.totals.unconverted_currencies?.join(", ") ||
    translate(locale, "unconvertedTransactionsCurrencyFallback");
  return (
    <UnconvertedTransactionsNotice
      countLabel={String(count)}
      currencies={currencies}
    />
  );
}

export default function AiInsightsPage() {
  const styles = useStyles();
  const shellStyles = useAppShellStyles();
  const pathname = usePathname();
  const { locale } = useLocale();
  const { status } = useAuth();
  const [settings, setSettings] = useState<AiSettingsResponse | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<AiProvider | "">("");
  const [range, setRange] = useState<InsightRange>("quarter");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [insight, setInsight] = useState<AiInsightResponse | null>(null);

  useEffect(() => {
    if (status !== "authenticated") {
      setLoading(false);
      return;
    }
    void loadSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const configuredProviders = useMemo(
    () => settings?.providers.filter((provider) => provider.configured) ?? [],
    [settings]
  );

  async function loadSettings() {
    setLoading(true);
    setError("");
    try {
      const nextSettings = await apiFetchJson<AiSettingsResponse>(
        "/ai/settings",
        translate(locale, "errAiSettingsLoad")
      );
      setSettings(nextSettings);
      const activeProvider =
        nextSettings.selected_provider ??
        nextSettings.providers.find((provider) => provider.configured)?.provider ??
        "";
      setSelectedProvider(activeProvider);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : translate(locale, "errAiSettingsLoad"));
    } finally {
      setLoading(false);
    }
  }

  async function generateInsights() {
    if (!selectedProvider) {
      setError(translate(locale, "aiInsightsNeedConfig"));
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const nextInsight = await apiFetchJson<AiInsightResponse>(
        "/ai/insights",
        translate(locale, "errAiInsightsFailed"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: selectedProvider,
            range,
            language: locale,
          }),
        }
      );
      setInsight(nextInsight);
    } catch (generateError) {
      setError(
        generateError instanceof Error
          ? generateError.message
          : translate(locale, "errAiInsightsFailed")
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (status === "loading" || loading) {
    return (
      <AuthPage>
        <Card className={styles.card}>
          <Spinner label="Loading AI insights" />
        </Card>
      </AuthPage>
    );
  }

  if (status !== "authenticated") {
    return (
      <AuthPage>
        <AuthPanel
          title={translate(locale, "aiInsightsTitle")}
          body={translate(locale, "aiInsightsBody")}
        />
      </AuthPage>
    );
  }

  return (
    <main className={shellStyles.page}>
      <div className={shellStyles.shell} data-testid="ai-insights-shell">
        <aside className={shellStyles.sidebar}>
          <Card className={mergeClasses(styles.card, styles.sidebarPanel)}>
            <div className={styles.sectionEyebrow}>{translate(locale, "navTitle")}</div>
            <Title2>{translate(locale, "navTitle")}</Title2>
            <Body1 className={styles.quietText}>{translate(locale, "navHint")}</Body1>
            <div className={styles.sidebarNavLinks}>
              {navigationLinks(locale).map((item) => (
                <Link
                  key={item.href}
                  className={mergeClasses(
                    shellStyles.sidebarNavLink,
                    pathname === item.href && styles.sidebarNavLinkActive
                  )}
                  href={item.href}
                >
                  {item.label}
                </Link>
              ))}
            </div>
          </Card>
          <SessionCard />
        </aside>

        <section className={shellStyles.contentColumn}>
          <Card className={styles.card}>
            <div className={styles.sectionEyebrow}>{translate(locale, "navAiInsights")}</div>
            <Title1>{translate(locale, "aiInsightsTitle")}</Title1>
            <Body1 className={styles.quietText}>{translate(locale, "aiInsightsBody")}</Body1>
          </Card>

          {configuredProviders.length === 0 ? (
            <MessageBar intent="warning">
              <MessageBarBody>
                <MessageBarTitle>{translate(locale, "aiInsightsNeedConfig")}</MessageBarTitle>
                <Link href="/settings">{translate(locale, "navSettings")}</Link>
              </MessageBarBody>
            </MessageBar>
          ) : null}

          {error ? (
            <MessageBar intent="error">
              <MessageBarBody>
                <MessageBarTitle>{translate(locale, "errRequestFailed")}</MessageBarTitle>
                {error}
              </MessageBarBody>
            </MessageBar>
          ) : null}

          <Card className={styles.card}>
            <div className={styles.controls}>
              <Field label={translate(locale, "aiInsightsProviderLabel")}>
                <select
                  className={styles.selectControl}
                  value={selectedProvider}
                  onChange={(event) =>
                    setSelectedProvider(event.target.value as AiProvider | "")
                  }
                >
                  {configuredProviders.map((provider) => (
                    <option key={provider.provider} value={provider.provider}>
                      {provider.display_name}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label={translate(locale, "aiInsightsRangeLabel")}>
                <select
                  className={styles.selectControl}
                  value={range}
                  onChange={(event) => setRange(event.target.value as InsightRange)}
                >
                  <option value="month">{translate(locale, "rangeMonth")}</option>
                  <option value="quarter">{translate(locale, "rangeQuarter")}</option>
                  <option value="6months">{translate(locale, "range6Months")}</option>
                  <option value="year">{translate(locale, "rangeYear")}</option>
                </select>
              </Field>
            </div>

            <div className={styles.actions}>
              <Button
                appearance="primary"
                disabled={submitting || configuredProviders.length === 0}
                onClick={() => void generateInsights()}
              >
                {submitting
                  ? translate(locale, "aiInsightsGenerating")
                  : translate(locale, "aiInsightsGenerate")}
              </Button>
              {selectedProvider ? (
                <Badge appearance="outline">
                  {configuredProviders.find((provider) => provider.provider === selectedProvider)
                    ?.display_name ?? selectedProvider}
                </Badge>
              ) : null}
            </div>
          </Card>

          <Card className={styles.card}>
            <div className={styles.sectionEyebrow}>{translate(locale, "aiInsightsPrivacyTitle")}</div>
            <Title2>{translate(locale, "aiInsightsPrivacyTitle")}</Title2>
            <Body1 className={styles.quietText}>
              {translate(locale, "aiInsightsPrivacyBody")}
            </Body1>
          </Card>

          {insight ? (
            <>
              <Card className={styles.card}>
                <div className={styles.sectionEyebrow}>{translate(locale, "aiInsightsResultTitle")}</div>
                <Title2>{translate(locale, "aiInsightsResultTitle")}</Title2>
                <div className={styles.inlineList}>
                  <Badge appearance="filled">
                    {configuredProviders.find((provider) => provider.provider === insight.provider)
                      ?.display_name ?? insight.provider}
                  </Badge>
                  <Badge appearance="outline">{insight.model}</Badge>
                  <Badge appearance="outline">
                    {rangeLabel(locale, insight.time_range)}
                  </Badge>
                </div>
                <div className={styles.preformatted}>{insight.insight_text}</div>
              </Card>

              <Card className={styles.card}>
                <div className={styles.sectionEyebrow}>{translate(locale, "aiInsightsSnapshotTitle")}</div>
                <Title2>{translate(locale, "aiInsightsSnapshotTitle")}</Title2>
                {unconvertedInsightNotice(insight, locale)}
                <div className={styles.snapshotGrid}>
                  <Card className={styles.card}>
                    <Body1>{translate(locale, "snapshotTransactions")}</Body1>
                    <Title2>{String(insight.snapshot.transaction_count)}</Title2>
                  </Card>
                  <Card className={styles.card}>
                    <Body1>{translate(locale, "snapshotDisplayCurrency")}</Body1>
                    <Title2>{describeCurrency(insight.snapshot.display_currency)}</Title2>
                  </Card>
                  <Card className={styles.card}>
                    <Body1>{translate(locale, "snapshotDateWindow")}</Body1>
                    <Title2>
                      {insight.snapshot.date_start && insight.snapshot.date_end
                        ? `${insight.snapshot.date_start} - ${insight.snapshot.date_end}`
                        : translate(locale, "waiting")}
                    </Title2>
                  </Card>
                  <Card className={styles.card}>
                    <Body1>{translate(locale, "transactionsNetTotal")}</Body1>
                    <Title2>
                      {formatCurrencyAmount(
                        insight.snapshot.totals.net,
                        insight.snapshot.display_currency,
                        intlLocale(locale)
                      )}
                    </Title2>
                  </Card>
                </div>

                <div className={styles.snapshotGrid}>
                  <Card className={styles.card}>
                    <Title2>{translate(locale, "snapshotTopCategories")}</Title2>
                    <div className={styles.list}>
                      {insight.snapshot.top_categories.map((item) => (
                        <Body1 key={item.label}>
                          {item.label}:{" "}
                          {formatCurrencyAmount(
                            item.amount,
                            insight.snapshot.display_currency,
                            intlLocale(locale)
                          )}
                        </Body1>
                      ))}
                    </div>
                  </Card>

                  <Card className={styles.card}>
                    <Title2>{translate(locale, "snapshotTopMerchants")}</Title2>
                    <div className={styles.list}>
                      {insight.snapshot.top_merchants.map((item) => (
                        <Body1 key={item.label}>
                          {item.label}:{" "}
                          {formatCurrencyAmount(
                            item.amount,
                            insight.snapshot.display_currency,
                            intlLocale(locale)
                          )}
                        </Body1>
                      ))}
                    </div>
                  </Card>

                  <Card className={styles.card}>
                    <Title2>{translate(locale, "snapshotRecurringMerchants")}</Title2>
                    <div className={styles.list}>
                      {insight.snapshot.recurring_merchants.length > 0 ? (
                        insight.snapshot.recurring_merchants.map((item) => (
                          <Body1 key={item.label}>
                            {item.label}
                            {item.count ? ` (${item.count})` : ""}
                          </Body1>
                        ))
                      ) : (
                        <Body1 className={styles.quietText}>
                          {translate(locale, "snapshotNoRecurring")}
                        </Body1>
                      )}
                    </div>
                  </Card>

                  <Card className={styles.card}>
                    <Title2>{translate(locale, "snapshotByOriginalCurrency")}</Title2>
                    <div className={styles.list}>
                      {insight.snapshot.totals.by_original_currency.map((item) => (
                        <Body1 key={item.currency}>
                          {item.currency}:{" "}
                          {formatCurrencyAmount(
                            item.absolute_total,
                            item.currency,
                            intlLocale(locale)
                          )}
                        </Body1>
                      ))}
                    </div>
                  </Card>
                </div>
              </Card>

              {insight.citations.length > 0 ? (
                <Card className={styles.card}>
                  <div className={styles.sectionEyebrow}>{translate(locale, "aiInsightsSourcesTitle")}</div>
                  <Title2>{translate(locale, "aiInsightsSourcesTitle")}</Title2>
                  <div className={styles.sourceList}>
                    {insight.citations.map((citation) => (
                      <Link key={citation} href={citation} target="_blank">
                        {citation}
                      </Link>
                    ))}
                  </div>
                </Card>
              ) : null}
            </>
          ) : (
            <Card className={styles.card}>
              <Body1 className={styles.quietText}>{translate(locale, "aiInsightsNoResult")}</Body1>
            </Card>
          )}
        </section>
      </div>
    </main>
  );
}

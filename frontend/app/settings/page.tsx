"use client";

import { useEffect, useRef, useState } from "react";
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
  Spinner,
  Title1,
  Title2,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";

import { apiFetch, apiFetchJson, ApiError } from "../api";
import {
  DashboardRequestCoordinator,
  recoverableReadFailure,
} from "../dashboard-refresh";
import { useReadRecovery } from "../read-recovery";
import { mergeProviderDraft } from "../settings-drafts";
import {
  type AiProvider,
  type AiProviderSettingsView,
  type AiSettingsResponse,
} from "../ai-types";
import { AuthPage, AuthPanel, SessionCard } from "../auth-card";
import { useAuth } from "../auth-provider";
import { useLocale } from "../locale-provider";
import { useAppShellStyles } from "../app-shell-styles";
import { translate } from "../messages";

type ProviderDraft = {
  apiKey: string;
  model: string;
  saving: boolean;
  removing: boolean;
};

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
  providerGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
    gap: "12px",
  },
  providerCard: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  providerHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: "12px",
    flexWrap: "wrap",
  },
  providerMeta: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
  },
  badgeRow: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  actions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
});

const emptyDrafts: Record<AiProvider, ProviderDraft> = {
  openai: { apiKey: "", model: "", saving: false, removing: false },
  perplexity: { apiKey: "", model: "", saving: false, removing: false },
};

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

export default function SettingsPage() {
  const styles = useStyles();
  const shellStyles = useAppShellStyles();
  const pathname = usePathname();
  const { locale } = useLocale();
  const { refreshSession, status, user } = useAuth();
  const [settings, setSettings] = useState<AiSettingsResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<AiProvider, ProviderDraft>>(emptyDrafts);
  const [error, setError] = useState("");
  const [loadMessage, setLoadMessage] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [failureId, setFailureId] = useState(0);
  const [recoveryTick, setRecoveryTick] = useState(0);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const failureIdRef = useRef(failureId);
  failureIdRef.current = failureId;
  const localeRef = useRef(locale);
  localeRef.current = locale;
  const refreshSessionRef = useRef(refreshSession);
  refreshSessionRef.current = refreshSession;
  const coordinatorRef = useRef(new DashboardRequestCoordinator());
  const signedInUserIdRef = useRef<number | null>(null);
  const { retryNow, resetCycle } = useReadRecovery(
    status === "authenticated",
    failureId,
    () => setRecoveryTick((tick) => tick + 1)
  );

  function applySettings(nextSettings: AiSettingsResponse, resetProvider?: AiProvider) {
    const previous = settingsRef.current;
    setSettings(nextSettings);
    setDrafts((currentDrafts) => {
      const nextDrafts = { ...currentDrafts };
      for (const provider of nextSettings.providers) {
        const previousModel =
          previous?.providers.find((item) => item.provider === provider.provider)?.model ??
          null;
        nextDrafts[provider.provider] = mergeProviderDraft(
          currentDrafts[provider.provider],
          provider.model,
          previousModel,
          provider.provider === resetProvider
        );
      }
      return nextDrafts;
    });
  }

  async function loadSettings() {
    const coordinator = coordinatorRef.current;
    const ticket = coordinator.start();
    const activeLocale = localeRef.current;
    if (settingsRef.current === null && failureIdRef.current === 0) {
      setLoading(true);
    }
    if (failureIdRef.current === 0) {
      setLoadMessage("");
    }
    try {
      const nextSettings = await apiFetchJson<AiSettingsResponse>(
        "/ai/settings",
        translate(activeLocale, "errAiSettingsLoad"),
        { signal: ticket.signal }
      );
      if (!coordinator.isLatest(ticket.generation) || ticket.signal.aborted) {
        return;
      }
      applySettings(nextSettings);
      setLoadMessage("");
      setFailureId(0);
    } catch (loadError) {
      const outcome = recoverableReadFailure(
        loadError,
        settingsRef.current !== null,
        {
          staleData: translate(activeLocale, "dashboardStaleData"),
          unableToRefresh: translate(activeLocale, "initialRecovery"),
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
        void refreshSessionRef.current();
        return;
      }
      setLoadMessage(outcome.message);
      setFailureId((id) => id + 1);
    } finally {
      if (coordinator.isLatest(ticket.generation) && !ticket.signal.aborted) {
        setLoading(false);
      }
    }
  }

  const loadSettingsRef = useRef(loadSettings);
  loadSettingsRef.current = loadSettings;

  useEffect(() => {
    resetCycle();
  }, [resetCycle, status]);

  useEffect(() => {
    if (status !== "authenticated") {
      signedInUserIdRef.current = null;
      setLoading(status === "loading");
      if (status === "unauthenticated") {
        setSettings(null);
        setDrafts(emptyDrafts);
        setFailureId(0);
        setLoadMessage("");
        setError("");
        setMessage("");
      }
      return;
    }
    if (
      user &&
      signedInUserIdRef.current !== null &&
      signedInUserIdRef.current !== user.id
    ) {
      setDrafts(emptyDrafts);
    }
    if (user) {
      signedInUserIdRef.current = user.id;
    }
    void loadSettingsRef.current();
    return () => {
      coordinatorRef.current.start();
    };
  }, [recoveryTick, status, user]);

  function setDraftValue(provider: AiProvider, field: "apiKey" | "model", value: string) {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [provider]: {
        ...currentDrafts[provider],
        [field]: value,
      },
    }));
  }

  async function saveProvider(provider: AiProvider, selected: boolean) {
    const draft = drafts[provider];
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [provider]: {
        ...currentDrafts[provider],
        saving: true,
      },
    }));
    setError("");
    setMessage("");
    coordinatorRef.current.start();
    try {
      const nextSettings = await apiFetchJson<AiSettingsResponse>(
        `/ai/settings/${provider}`,
        translate(locale, "errAiSettingsSave"),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_key: draft.apiKey || null,
            model: draft.model,
            selected,
          }),
        }
      );
      coordinatorRef.current.start();
      applySettings(nextSettings, provider);
      setFailureId(0);
      setLoadMessage("");
      setMessage(translate(locale, "settingsSavedMessage"));
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : translate(locale, "errAiSettingsSave"));
      setDrafts((currentDrafts) => ({
        ...currentDrafts,
        [provider]: {
          ...currentDrafts[provider],
          saving: false,
        },
      }));
    }
  }

  async function removeProvider(provider: AiProvider) {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [provider]: {
        ...currentDrafts[provider],
        removing: true,
      },
    }));
    setError("");
    setMessage("");
    coordinatorRef.current.start();
    try {
      const response = await apiFetch(`/ai/settings/${provider}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const detail = (await response.text()).trim();
        throw new ApiError(
          detail || translate(locale, "errAiSettingsDelete"),
          response.status
        );
      }
      await loadSettings();
      setMessage(translate(locale, "settingsRemovedMessage"));
    } catch (removeError) {
      setError(
        removeError instanceof Error
          ? removeError.message
          : translate(locale, "errAiSettingsDelete")
      );
      setDrafts((currentDrafts) => ({
        ...currentDrafts,
        [provider]: {
          ...currentDrafts[provider],
          removing: false,
        },
      }));
    }
  }

  if (status === "loading" || loading) {
    return (
      <AuthPage>
        <Card className={styles.card}>
          <Spinner label="Loading settings" />
        </Card>
      </AuthPage>
    );
  }

  if (status !== "authenticated") {
    return (
      <AuthPage>
        <AuthPanel
          title={translate(locale, "settingsTitle")}
          body={translate(locale, "settingsBody")}
        />
      </AuthPage>
    );
  }

  return (
    <main className={shellStyles.page}>
      <div className={shellStyles.shell} data-testid="settings-shell">
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
            <div className={styles.sectionEyebrow}>{translate(locale, "navSettings")}</div>
            <Title1>{translate(locale, "settingsTitle")}</Title1>
            <Body1 className={styles.quietText}>{translate(locale, "settingsBody")}</Body1>
          </Card>

          {loadMessage ? (
            <div
              data-testid={
                settings
                  ? "settings-stale-warning"
                  : "settings-initial-recovery"
              }
            >
              <MessageBar intent={settings ? "error" : "warning"}>
                <MessageBarBody>
                  <MessageBarTitle>{translate(locale, "errRequestFailed")}</MessageBarTitle>
                  {loadMessage}
                  <Button appearance="secondary" onClick={retryNow} size="small">
                    {translate(locale, "retryNow")}
                  </Button>
                </MessageBarBody>
              </MessageBar>
            </div>
          ) : null}

          {error ? (
            <MessageBar intent="error">
              <MessageBarBody>
                <MessageBarTitle>{translate(locale, "errRequestFailed")}</MessageBarTitle>
                {error}
              </MessageBarBody>
            </MessageBar>
          ) : null}

          {message ? (
            <MessageBar intent="success">
              <MessageBarBody>
                <MessageBarTitle>{translate(locale, "settingsTitle")}</MessageBarTitle>
                {message}
              </MessageBarBody>
            </MessageBar>
          ) : null}

          <div className={styles.providerGrid}>
            {settings?.providers.map((provider) => {
              const draft = drafts[provider.provider];
              return (
                <Card
                  key={provider.provider}
                  className={mergeClasses(styles.card, styles.providerCard)}
                >
                  <div className={styles.providerHeader}>
                    <div className={styles.providerMeta}>
                      <Title2>{provider.display_name}</Title2>
                      <Body1 className={styles.quietText}>
                        {translate(locale, "settingsProviderCardHint")}
                      </Body1>
                    </div>
                    <div className={styles.badgeRow}>
                      <Badge appearance="outline">
                        {provider.configured
                          ? translate(locale, "settingsStatusConfigured")
                          : translate(locale, "settingsStatusNotConfigured")}
                      </Badge>
                      {provider.selected ? (
                        <Badge appearance="filled">
                          {translate(locale, "settingsStatusActive")}
                        </Badge>
                      ) : null}
                    </div>
                  </div>

                  {provider.masked_api_key ? (
                    <Body1 className={styles.quietText}>
                      {translate(locale, "settingsSavedKeyTemplate", {
                        key: provider.masked_api_key,
                      })}
                    </Body1>
                  ) : null}

                  <Field
                    label={translate(locale, "settingsApiKeyLabel")}
                    hint={translate(locale, "settingsApiKeyHint")}
                  >
                    <Input
                      type="password"
                      value={draft.apiKey}
                      onChange={(_, data) =>
                        setDraftValue(provider.provider, "apiKey", data.value)
                      }
                    />
                  </Field>

                  <Field
                    label={translate(locale, "settingsModelLabel")}
                    hint={translate(locale, "settingsModelHint")}
                  >
                    <Input
                      value={draft.model}
                      onChange={(_, data) =>
                        setDraftValue(provider.provider, "model", data.value)
                      }
                    />
                  </Field>

                  <div className={styles.actions}>
                    <Button
                      appearance="primary"
                      disabled={draft.saving || draft.removing}
                      onClick={() => void saveProvider(provider.provider, true)}
                    >
                      {draft.saving
                        ? translate(locale, "settingsSaving")
                        : translate(locale, "settingsSaveUse")}
                    </Button>
                    {provider.configured && !provider.selected ? (
                      <Button
                        disabled={draft.saving || draft.removing}
                        onClick={() => void saveProvider(provider.provider, true)}
                      >
                        {translate(locale, "settingsUseSaved")}
                      </Button>
                    ) : null}
                    {provider.configured ? (
                      <Button
                        appearance="secondary"
                        disabled={draft.saving || draft.removing}
                        onClick={() => void removeProvider(provider.provider)}
                      >
                        {draft.removing
                          ? translate(locale, "settingsRemoving")
                          : translate(locale, "settingsRemove")}
                      </Button>
                    ) : null}
                  </div>
                </Card>
              );
            })}
          </div>

          <Card className={styles.card}>
            <div className={styles.sectionEyebrow}>{translate(locale, "settingsPrivacyTitle")}</div>
            <Title2>{translate(locale, "settingsPrivacyTitle")}</Title2>
            <Body1 className={styles.quietText}>
              {translate(locale, "settingsPrivacyBody")}
            </Body1>
          </Card>
        </section>
      </div>
    </main>
  );
}

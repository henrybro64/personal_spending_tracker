"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import {
  Body1,
  Button,
  Card,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Title2,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";

import { useAuth } from "./auth-provider";

const useStyles = makeStyles({
  authPage: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    paddingTop: "24px",
    paddingBottom: "24px",
    paddingLeft: "24px",
    paddingRight: "24px",
    backgroundColor: "var(--page-surface)",
    color: tokens.colorNeutralForeground1,
    "@media (max-width: 640px)": {
      paddingLeft: "16px",
      paddingRight: "16px",
    },
  },
  authCard: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    maxWidth: "460px",
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05), 0 10px 28px rgba(0, 0, 0, 0.06)",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusXLarge),
  },
  formGrid: {
    display: "grid",
    gap: "12px",
  },
  actions: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap",
  },
  quietText: {
    color: tokens.colorNeutralForeground3,
  },
  strongText: {
    fontWeight: tokens.fontWeightSemibold,
  },
});

type AuthPageProps = {
  children: ReactNode;
};

export function AuthPage({ children }: AuthPageProps) {
  const styles = useStyles();
  return <main className={styles.authPage}>{children}</main>;
}

type AuthPanelProps = {
  title: string;
  body: string;
};

export function AuthPanel({ title, body }: AuthPanelProps) {
  const styles = useStyles();
  const {
    claimedLegacyTransactions,
    claimedLegacyUploads,
    login,
    register,
  } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit() {
    setSubmitting(true);
    setError("");
    try {
      if (mode === "register") {
        await register({
          display_name: displayName || null,
          email,
          password,
        });
      } else {
        await login({ email, password });
      }
      setPassword("");
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : "Authentication failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className={styles.authCard}>
      <Title2>{title}</Title2>
      <Body1 className={styles.quietText}>{body}</Body1>
      {claimedLegacyTransactions > 0 || claimedLegacyUploads > 0 ? (
        <MessageBar intent="success">
          <MessageBarBody>
            <MessageBarTitle>Legacy data claimed</MessageBarTitle>
            {claimedLegacyTransactions} transaction(s) and {claimedLegacyUploads} upload(s)
            were assigned to your first account.
          </MessageBarBody>
        </MessageBar>
      ) : null}
      {error ? (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>Authentication error</MessageBarTitle>
            {error}
          </MessageBarBody>
        </MessageBar>
      ) : null}
      <div className={styles.formGrid}>
        {mode === "register" ? (
          <Field label="Display name">
            <Input
              value={displayName}
              onChange={(_, data) => setDisplayName(data.value)}
            />
          </Field>
        ) : null}
        <Field label="Username">
          <Input
            type="text"
            autoComplete="username"
            value={email}
            onChange={(_, data) => setEmail(data.value)}
          />
        </Field>
        <Field label="Password">
          <Input
            type="password"
            value={password}
            onChange={(_, data) => setPassword(data.value)}
          />
        </Field>
      </div>
      <div className={styles.actions}>
        <Button
          appearance="primary"
          disabled={submitting}
          onClick={() => void onSubmit()}
        >
          {mode === "register" ? "Create account" : "Sign in"}
        </Button>
        <Button
          disabled={submitting}
          onClick={() => {
            setError("");
            setMode((currentMode) => (currentMode === "login" ? "register" : "login"));
          }}
        >
          {mode === "register" ? "Use existing account" : "Create account"}
        </Button>
      </div>
      <Body1 className={styles.quietText}>
        {mode === "register"
          ? "The first account created here automatically claims any legacy single-user data."
          : "Sign in to view only your own uploads, transactions, summaries, and batch-review data."}
      </Body1>
    </Card>
  );
}

export function SessionCard() {
  const styles = useStyles();
  const { logout, user } = useAuth();

  if (!user) {
    return null;
  }

  return (
    <Card className={styles.authCard}>
      <Body1 className={styles.quietText}>Signed in as</Body1>
      <Title2>{user.display_name}</Title2>
      <Body1 className={styles.strongText}>{user.email}</Body1>
      <Button appearance="secondary" onClick={() => void logout()}>
        Sign out
      </Button>
    </Card>
  );
}

"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
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

import { ApiError, apiUrl } from "./api";
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

// The proxy answers 502 when it cannot reach the backend; 503 and 504 also
// mean the server, not the credentials, is the problem (#96).
const SERVER_UNAVAILABLE_STATUSES = new Set([502, 503, 504]);

type AuthErrorMessage = {
  title: string;
  body: string;
};

function authErrorMessage(error: unknown): AuthErrorMessage {
  if (error instanceof ApiError && SERVER_UNAVAILABLE_STATUSES.has(error.status)) {
    return {
      title: "Server unavailable",
      body: "The app server is not responding. Try again in a moment.",
    };
  }
  return {
    title: "Authentication error",
    body: error instanceof Error ? error.message : "Authentication failed.",
  };
}

// ?auth_error=<code> set by the backend's Microsoft sign-in callback (#100).
const ENTRA_ERROR_MESSAGES: Record<string, AuthErrorMessage> = {
  entra_not_allowed: {
    title: "Microsoft account not allowed",
    body: "This Microsoft account is not allowed to use this app.",
  },
  entra_state_invalid: {
    title: "Microsoft sign-in expired",
    body: "The sign-in took too long or was started in another browser. Try again.",
  },
  entra_signin_failed: {
    title: "Microsoft sign-in failed",
    body: "Microsoft sign-in did not complete. Try again.",
  },
  entra_unavailable: {
    title: "Microsoft sign-in unavailable",
    body: "Microsoft sign-in is not reachable right now. Try again in a moment.",
  },
};

// Only a real object id is echoed, so a crafted link cannot show other text.
const OBJECT_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Read and remove the sign-in error the callback put in the address bar. */
function takeEntraErrorFromUrl(): AuthErrorMessage | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (!code) {
    return null;
  }
  const objectId = url.searchParams.get("entra_object_id") ?? "";
  url.searchParams.delete("auth_error");
  url.searchParams.delete("entra_object_id");
  window.history.replaceState(window.history.state, "", url.toString());

  const message = ENTRA_ERROR_MESSAGES[code] ?? ENTRA_ERROR_MESSAGES.entra_signin_failed;
  if (code === "entra_not_allowed" && OBJECT_ID_PATTERN.test(objectId)) {
    return {
      ...message,
      body: `${message.body} Ask the administrator to allow object ID ${objectId.toLowerCase()}.`,
    };
  }
  return message;
}

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
    providers,
    register,
  } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<AuthErrorMessage | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const entraError = takeEntraErrorFromUrl();
    if (entraError) {
      setError(entraError);
    }
  }, []);

  useEffect(() => {
    if (!providers.localRegistrationEnabled) {
      setMode("login");
    }
  }, [providers.localRegistrationEnabled]);

  function signInWithMicrosoft() {
    const returnTo = `${window.location.pathname}${window.location.search}`;
    window.location.assign(
      apiUrl(`/auth/entra/login?return_to=${encodeURIComponent(returnTo)}`)
    );
  }

  async function onSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        await register({
          display_name: displayName || null,
          email: username,
          password,
        });
      } else {
        await login({ email: username, password });
      }
      setPassword("");
    } catch (authError) {
      setError(authErrorMessage(authError));
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
            <MessageBarTitle>{error.title}</MessageBarTitle>
            {error.body}
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
            value={username}
            onChange={(_, data) => setUsername(data.value)}
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
        {providers.localRegistrationEnabled ? (
          <Button
            disabled={submitting}
            onClick={() => {
              setError(null);
              setMode((currentMode) => (currentMode === "login" ? "register" : "login"));
            }}
          >
            {mode === "register" ? "Use existing account" : "Create account"}
          </Button>
        ) : null}
      </div>
      {providers.entraEnabled && mode === "login" ? (
        <div className={styles.actions}>
          <Button disabled={submitting} onClick={signInWithMicrosoft}>
            Sign in with Microsoft
          </Button>
        </div>
      ) : null}
      <Body1 className={styles.quietText}>
        {mode === "register"
          ? "The first account created here automatically claims any legacy single-user data."
          : "Sign in with your username and password to view only your own data."}
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

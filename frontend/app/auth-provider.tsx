"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { apiFetchJson, ApiError } from "./api";

type SessionUser = {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
};

type SessionResponse = {
  authenticated: boolean;
  user: SessionUser | null;
  claimed_legacy_transactions: number;
  claimed_legacy_uploads: number;
};

type LogoutResponse = SessionResponse & {
  // Microsoft sign-out page, for a user who signed in with Microsoft (#100).
  logout_url?: string | null;
};

type AuthProvidersResponse = {
  local_registration_enabled: boolean;
  entra_enabled: boolean;
};

export type AuthProviders = {
  localRegistrationEnabled: boolean;
  entraEnabled: boolean;
};

// Until /auth/providers answers, offer what every server supports.
const DEFAULT_PROVIDERS: AuthProviders = {
  localRegistrationEnabled: true,
  entraEnabled: false,
};

type AuthPayload = {
  email: string;
  password: string;
  display_name?: string | null;
};

type AuthContextValue = {
  claimedLegacyTransactions: number;
  claimedLegacyUploads: number;
  login: (payload: AuthPayload) => Promise<void>;
  logout: () => Promise<void>;
  providers: AuthProviders;
  refreshSession: () => Promise<void>;
  register: (payload: AuthPayload) => Promise<void>;
  status: "loading" | "authenticated" | "unauthenticated";
  user: SessionUser | null;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function toSessionState(session: SessionResponse) {
  return {
    claimedLegacyTransactions: session.claimed_legacy_transactions,
    claimedLegacyUploads: session.claimed_legacy_uploads,
    status: session.authenticated ? "authenticated" : "unauthenticated",
    user: session.user,
  } as const;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthContextValue["status"]>("loading");
  const statusRef = useRef(status);
  statusRef.current = status;
  const [user, setUser] = useState<SessionUser | null>(null);
  const [claimedLegacyTransactions, setClaimedLegacyTransactions] = useState(0);
  const [claimedLegacyUploads, setClaimedLegacyUploads] = useState(0);
  const [providers, setProviders] = useState<AuthProviders>(DEFAULT_PROVIDERS);

  const applySession = useCallback((session: SessionResponse) => {
    const nextState = toSessionState(session);
    setStatus(nextState.status);
    setUser(nextState.user);
    setClaimedLegacyTransactions(nextState.claimedLegacyTransactions);
    setClaimedLegacyUploads(nextState.claimedLegacyUploads);
  }, []);

  const refreshSession = useCallback(async () => {
    try {
      const session = await apiFetchJson<SessionResponse>(
        "/auth/session",
        "Failed to load session."
      );
      applySession(session);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        applySession({
          authenticated: false,
          user: null,
          claimed_legacy_transactions: 0,
          claimed_legacy_uploads: 0,
        });
        return;
      }
      if (statusRef.current === "loading") {
        applySession({
          authenticated: false,
          user: null,
          claimed_legacy_transactions: 0,
          claimed_legacy_uploads: 0,
        });
        return;
      }

      throw error;
    }
  }, [applySession]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    let cancelled = false;
    apiFetchJson<AuthProvidersResponse>("/auth/providers", "Failed to load sign-in options.")
      .then((response) => {
        if (!cancelled) {
          setProviders({
            localRegistrationEnabled: response.local_registration_enabled,
            entraEnabled: response.entra_enabled,
          });
        }
      })
      .catch(() => {
        // Keep the defaults: password sign-in still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const authenticate = useCallback(
    async (path: "/auth/login" | "/auth/register", payload: AuthPayload) => {
      const session = await apiFetchJson<SessionResponse>(
        path,
        "Authentication failed.",
        {
          body: JSON.stringify(payload),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        }
      );
      applySession(session);
    },
    [applySession]
  );

  const login = useCallback(
    async (payload: AuthPayload) => {
      await authenticate("/auth/login", payload);
    },
    [authenticate]
  );

  const register = useCallback(
    async (payload: AuthPayload) => {
      await authenticate("/auth/register", payload);
    },
    [authenticate]
  );

  const logout = useCallback(async () => {
    const session = await apiFetchJson<LogoutResponse>(
      "/auth/logout",
      "Failed to sign out.",
      { method: "POST" }
    );
    applySession(session);
    // The app session is already revoked; also sign out of Microsoft.
    if (session.logout_url?.startsWith("https://")) {
      window.location.assign(session.logout_url);
    }
  }, [applySession]);

  const value = useMemo<AuthContextValue>(
    () => ({
      claimedLegacyTransactions,
      claimedLegacyUploads,
      login,
      logout,
      providers,
      refreshSession,
      register,
      status,
      user,
    }),
    [
      claimedLegacyTransactions,
      claimedLegacyUploads,
      login,
      logout,
      providers,
      refreshSession,
      register,
      status,
      user,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider.");
  }
  return context;
}

"use client";

import {
  anyAbortSignal,
  isRetryableMethod,
  parseRetryAfter,
  runWithRetry,
  TRANSIENT_HTTP_STATUSES,
} from "./request-retry";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

/**
 * The Next.js proxy waits up to 120s and then returns 502. This budget aborts
 * the in-flight GET so three hung attempts cannot stack into several minutes.
 * Fast 502/503 responses still retry inside the budget.
 */
const DEFAULT_RETRY = {
  maxAttempts: 3,
  baseDelayMs: 500,
  maxDelayMs: 4_000,
  deadlineMs: 20_000,
} as const;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type RetryOptions = {
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  deadlineMs?: number;
  random?: () => number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
};

export type ApiFetchInit = RequestInit & {
  retry?: false | RetryOptions;
};

export async function apiFetch(path: string, init?: ApiFetchInit): Promise<Response> {
  const { retry, ...fetchInit } = init ?? {};
  const method = fetchInit.method ?? "GET";
  const requestUrl = `${apiBaseUrl}${path}`;
  const retryEnabled =
    retry !== false && (retry != null || isRetryableMethod(method));
  const policy = typeof retry === "object" && retry ? retry : {};
  const signal = retryEnabled
    ? anyAbortSignal(
        [
          AbortSignal.timeout(policy.deadlineMs ?? DEFAULT_RETRY.deadlineMs),
          fetchInit.signal ?? undefined,
        ].filter((item): item is AbortSignal => item != null)
      )
    : fetchInit.signal ?? undefined;
  const send = () =>
    fetch(requestUrl, {
      cache: "no-store",
      credentials: "include",
      ...fetchInit,
      signal,
    });

  if (!retryEnabled) {
    return send();
  }

  return runWithRetry({
    maxAttempts: policy.maxAttempts ?? DEFAULT_RETRY.maxAttempts,
    baseDelayMs: policy.baseDelayMs ?? DEFAULT_RETRY.baseDelayMs,
    maxDelayMs: policy.maxDelayMs ?? DEFAULT_RETRY.maxDelayMs,
    signal,
    random: policy.random,
    sleep: policy.sleep,
    run: send,
    shouldRetry: (response) => TRANSIENT_HTTP_STATUSES.has(response.status),
    retryAfterMs: (response) => {
      if (response.status !== 429 && response.status !== 503) {
        return null;
      }
      return parseRetryAfter(response.headers.get("Retry-After"));
    },
    onRetry: ({ attempt, delayMs }) => {
      const statusPath = path.split("?")[0];
      console.info(
        `[api] retry ${method.toUpperCase()} ${statusPath} attempt ${attempt} in ${Math.round(delayMs)}ms`
      );
    },
  });
}

export async function apiFetchJson<T>(
  path: string,
  requestFailedFallback: string,
  init?: ApiFetchInit
): Promise<T> {
  const response = await apiFetch(path, init);
  const responseText = await response.text();

  let payload: unknown = null;
  if (responseText) {
    try {
      payload = JSON.parse(responseText) as unknown;
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const jsonDetail =
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof payload.detail === "string"
        ? payload.detail
        : null;
    const detail =
      jsonDetail ??
      (responseText.trim().slice(0, 300) || requestFailedFallback);
    throw new ApiError(
      jsonDetail ? detail : `HTTP ${response.status}: ${detail}`,
      response.status
    );
  }

  return payload as T;
}

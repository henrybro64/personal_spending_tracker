/**
 * Pure helpers for the API proxy route. No imports from next/* so that
 * `node --test` can load this file directly.
 */

/**
 * Hop-by-hop headers (RFC 9110 section 7.6.1) describe one connection, not
 * the request, so a proxy must not forward them. Node's fetch also rejects
 * several of them (for example Expect and Upgrade) with a bare
 * "fetch failed", which would turn a healthy backend into a 502.
 */
export const HOP_BY_HOP_REQUEST_HEADERS = [
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "expect",
  "te",
  "trailer",
  "proxy-authorization",
  "proxy-connection",
  "host",
];

export function forwardableRequestHeaders(incoming: Headers): Headers {
  const headers = new Headers(incoming);
  // Connection may name more per-hop headers, for example "keep-alive, x-foo".
  const connectionTokens = (incoming.get("connection") ?? "")
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean);
  for (const name of [...HOP_BY_HOP_REQUEST_HEADERS, ...connectionTokens]) {
    headers.delete(name);
  }
  return headers;
}

function errorField(value: unknown, field: "code" | "message" | "name"): string {
  if (value && typeof value === "object" && field in value) {
    const fieldValue = (value as Record<string, unknown>)[field];
    if (typeof fieldValue === "string") {
      return fieldValue.trim();
    }
  }
  return "";
}

/**
 * Why a proxied fetch failed. Node's fetch always says "fetch failed" and
 * keeps the reason in `cause`: ECONNREFUSED when the backend is down,
 * ENOTFOUND for a wrong API_PROXY_TARGET host, and so on. The code comes
 * first because the cause's message can be empty (AggregateError) or name
 * the backend address.
 */
export function describeFetchFailure(
  error: unknown,
  options: { timeoutMs?: number } = {}
): string {
  const name = errorField(error, "name");
  if (name === "TimeoutError" || name === "AbortError") {
    // The only signal on the proxied fetch is AbortSignal.timeout.
    return options.timeoutMs != null
      ? `timed out after ${Math.round(options.timeoutMs / 1000)} s`
      : "timed out";
  }
  const cause =
    error && typeof error === "object" && "cause" in error
      ? (error as { cause: unknown }).cause
      : undefined;
  return (
    errorField(cause, "code") ||
    errorField(cause, "message") ||
    errorField(error, "message") ||
    "API proxy request failed"
  );
}

/** Replace the backend's host name in text that is returned to the browser. */
export function redactBackendHost(text: string, backendUrl: string): string {
  let hostname: string;
  try {
    hostname = new URL(backendUrl).hostname;
  } catch {
    return text;
  }
  return hostname ? text.split(hostname).join("backend") : text;
}

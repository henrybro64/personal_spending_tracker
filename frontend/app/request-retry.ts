export const TRANSIENT_HTTP_STATUSES = new Set([408, 429, 502, 503, 504]);

const RETRY_AFTER_CAP_MS = 30_000;

export type RetryTiming = {
  attempt: number;
  baseDelayMs: number;
  maxDelayMs: number;
  random: number;
  retryAfterMs?: number | null;
};

export function isRetryableMethod(method: string | undefined): boolean {
  const normalized = (method ?? "GET").toUpperCase();
  return normalized === "GET" || normalized === "HEAD";
}

export function isRetryableTransportError(error: unknown): boolean {
  return error instanceof TypeError && error.name !== "AbortError";
}

export function parseRetryAfter(
  header: string | null,
  nowMs = Date.now()
): number | null {
  if (!header) {
    return null;
  }
  const trimmed = header.trim();
  if (!trimmed) {
    return null;
  }
  const seconds = Number(trimmed);
  if (Number.isFinite(seconds)) {
    return Math.min(RETRY_AFTER_CAP_MS, Math.max(0, seconds * 1000));
  }
  const dateMs = Date.parse(trimmed);
  if (Number.isNaN(dateMs)) {
    return null;
  }
  return Math.min(RETRY_AFTER_CAP_MS, Math.max(0, dateMs - nowMs));
}

export function retryDelayMs(timing: RetryTiming): number {
  if (timing.retryAfterMs != null) {
    return Math.min(RETRY_AFTER_CAP_MS, Math.max(0, timing.retryAfterMs));
  }
  const exponential = Math.min(
    timing.maxDelayMs,
    timing.baseDelayMs * 2 ** Math.max(0, timing.attempt - 1)
  );
  const jittered = exponential * (0.75 + timing.random * 0.5);
  return Math.min(timing.maxDelayMs, Math.max(0, jittered));
}

export async function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  if (ms <= 0) {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export type RetryRunOptions<T> = {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  signal?: AbortSignal;
  random?: () => number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  shouldRetry: (value: T) => boolean;
  retryAfterMs?: (value: T) => number | null;
  run: () => Promise<T>;
  onRetry?: (info: { attempt: number; delayMs: number }) => void;
};

export async function runWithRetry<T>(options: RetryRunOptions<T>): Promise<T> {
  const random = options.random ?? Math.random;
  const sleep = options.sleep ?? delay;
  const maxAttempts = Math.max(1, options.maxAttempts);
  let attempt = 1;
  while (true) {
    if (options.signal?.aborted) {
      throw new DOMException("The operation was aborted.", "AbortError");
    }
    try {
      const value = await options.run();
      if (attempt >= maxAttempts || !options.shouldRetry(value)) {
        return value;
      }
      const delayMs = retryDelayMs({
        attempt,
        baseDelayMs: options.baseDelayMs,
        maxDelayMs: options.maxDelayMs,
        random: random(),
        retryAfterMs: options.retryAfterMs?.(value),
      });
      options.onRetry?.({ attempt, delayMs });
      await sleep(delayMs, options.signal);
    } catch (error) {
      if (!isRetryableTransportError(error) || attempt >= maxAttempts) {
        throw error;
      }
      const delayMs = retryDelayMs({
        attempt,
        baseDelayMs: options.baseDelayMs,
        maxDelayMs: options.maxDelayMs,
        random: random(),
      });
      options.onRetry?.({ attempt, delayMs });
      await sleep(delayMs, options.signal);
    }
    attempt += 1;
  }
}

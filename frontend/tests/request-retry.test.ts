import assert from "node:assert/strict";
import { test } from "node:test";

import { apiFetch } from "../app/api";
import { retryDelayMs } from "../app/request-retry";

const noWait = async () => {};

function installFetch(handler: typeof fetch): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  return () => {
    globalThis.fetch = original;
  };
}

test("retry delays include jitter and honor Retry-After", () => {
  const low = retryDelayMs({
    attempt: 1,
    baseDelayMs: 500,
    maxDelayMs: 4_000,
    random: 0,
  });
  const high = retryDelayMs({
    attempt: 1,
    baseDelayMs: 500,
    maxDelayMs: 4_000,
    random: 1,
  });
  assert.notEqual(low, high);
  assert.equal(low < high, true);
  assert.equal(
    retryDelayMs({
      attempt: 2,
      baseDelayMs: 500,
      maxDelayMs: 4_000,
      random: 0,
      retryAfterMs: 2_000,
    }),
    2_000
  );
});

test("a network failure retries and then returns the successful response", async () => {
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    if (calls === 1) {
      throw new TypeError("Failed to fetch");
    }
    return new Response("{}", { status: 200 });
  });
  try {
    const response = await apiFetch("/dashboard/view?currency=USD", {
      retry: { sleep: noWait, random: () => 0 },
    });
    assert.equal(response.status, 200);
    assert.equal(calls, 2);
  } finally {
    restore();
  }
});

test("a 503 retries and a later success is returned", async () => {
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    if (calls === 1) {
      return new Response("unavailable", { status: 503 });
    }
    return new Response("{}", { status: 200 });
  });
  try {
    const response = await apiFetch("/uploads/recent", {
      retry: { sleep: noWait, random: () => 0 },
    });
    assert.equal(response.status, 200);
    assert.equal(calls, 2);
  } finally {
    restore();
  }
});

test("a 429 waits for Retry-After", async () => {
  const delays: number[] = [];
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    if (calls === 1) {
      return new Response("slow", {
        status: 429,
        headers: { "Retry-After": "2" },
      });
    }
    return new Response("{}", { status: 200 });
  });
  try {
    const response = await apiFetch("/transactions?range=all", {
      retry: {
        random: () => 0,
        sleep: async (ms) => {
          delays.push(ms);
        },
      },
    });
    assert.equal(response.status, 200);
    assert.deepEqual(delays, [2_000]);
  } finally {
    restore();
  }
});

test("a 404 is not retried", async () => {
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    return new Response("missing", { status: 404 });
  });
  try {
    const response = await apiFetch("/transactions/9", {
      retry: { sleep: noWait, random: () => 0 },
    });
    assert.equal(response.status, 404);
    assert.equal(calls, 1);
  } finally {
    restore();
  }
});

test("abort during backoff stops further attempts", async () => {
  let calls = 0;
  const controller = new AbortController();
  const restore = installFetch(async () => {
    calls += 1;
    return new Response("unavailable", { status: 503 });
  });
  try {
    await assert.rejects(
      apiFetch("/ai/settings", {
        signal: controller.signal,
        retry: {
          random: () => 0,
          sleep: async () => {
            controller.abort();
            throw new DOMException("The operation was aborted.", "AbortError");
          },
        },
      }),
      (error: unknown) => error instanceof Error && error.name === "AbortError"
    );
    assert.equal(calls, 1);
  } finally {
    restore();
  }
});

test("retry stops after the attempt limit and returns the last response", async () => {
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    return new Response("down", { status: 502 });
  });
  try {
    const response = await apiFetch("/dashboard/view", {
      retry: { sleep: noWait, random: () => 0, maxAttempts: 3 },
    });
    assert.equal(response.status, 502);
    assert.equal(calls, 3);
  } finally {
    restore();
  }
});

test("a hung request stops at the retry deadline instead of stacking attempts", async () => {
  let calls = 0;
  const restore = installFetch((_input, init) => {
    calls += 1;
    return new Promise((_resolve, reject) => {
      const signal = init?.signal;
      if (signal == null) {
        return;
      }
      const fail = () => {
        reject(signal.reason);
      };
      if (signal.aborted) {
        fail();
        return;
      }
      signal.addEventListener("abort", fail, { once: true });
    });
  });
  const keepAlive = setInterval(() => {}, 1_000);
  try {
    await assert.rejects(
      apiFetch("/auth/session", {
        retry: { deadlineMs: 40, sleep: noWait, random: () => 0 },
      }),
      (error: unknown) => error instanceof Error && error.name === "TimeoutError"
    );
    assert.equal(calls, 1);
  } finally {
    clearInterval(keepAlive);
    restore();
  }
});

test("a mutation is not retried", async () => {
  let calls = 0;
  const restore = installFetch(async () => {
    calls += 1;
    return new Response("unavailable", { status: 503 });
  });
  try {
    const response = await apiFetch("/transactions/manual", {
      method: "POST",
      body: "{}",
    });
    assert.equal(response.status, 503);
    assert.equal(calls, 1);
  } finally {
    restore();
  }
});

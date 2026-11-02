import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";

import {
  describeFetchFailure,
  forwardableRequestHeaders,
  redactBackendHost,
} from "../app/api/proxy-helpers";

function fetchFailed(cause: unknown): TypeError {
  return new TypeError("fetch failed", { cause });
}

test("hop-by-hop request headers are not forwarded", () => {
  const out = forwardableRequestHeaders(
    new Headers({
      host: "example.test",
      connection: "keep-alive, x-drop-me",
      "keep-alive": "timeout=5",
      expect: "100-continue",
      upgrade: "websocket",
      te: "trailers",
      trailer: "x-checksum",
      "proxy-authorization": "Basic abc",
      "proxy-connection": "keep-alive",
      "x-drop-me": "1",
      cookie: "session=abc",
      "content-type": "application/json",
    }),
  );
  for (const name of [
    "host",
    "connection",
    "keep-alive",
    "expect",
    "upgrade",
    "te",
    "trailer",
    "proxy-authorization",
    "proxy-connection",
    "x-drop-me",
  ]) {
    assert.equal(out.has(name), false, name);
  }
  assert.equal(out.get("cookie"), "session=abc");
  assert.equal(out.get("content-type"), "application/json");
});

test("the incoming headers are not modified", () => {
  const incoming = new Headers({ expect: "100-continue", cookie: "a=1" });
  forwardableRequestHeaders(incoming);
  assert.equal(incoming.get("expect"), "100-continue");
});

test("forwarded headers are accepted by Node fetch", async () => {
  const server = http
    .createServer((req, res) => {
      req.resume();
      req.on("end", () => res.end("ok"));
    })
    .listen(0);
  const { port } = server.address() as { port: number };
  try {
    const headers = forwardableRequestHeaders(
      new Headers({
        expect: "100-continue",
        "keep-alive": "timeout=5",
        upgrade: "websocket",
        "transfer-encoding": "chunked",
        connection: "keep-alive",
      }),
    );
    const response = await fetch(`http://127.0.0.1:${port}/`, {
      method: "POST",
      headers,
      body: new TextEncoder().encode("{}"),
    });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "ok");
  } finally {
    server.close();
  }
});

test("a refused connection is described by its cause code", async () => {
  const server = http.createServer().listen(0);
  const { port } = server.address() as { port: number };
  await new Promise((resolve) => server.close(resolve));
  const error = await fetch(`http://127.0.0.1:${port}/`).catch((e) => e);
  assert.equal(describeFetchFailure(error), "ECONNREFUSED");
});

test("an AggregateError cause with an empty message still gives its code", () => {
  // Node reports a refused dual-stack host (localhost) this way.
  const cause = Object.assign(new AggregateError([], ""), {
    code: "ECONNREFUSED",
  });
  assert.equal(describeFetchFailure(fetchFailed(cause)), "ECONNREFUSED");
});

test("a cause without a code is described by its message", () => {
  const cause = new Error("invalid upgrade header");
  assert.equal(describeFetchFailure(fetchFailed(cause)), "invalid upgrade header");
});

test("an error without a cause falls back to its own message", () => {
  assert.equal(describeFetchFailure(new Error("boom")), "boom");
  assert.equal(describeFetchFailure("not an error"), "API proxy request failed");
  assert.equal(describeFetchFailure(undefined), "API proxy request failed");
});

test("a timeout says how long the proxy waited", async () => {
  const server = http.createServer(() => {}).listen(0);
  const { port } = server.address() as { port: number };
  try {
    const error = await fetch(`http://127.0.0.1:${port}/`, {
      signal: AbortSignal.timeout(50),
    }).catch((e) => e);
    assert.equal(
      describeFetchFailure(error, { timeoutMs: 120_000 }),
      "timed out after 120 s",
    );
    assert.equal(describeFetchFailure(error), "timed out");
  } finally {
    server.closeAllConnections();
    server.close();
  }
  const aborted = new DOMException("This operation was aborted", "AbortError");
  assert.equal(
    describeFetchFailure(aborted, { timeoutMs: 1_800_000 }),
    "timed out after 1800 s",
  );
});

test("the backend host name is redacted from browser-facing text", () => {
  assert.equal(
    redactBackendHost(
      "connect ECONNREFUSED backend.internal:8000",
      "http://backend.internal:8000",
    ),
    "connect ECONNREFUSED backend:8000",
  );
  assert.equal(redactBackendHost("ECONNREFUSED", "http://localhost:8000"), "ECONNREFUSED");
  assert.equal(redactBackendHost("text", "not a url"), "text");
});

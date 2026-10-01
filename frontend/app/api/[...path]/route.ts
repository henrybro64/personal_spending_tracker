import { NextRequest, NextResponse } from "next/server";

const BACKEND =
  process.env.API_PROXY_TARGET?.replace(/\/$/, "") ?? "http://localhost:8000";

/**
 * Transport and timeout only. Safe GET/HEAD retries live in the browser API
 * client. Outbound AI provider retries live in the backend. This proxy does
 * not retry, so one user action cannot be retried in more than one layer.
 * Batch PDF import can run many minutes; default Node fetch timeouts are too short.
 * Provider retries in the backend must finish inside this non-batch limit.
 */
const BATCH_IMPORT_TIMEOUT_MS = 30 * 60 * 1000;
const DEFAULT_TIMEOUT_MS = 120_000;

async function proxyRequest(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const { path } = await context.params;
  const pathname = path.join("/");
  const url = `${BACKEND}/${pathname}${request.nextUrl.search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const timeoutMs = pathname.startsWith("batch-imports/")
    ? BATCH_IMPORT_TIMEOUT_MS
    : DEFAULT_TIMEOUT_MS;

  let backendResponse: Response;
  try {
    backendResponse = await fetch(url, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "API proxy request failed";
    console.error(`[api proxy] ${request.method} ${url} failed:`, message);
    return NextResponse.json(
      {
        detail: `API proxy to backend failed: ${message}`,
      },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers(backendResponse.headers);
  responseHeaders.delete("transfer-encoding");

  return new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    statusText: backendResponse.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PATCH = proxyRequest;
export const PUT = proxyRequest;
export const DELETE = proxyRequest;

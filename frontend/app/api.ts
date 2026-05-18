"use client";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${apiBaseUrl}${path}`, {
    cache: "no-store",
    credentials: "include",
    ...init,
  });
}

export async function apiFetchJson<T>(
  path: string,
  requestFailedFallback: string,
  init?: RequestInit
): Promise<T> {
  const response = await apiFetch(path, init);

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof payload.detail === "string"
        ? payload.detail
        : requestFailedFallback;
    throw new ApiError(detail, response.status);
  }

  return payload as T;
}

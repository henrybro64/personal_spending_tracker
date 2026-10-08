import { ApiError } from "./api";

export type DashboardFilters = {
  timeRange: string;
  searchQuery: string;
  amountFilter: string;
  sortOption: string;
  selectedMerchant: string | null;
  displayCurrency: string;
};

export type SnapshotPart<T> =
  | { status: "ok"; value: T }
  | { status: "error"; error: unknown };

export type DashboardLoadDecision = "applied" | "ignored" | "error";

export type DashboardFailureCopy = {
  staleData: string;
  unableToRefresh: string;
};

const clearedSlicers = {
  searchQuery: "",
  amountFilter: "all",
  sortOption: "amount_desc",
  selectedMerchant: null,
} as const;

export function refreshAfterUpload(filters: DashboardFilters): DashboardFilters {
  return {
    ...filters,
    ...clearedSlicers,
  };
}

export function buildDashboardViewPath(
  filters: DashboardFilters,
  limit = 5000
): string {
  const params = new URLSearchParams({
    range: filters.timeRange,
    search: filters.searchQuery,
    amount_filter: filters.amountFilter,
    selected_merchant: filters.selectedMerchant ?? "",
    sort: filters.sortOption,
    currency: filters.displayCurrency,
    limit: String(limit),
  });
  return `/dashboard/view?${params.toString()}`;
}

export function dashboardLoadKey(
  filters: DashboardFilters,
  revision: number
): string {
  return `${revision}\n${buildDashboardViewPath(filters)}`;
}

/** Typing waits this long after the last keystroke before the dashboard reloads. */
export const SEARCH_DEBOUNCE_MS = 300;

/** Clearing the search applies at once. Typing waits for a pause. */
export function searchDebounceDelay(searchQuery: string): number {
  return searchQuery === "" ? 0 : SEARCH_DEBOUNCE_MS;
}

/**
 * Search is debounced while typing. A mutation that clears it pins the canonical
 * query so a lagging debounced value cannot revive the pre-clear search. The pin
 * stays in place while the user types a new query, and it releases once the
 * debounced value matches the input.
 */
export function effectiveSearchQuery(
  searchQuery: string,
  debouncedSearchQuery: string,
  pinnedSearch: string | null
): string {
  if (pinnedSearch !== null && debouncedSearchQuery !== searchQuery) {
    return pinnedSearch;
  }
  return debouncedSearchQuery;
}

export function shouldReleaseSearchPin(
  searchQuery: string,
  debouncedSearchQuery: string,
  pinnedSearch: string | null
): boolean {
  if (pinnedSearch === null) {
    return false;
  }
  return debouncedSearchQuery === searchQuery;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function lastRefreshLabel(options: {
  latestUploadAt: string | null;
  hasSnapshot: boolean;
  waitingLabel: string;
}): string {
  if (options.latestUploadAt) {
    return options.latestUploadAt;
  }
  if (options.hasSnapshot) {
    return options.waitingLabel;
  }
  return "—";
}

export function merchantSlicerEmpty(
  hasMerchants: boolean,
  hasSnapshot: boolean
): "list" | "empty" | "hidden" {
  if (hasMerchants) {
    return "list";
  }
  return hasSnapshot ? "empty" : "hidden";
}

/**
 * A failed refresh keeps the last successful snapshot. The raw browser message
 * is replaced with localized copy. API details stay available for HTTP errors.
 */
export function dashboardFailureMessage(
  error: unknown,
  hasCurrent: boolean,
  copy: DashboardFailureCopy
): string {
  if (isAbortError(error) || isUnauthorizedError(error)) {
    return "";
  }
  if (hasCurrent) {
    if (error instanceof ApiError) {
      return `${copy.staleData} ${error.message}`;
    }
    return copy.staleData;
  }
  if (error instanceof ApiError) {
    return error.message;
  }
  return copy.unableToRefresh;
}

export type RecoverableReadFailure =
  | { action: "ignore" }
  | { action: "unauthorized" }
  | { action: "show"; message: string };

/** Classify a finished read. Abort and a stale response do not schedule another try. */
export function recoverableReadFailure(
  error: unknown,
  hasData: boolean,
  copy: DashboardFailureCopy,
  request: { aborted: boolean; latest: boolean }
): RecoverableReadFailure {
  if (isAbortError(error) || request.aborted || !request.latest) {
    return { action: "ignore" };
  }
  if (isUnauthorizedError(error)) {
    return { action: "unauthorized" };
  }
  return {
    action: "show",
    message: dashboardFailureMessage(error, hasData, copy) || copy.unableToRefresh,
  };
}

export function reduceDashboardView<TView>(
  current: TView | null,
  part: SnapshotPart<TView>,
  copy: DashboardFailureCopy
): { view: TView | null; error: string; stale: boolean; unauthorized: boolean } {
  if (part.status === "ok") {
    return { view: part.value, error: "", stale: false, unauthorized: false };
  }
  if (isUnauthorizedError(part.error)) {
    return { view: current, error: "", stale: false, unauthorized: true };
  }
  const hasCurrent = current !== null;
  return {
    view: current,
    error: dashboardFailureMessage(part.error, hasCurrent, copy),
    stale: hasCurrent,
    unauthorized: false,
  };
}

export function reduceRecentUploads<TUploads>(
  current: TUploads[],
  part: SnapshotPart<{ uploads: TUploads[] }>,
  copy: DashboardFailureCopy
): { uploads: TUploads[]; error: string; unauthorized: boolean } {
  if (part.status === "ok") {
    return { uploads: part.value.uploads, error: "", unauthorized: false };
  }
  if (isUnauthorizedError(part.error)) {
    return { uploads: current, error: "", unauthorized: true };
  }
  return {
    uploads: current,
    error: dashboardFailureMessage(part.error, current.length > 0, {
      staleData: copy.unableToRefresh,
      unableToRefresh: copy.unableToRefresh,
    }),
    unauthorized: false,
  };
}

export class DashboardRequestCoordinator {
  private generation = 0;
  private controller: AbortController | null = null;

  start(): { generation: number; signal: AbortSignal; abort: () => void } {
    this.controller?.abort();
    this.generation += 1;
    const controller = new AbortController();
    this.controller = controller;
    const generation = this.generation;
    return {
      generation,
      signal: controller.signal,
      abort: () => {
        controller.abort();
      },
    };
  }

  isLatest(generation: number): boolean {
    return generation === this.generation;
  }
}

export const RECENT_UPLOADS_PATH = "/uploads/recent?limit=6";

type FetchJson = <T>(path: string, signal: AbortSignal) => Promise<T>;

/** Read one path. An abort rethrows; any other failure becomes an error part. */
async function readSnapshotPart<T>(
  path: string,
  signal: AbortSignal,
  fetchJson: FetchJson
): Promise<SnapshotPart<T>> {
  try {
    return { status: "ok", value: await fetchJson<T>(path, signal) };
  } catch (error) {
    if (isAbortError(error) || signal.aborted) {
      throw error instanceof Error
        ? error
        : new DOMException("The operation was aborted.", "AbortError");
    }
    return { status: "error", error };
  }
}

/** The dashboard view depends on the filters, so it reloads when they change. */
export function loadDashboardView<TView>(options: {
  filters: DashboardFilters;
  signal: AbortSignal;
  fetchJson: FetchJson;
}): Promise<SnapshotPart<TView>> {
  return readSnapshotPart<TView>(
    buildDashboardViewPath(options.filters),
    options.signal,
    options.fetchJson
  );
}

/** Recent uploads ignore the filters, so they reload only after data changes. */
export function loadRecentUploads<TUploads>(options: {
  signal: AbortSignal;
  fetchJson: FetchJson;
}): Promise<SnapshotPart<TUploads>> {
  return readSnapshotPart<TUploads>(
    RECENT_UPLOADS_PATH,
    options.signal,
    options.fetchJson
  );
}

export type DashboardReadPart = "view" | "uploads";
export type DashboardReadFailures = Record<DashboardReadPart, boolean>;

/**
 * The view and recent uploads load separately but share one retry cycle.
 * A new failure bumps it. A success clears it only when the other read is
 * healthy too, so one read's success cannot cancel the other's retry.
 */
export function recordDashboardRead(
  failures: DashboardReadFailures,
  part: DashboardReadPart,
  failed: boolean
): { failures: DashboardReadFailures; failureAction: "clear" | "bump" | "keep" } {
  const next = { ...failures, [part]: failed };
  if (failed) {
    return { failures: next, failureAction: "bump" };
  }
  return {
    failures: next,
    failureAction: next.view || next.uploads ? "keep" : "clear",
  };
}

export async function runGuardedDashboardLoad<T>(options: {
  coordinator: DashboardRequestCoordinator;
  generation: number;
  signal: AbortSignal;
  load: () => Promise<T>;
  apply: (value: T) => void;
  onError: (error: unknown) => void;
  onSettled: () => void;
}): Promise<DashboardLoadDecision> {
  try {
    const value = await options.load();
    if (
      !options.coordinator.isLatest(options.generation) ||
      options.signal.aborted
    ) {
      return "ignored";
    }
    options.apply(value);
    return "applied";
  } catch (error) {
    if (
      isAbortError(error) ||
      options.signal.aborted ||
      !options.coordinator.isLatest(options.generation)
    ) {
      return "ignored";
    }
    options.onError(error);
    return "error";
  } finally {
    if (
      options.coordinator.isLatest(options.generation) &&
      !options.signal.aborted
    ) {
      options.onSettled();
    }
  }
}

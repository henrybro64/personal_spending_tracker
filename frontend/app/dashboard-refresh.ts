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

/**
 * Search is deferred while typing. A mutation that clears it pins the canonical
 * query so a lagging deferred value cannot revive the pre-clear search. The pin
 * stays in place while the user types a new query, and it releases once the
 * deferred value matches the input.
 */
export function effectiveSearchQuery(
  searchQuery: string,
  deferredSearchQuery: string,
  pinnedSearch: string | null
): string {
  if (pinnedSearch !== null && deferredSearchQuery !== searchQuery) {
    return pinnedSearch;
  }
  return deferredSearchQuery;
}

export function shouldReleaseSearchPin(
  searchQuery: string,
  deferredSearchQuery: string,
  pinnedSearch: string | null
): boolean {
  if (pinnedSearch === null) {
    return false;
  }
  return deferredSearchQuery === searchQuery;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
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

export async function loadDashboardSnapshot<TUploads, TView>(options: {
  filters: DashboardFilters;
  signal: AbortSignal;
  fetchJson: <T>(path: string, signal: AbortSignal) => Promise<T>;
}): Promise<{ uploads: SnapshotPart<TUploads>; view: SnapshotPart<TView> }> {
  async function read<T>(path: string): Promise<SnapshotPart<T>> {
    try {
      return {
        status: "ok",
        value: await options.fetchJson<T>(path, options.signal),
      };
    } catch (error) {
      if (isAbortError(error) || options.signal.aborted) {
        throw error instanceof Error
          ? error
          : new DOMException("The operation was aborted.", "AbortError");
      }
      return { status: "error", error };
    }
  }

  const [uploads, view] = await Promise.all([
    read<TUploads>("/uploads/recent?limit=6"),
    read<TView>(buildDashboardViewPath(options.filters)),
  ]);
  return { uploads, view };
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

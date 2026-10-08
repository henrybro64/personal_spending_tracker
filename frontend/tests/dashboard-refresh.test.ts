import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../app/api";
import {
  type DashboardFilters,
  DashboardRequestCoordinator,
  buildDashboardViewPath,
  dashboardLoadKey,
  effectiveSearchQuery,
  RECENT_UPLOADS_PATH,
  SEARCH_DEBOUNCE_MS,
  loadDashboardView,
  loadRecentUploads,
  recordDashboardRead,
  reduceDashboardView,
  reduceRecentUploads,
  refreshAfterUpload,
  runGuardedDashboardLoad,
  searchDebounceDelay,
  shouldReleaseSearchPin,
  shouldResetRecoveryOnFilterChange,
} from "../app/dashboard-refresh";

const dirtyFilters: DashboardFilters = {
  timeRange: "year",
  searchQuery: "uber",
  amountFilter: "spend",
  sortOption: "date",
  selectedMerchant: "UBER TRIP",
  displayCurrency: "USD",
};

/** Start a guarded view load that shows the response label when it applies. */
function startLabelLoad(options: {
  coordinator: DashboardRequestCoordinator;
  filters: DashboardFilters;
  fetchJson: <T>(path: string) => Promise<T>;
  show: (label: string) => void;
  errors: unknown[];
}) {
  const ticket = options.coordinator.start();
  const load = runGuardedDashboardLoad({
    coordinator: options.coordinator,
    generation: ticket.generation,
    signal: ticket.signal,
    load: () =>
      loadDashboardView<{ label: string }>({
        filters: options.filters,
        signal: ticket.signal,
        fetchJson: options.fetchJson,
      }),
    apply: (part) => {
      if (part.status === "ok") {
        options.show(part.value.label);
      }
    },
    onError: (error) => options.errors.push(error),
    onSettled: () => {},
  });
  return { ticket, load };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("upload refresh clears slicers and keeps the active range and currency", () => {
  const next = refreshAfterUpload({
    ...dirtyFilters,
    timeRange: "month",
    displayCurrency: "TWD",
  });

  assert.deepEqual(next, {
    timeRange: "month",
    searchQuery: "",
    amountFilter: "all",
    sortOption: "amount_desc",
    selectedMerchant: null,
    displayCurrency: "TWD",
  });
});

test("a cleared search pin survives a lagging deferred query and then releases once", () => {
  const pinned = refreshAfterUpload(dirtyFilters).searchQuery;
  assert.equal(effectiveSearchQuery("", "uber", pinned), "");
  assert.equal(shouldReleaseSearchPin("", "uber", pinned), false);

  assert.equal(effectiveSearchQuery("", "", pinned), "");
  assert.equal(shouldReleaseSearchPin("", "", pinned), true);

  const duringPin = dashboardLoadKey(
    { ...refreshAfterUpload(dirtyFilters), searchQuery: effectiveSearchQuery("", "uber", pinned) },
    4
  );
  const afterRelease = dashboardLoadKey(
    { ...refreshAfterUpload(dirtyFilters), searchQuery: effectiveSearchQuery("", "", null) },
    4
  );
  assert.equal(duringPin, afterRelease);
  assert.match(duringPin, /search=(?:&|$)/);
  assert.match(duringPin, /amount_filter=all/);
  assert.doesNotMatch(duringPin, /search=uber/);
});

test("typing during a cleared search pin does not revive the previous query", () => {
  const cleared = refreshAfterUpload(dirtyFilters);
  const pinned = cleared.searchQuery;
  const revision = 4;

  assert.equal(effectiveSearchQuery("cafe", "uber", pinned), "");
  assert.equal(shouldReleaseSearchPin("cafe", "uber", pinned), false);
  assert.equal(effectiveSearchQuery("cafe", "caf", pinned), "");
  assert.equal(shouldReleaseSearchPin("cafe", "caf", pinned), false);

  const whileDeferredLags = dashboardLoadKey(
    {
      ...cleared,
      searchQuery: effectiveSearchQuery("cafe", "uber", pinned),
    },
    revision
  );
  const whileStillTyping = dashboardLoadKey(
    {
      ...cleared,
      searchQuery: effectiveSearchQuery("cafe", "caf", pinned),
    },
    revision
  );
  assert.equal(whileDeferredLags, whileStillTyping);
  assert.doesNotMatch(whileDeferredLags, /search=uber/);
  assert.doesNotMatch(whileDeferredLags, /search=caf/);

  assert.equal(effectiveSearchQuery("cafe", "cafe", pinned), "cafe");
  assert.equal(shouldReleaseSearchPin("cafe", "cafe", pinned), true);
  const committed = dashboardLoadKey(
    {
      ...cleared,
      searchQuery: effectiveSearchQuery("cafe", "cafe", null),
    },
    revision
  );
  assert.match(committed, /search=cafe/);
  assert.equal(
    committed,
    dashboardLoadKey(
      {
        ...cleared,
        searchQuery: effectiveSearchQuery("cafe", "cafe", pinned),
      },
      revision
    )
  );
});

test("manual refresh keeps filters and changes only the load revision", () => {
  const before = dashboardLoadKey(dirtyFilters, 2);
  const after = dashboardLoadKey(dirtyFilters, 3);
  assert.notEqual(before, after);
  assert.equal(
    buildDashboardViewPath(dirtyFilters),
    buildDashboardViewPath(dirtyFilters)
  );
  assert.match(after, /search=uber/);
  assert.match(after, /currency=USD/);
});

test("an older dashboard response cannot overwrite a newer one", async () => {
  const coordinator = new DashboardRequestCoordinator();
  const displayed: string[] = [];
  const errors: unknown[] = [];
  let loading = false;
  const settled: number[] = [];

  const first = coordinator.start();
  loading = true;
  const firstPayload = deferred<{ label: string }>();
  const firstLoad = runGuardedDashboardLoad({
    coordinator,
    generation: first.generation,
    signal: first.signal,
    load: () => firstPayload.promise,
    apply: (value) => displayed.push(value.label),
    onError: (error) => errors.push(error),
    onSettled: () => {
      loading = false;
      settled.push(first.generation);
    },
  });

  const second = coordinator.start();
  loading = true;
  const secondPayload = deferred<{ label: string }>();
  const secondLoad = runGuardedDashboardLoad({
    coordinator,
    generation: second.generation,
    signal: second.signal,
    load: () => secondPayload.promise,
    apply: (value) => displayed.push(value.label),
    onError: (error) => errors.push(error),
    onSettled: () => {
      loading = false;
      settled.push(second.generation);
    },
  });

  secondPayload.resolve({ label: "latest" });
  await secondLoad;
  firstPayload.resolve({ label: "stale" });
  const firstDecision = await firstLoad;

  assert.equal(firstDecision, "ignored");
  assert.deepEqual(displayed, ["latest"]);
  assert.deepEqual(errors, []);
  assert.equal(loading, false);
  assert.deepEqual(settled, [second.generation]);
});

test("an aborted request does not clear data or surface an error", async () => {
  const coordinator = new DashboardRequestCoordinator();
  const displayed: string[] = [];
  const errors: unknown[] = [];
  let loading = true;

  const ticket = coordinator.start();
  const pending = deferred<string>();
  const load = runGuardedDashboardLoad({
    coordinator,
    generation: ticket.generation,
    signal: ticket.signal,
    load: () => pending.promise,
    apply: (value) => displayed.push(value),
    onError: (error) => errors.push(error),
    onSettled: () => {
      loading = false;
    },
  });

  ticket.abort();
  pending.reject(new DOMException("The operation was aborted.", "AbortError"));
  const decision = await load;

  assert.equal(decision, "ignored");
  assert.deepEqual(displayed, []);
  assert.deepEqual(errors, []);
  assert.equal(loading, true);
});

test("rapid filter changes keep the newest snapshot when responses resolve out of order", async () => {
  const coordinator = new DashboardRequestCoordinator();
  let displayed = "";
  const errors: unknown[] = [];

  const usdFilters: DashboardFilters = { ...dirtyFilters, displayCurrency: "USD" };
  const twdFilters: DashboardFilters = { ...dirtyFilters, displayCurrency: "TWD" };
  const fetches: Array<{
    path: string;
    resolve: (value: { label: string }) => void;
    reject: (error: unknown) => void;
  }> = [];

  function fetchJson<T>(path: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      fetches.push({
        path,
        resolve: (value) => resolve(value as T),
        reject,
      });
    });
  }

  const show = (label: string) => {
    displayed = label;
  };
  const firstLoad = startLabelLoad({
    coordinator,
    filters: usdFilters,
    fetchJson,
    show,
    errors,
  }).load;
  const secondLoad = startLabelLoad({
    coordinator,
    filters: twdFilters,
    fetchJson,
    show,
    errors,
  }).load;

  assert.equal(fetches.length, 2);
  assert.equal(
    fetches.some((item) => item.path.startsWith("/uploads/recent")),
    false
  );
  const twdView = fetches.find((item) => item.path.includes("currency=TWD"));
  const usdView = fetches.find((item) => item.path.includes("currency=USD"));
  assert.ok(twdView);
  assert.ok(usdView);

  twdView.resolve({ label: "TWD" });
  await secondLoad;

  usdView.reject(new Error("stale failure"));
  const firstDecision = await firstLoad;

  assert.equal(firstDecision, "ignored");
  assert.equal(displayed, "TWD");
  assert.deepEqual(errors, []);
});

test("upload, manual entry, and a later filter change keep the newest snapshot", async () => {
  const coordinator = new DashboardRequestCoordinator();
  let displayed = "";
  const errors: unknown[] = [];
  const fetches: Array<{
    path: string;
    resolve: (value: { label: string }) => void;
  }> = [];

  function fetchJson<T>(path: string): Promise<T> {
    return new Promise<T>((resolve) => {
      fetches.push({
        path,
        resolve: (value) => resolve(value as T),
      });
    });
  }

  function startLoad(filters: DashboardFilters) {
    return startLabelLoad({
      coordinator,
      filters,
      fetchJson,
      show: (label) => {
        displayed = label;
      },
      errors,
    });
  }

  const beforeManual = dashboardLoadKey(dirtyFilters, 2);
  const afterManual = dashboardLoadKey(dirtyFilters, 3);
  assert.notEqual(beforeManual, afterManual);
  assert.match(afterManual, /search=uber/);

  const dirtyLoad = startLoad(dirtyFilters);
  const cleared = refreshAfterUpload(dirtyFilters);
  const uploadFilters: DashboardFilters = {
    ...cleared,
    searchQuery: effectiveSearchQuery("cafe", "uber", cleared.searchQuery),
  };
  assert.equal(uploadFilters.searchQuery, "");
  const uploadLoad = startLoad(uploadFilters);
  const latestFilters: DashboardFilters = {
    ...uploadFilters,
    displayCurrency: "TWD",
  };
  const latestLoad = startLoad(latestFilters);

  const uploadView = fetches.find(
    (item) => item.path.includes("currency=USD") && item.path.includes("amount_filter=all")
  );
  const dirtyView = fetches.find((item) => item.path.includes("search=uber"));
  const latestView = fetches.find(
    (item) => item.path.includes("currency=TWD") && item.path.includes("amount_filter=all")
  );
  assert.ok(uploadView);
  assert.ok(dirtyView);
  assert.ok(latestView);
  assert.equal(
    fetches.filter((item) => item.path.startsWith("/dashboard/view")).length,
    3
  );

  latestView.resolve({ label: "cleared-TWD" });
  await latestLoad.load;

  uploadView.resolve({ label: "cleared-USD" });
  dirtyView.resolve({ label: "stale-uber" });
  const uploadDecision = await uploadLoad.load;
  const dirtyDecision = await dirtyLoad.load;

  assert.equal(uploadDecision, "ignored");
  assert.equal(dirtyDecision, "ignored");
  assert.equal(displayed, "cleared-TWD");
  assert.deepEqual(errors, []);
});

const failureCopy = {
  staleData: "Refresh failed. Showing the last successfully loaded data.",
  unableToRefresh: "Unable to refresh data. Check your connection and try again.",
};

const loadedView = { summary: { total_spending: 40 } };

test("a failed refresh keeps the last dashboard snapshot", () => {
  const next = reduceDashboardView(
    loadedView,
    { status: "error", error: new TypeError("Failed to fetch") },
    failureCopy
  );

  assert.equal(next.stale, true);
  assert.equal(next.view, loadedView);
  assert.equal(next.error, failureCopy.staleData);
  assert.equal(next.error.includes("Failed to fetch"), false);
});

test("the first failed load has no snapshot to preserve", () => {
  const next = reduceDashboardView(
    null,
    { status: "error", error: new TypeError("Failed to fetch") },
    failureCopy
  );

  assert.equal(next.view, null);
  assert.equal(next.stale, false);
  assert.equal(next.error, failureCopy.unableToRefresh);
});

test("the next successful refresh replaces stale data and clears the warning", () => {
  const recovered = { summary: { total_spending: 80 } };
  const next = reduceDashboardView(
    loadedView,
    { status: "ok", value: recovered },
    failureCopy
  );

  assert.equal(next.view, recovered);
  assert.equal(next.stale, false);
  assert.equal(next.error, "");
});

test("a 401 keeps the snapshot and leaves session handling to the caller", () => {
  const next = reduceDashboardView(
    loadedView,
    { status: "error", error: new ApiError("unauthorized", 401) },
    failureCopy
  );

  assert.equal(next.unauthorized, true);
  assert.equal(next.stale, false);
  assert.equal(next.error, "");
  assert.equal(next.view, loadedView);
});

test("an HTTP error keeps the API detail", () => {
  const next = reduceDashboardView(
    null,
    { status: "error", error: new ApiError("database down", 500) },
    failureCopy
  );

  assert.equal(next.error, "database down");
});

test("typing waits for a pause, but clearing the search applies at once", () => {
  assert.equal(SEARCH_DEBOUNCE_MS, 300);
  assert.equal(searchDebounceDelay("s"), SEARCH_DEBOUNCE_MS);
  assert.equal(searchDebounceDelay("starbucks"), SEARCH_DEBOUNCE_MS);
  assert.equal(searchDebounceDelay(""), 0);
});

test("the dashboard view and recent uploads load from separate requests", async () => {
  const paths: string[] = [];
  async function fetchJson<T>(path: string): Promise<T> {
    paths.push(path);
    return { label: path } as T;
  }
  const signal = new AbortController().signal;

  const view = await loadDashboardView<{ label: string }>({
    filters: dirtyFilters,
    signal,
    fetchJson,
  });
  const uploads = await loadRecentUploads<{ label: string }>({ signal, fetchJson });

  assert.deepEqual(paths, [buildDashboardViewPath(dirtyFilters), RECENT_UPLOADS_PATH]);
  assert.equal(view.status, "ok");
  assert.equal(uploads.status, "ok");
});

test("a failed uploads read keeps the list and reports an error", () => {
  const current = [{ id: 1 }];
  const next = reduceRecentUploads(
    current,
    { status: "error", error: new TypeError("Failed to fetch") },
    failureCopy
  );
  assert.equal(next.uploads, current);
  assert.equal(next.error, failureCopy.unableToRefresh);
  assert.equal(next.unauthorized, false);
});

test("one read's success does not cancel the other read's retry", () => {
  const healthy = { view: false, uploads: false };

  const uploadsFailed = recordDashboardRead(healthy, "uploads", true);
  assert.equal(uploadsFailed.failureAction, "bump");

  const viewOk = recordDashboardRead(uploadsFailed.failures, "view", false);
  assert.equal(viewOk.failureAction, "keep");

  const uploadsOk = recordDashboardRead(viewOk.failures, "uploads", false);
  assert.equal(uploadsOk.failureAction, "clear");
  assert.deepEqual(uploadsOk.failures, healthy);

  assert.equal(recordDashboardRead(healthy, "view", false).failureAction, "clear");
});

test("a filter change keeps the pending retry for failed recent uploads", () => {
  assert.equal(
    shouldResetRecoveryOnFilterChange({ view: false, uploads: false }),
    true
  );
  assert.equal(
    shouldResetRecoveryOnFilterChange({ view: true, uploads: false }),
    true
  );
  assert.equal(
    shouldResetRecoveryOnFilterChange({ view: false, uploads: true }),
    false
  );
});

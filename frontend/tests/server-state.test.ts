import assert from "node:assert/strict";
import { test } from "node:test";

import {
  invalidateSpendingData,
  resetSpendingDataForTests,
  spendingDataRevision,
  subscribeSpendingData,
} from "../app/server-state";

test("a data-entry mutation refreshes dashboard and transactions subscribers", () => {
  resetSpendingDataForTests();
  const refreshes = { dashboard: 0, transactions: 0 };
  const stopDashboard = subscribeSpendingData(() => {
    refreshes.dashboard += 1;
  });
  const stopTransactions = subscribeSpendingData(() => {
    refreshes.transactions += 1;
  });

  const revisionBefore = spendingDataRevision();
  invalidateSpendingData();

  assert.equal(spendingDataRevision(), revisionBefore + 1);
  assert.equal(refreshes.dashboard, 1);
  assert.equal(refreshes.transactions, 1);

  stopDashboard();
  stopTransactions();
  invalidateSpendingData();
  assert.equal(refreshes.dashboard, 1);
  assert.equal(spendingDataRevision(), revisionBefore + 2);
});

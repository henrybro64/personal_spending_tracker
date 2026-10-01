"use client";

import { useEffect, useState } from "react";

/**
 * One invalidation channel for spending data shared by Dashboard, Data Entry,
 * Transactions, and batch merge.
 *
 * Upload, manual entry, transaction edits, and merges call
 * `invalidateSpendingData()`. Subscribed pages refetch. A page that mounts
 * later reads the current revision and loads fresh data instead of a copy
 * from before the mutation.
 */
const listeners = new Set<() => void>();
let revision = 0;

export function spendingDataRevision(): number {
  return revision;
}

export function invalidateSpendingData(): void {
  revision += 1;
  for (const listener of listeners) {
    listener();
  }
}

export function subscribeSpendingData(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function resetSpendingDataForTests(): void {
  revision = 0;
  listeners.clear();
}

export function useSpendingRevision(): number {
  const [current, setCurrent] = useState(spendingDataRevision);
  useEffect(() => subscribeSpendingData(() => setCurrent(spendingDataRevision())), []);
  return current;
}

"use client";

import { useEffect, useState } from "react";

/**
 * Return `value` once it has stopped changing for `delayMs`.
 *
 * A delay of 0 or less returns `value` on the same render, so a reset (for
 * example clearing a search) applies at once and cancels any pending update.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    if (delayMs <= 0) {
      setDebounced(value);
      return;
    }
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return delayMs <= 0 ? value : debounced;
}

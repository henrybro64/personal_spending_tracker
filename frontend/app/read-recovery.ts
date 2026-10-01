"use client";

import { useCallback, useEffect, useRef } from "react";

/** After the per-request retry budget is spent, wait and try the read again. */
export const RECOVERY_DELAYS_MS = [2_000, 5_000, 10_000, 30_000];
export const RECOVERY_MAX_DELAY_MS = 30_000;

type TimerHandle = ReturnType<typeof setTimeout>;

export function recoveryDelayMs(
  cycle: number,
  random = Math.random(),
  hidden = false
): number {
  if (hidden) {
    return RECOVERY_MAX_DELAY_MS;
  }
  const index = Math.min(
    Math.max(cycle, 0),
    RECOVERY_DELAYS_MS.length - 1
  );
  const jittered = RECOVERY_DELAYS_MS[index] * (0.75 + random * 0.5);
  return Math.min(RECOVERY_MAX_DELAY_MS, Math.round(jittered));
}

export class ReadRecoveryScheduler {
  private timer: TimerHandle | null = null;
  private cycle = 0;
  private active = false;
  private stopped = false;
  private online = true;
  private visible = true;

  constructor(
    private readonly load: () => void,
    private readonly delayFor: (cycle: number, hidden: boolean) => number = (
      cycle,
      hidden
    ) => recoveryDelayMs(cycle, Math.random(), hidden),
    // Call through globalThis. A detached setTimeout throws in the browser.
    private readonly setTimer: (callback: () => void, delay: number) => TimerHandle = (
      callback,
      delay
    ) => globalThis.setTimeout(callback, delay),
    private readonly clearTimer: (handle: TimerHandle) => void = (handle) => {
      globalThis.clearTimeout(handle);
    }
  ) {}

  fail(): void {
    if (this.stopped) {
      return;
    }
    this.active = true;
    if (!this.online) {
      return;
    }
    this.arm();
  }

  succeed(): void {
    this.active = false;
    this.cycle = 0;
    this.clear();
  }

  retryNow(): void {
    if (this.stopped) {
      return;
    }
    this.clear();
    this.cycle = 0;
    this.active = true;
    this.load();
  }

  resetCycle(): void {
    this.cycle = 0;
    this.clear();
    this.active = false;
  }

  stop(): void {
    this.stopped = true;
    this.succeed();
  }

  resume(): void {
    this.stopped = false;
  }

  setOnline(online: boolean): void {
    const changed = this.online !== online;
    this.online = online;
    if (!online) {
      this.clear();
      return;
    }
    if (changed && this.active && !this.stopped) {
      this.cycle = 0;
      this.clear();
      this.load();
    }
  }

  setVisible(visible: boolean): void {
    const changed = this.visible !== visible;
    this.visible = visible;
    if (!this.active || !this.online || this.stopped || !changed) {
      return;
    }
    if (visible) {
      this.cycle = 0;
      this.clear();
      this.load();
      return;
    }
    this.arm();
  }

  private arm(): void {
    this.clear();
    const delay = this.delayFor(this.cycle, !this.visible);
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.cycle += 1;
      if (this.stopped || !this.online) {
        return;
      }
      this.load();
    }, delay);
  }

  private clear(): void {
    if (this.timer === null) {
      return;
    }
    this.clearTimer(this.timer);
    this.timer = null;
  }
}

export function useReadRecovery(
  enabled: boolean,
  failureId: number,
  onRecover: () => void
): { retryNow: () => void; resetCycle: () => void } {
  const onRecoverRef = useRef(onRecover);
  onRecoverRef.current = onRecover;
  const schedulerRef = useRef<ReadRecoveryScheduler | null>(null);
  if (schedulerRef.current === null) {
    schedulerRef.current = new ReadRecoveryScheduler(() => {
      onRecoverRef.current();
    });
  }

  useEffect(() => {
    schedulerRef.current?.resume();
    return () => {
      schedulerRef.current?.stop();
    };
  }, []);

  useEffect(() => {
    const scheduler = schedulerRef.current;
    if (!scheduler) {
      return;
    }
    if (!enabled || failureId === 0) {
      scheduler.succeed();
      return;
    }
    scheduler.fail();
  }, [enabled, failureId]);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") {
      return;
    }
    const scheduler = schedulerRef.current;
    if (!scheduler) {
      return;
    }
    const onOnline = () => scheduler.setOnline(true);
    const onOffline = () => scheduler.setOnline(false);
    const onVisibility = () => {
      scheduler.setVisible(document.visibilityState !== "hidden");
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisibility);
    scheduler.setOnline(navigator.onLine);
    scheduler.setVisible(document.visibilityState !== "hidden");
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled]);

  const retryNow = useCallback(() => {
    schedulerRef.current?.retryNow();
  }, []);
  const resetCycle = useCallback(() => {
    schedulerRef.current?.resetCycle();
  }, []);
  return { retryNow, resetCycle };
}

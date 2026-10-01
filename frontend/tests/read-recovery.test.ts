import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RECOVERY_MAX_DELAY_MS,
  ReadRecoveryScheduler,
  recoveryDelayMs,
} from "../app/read-recovery";

type PendingTimer = { callback: () => void; delay: number; cancelled: boolean };

function controllableScheduler(load: () => void, random = 0) {
  let pending: PendingTimer | null = null;
  const delays: number[] = [];
  const scheduler = new ReadRecoveryScheduler(
    load,
    (cycle, hidden) => {
      const delay = recoveryDelayMs(cycle, random, hidden);
      delays.push(delay);
      return delay;
    },
    ((callback: () => void) => {
      pending = { callback, delay: delays[delays.length - 1] ?? 0, cancelled: false };
      return 1 as unknown as ReturnType<typeof setTimeout>;
    }) as typeof setTimeout,
    (() => {
      if (pending) {
        pending.cancelled = true;
        pending = null;
      }
    }) as typeof clearTimeout
  );
  return {
    scheduler,
    delays,
    fire() {
      const current = pending;
      pending = null;
      if (current && !current.cancelled) {
        current.callback();
      }
    },
    pending: () => pending,
  };
}

test("recovery delays grow with jitter and stay under the cap", () => {
  const low = recoveryDelayMs(0, 0, false);
  const high = recoveryDelayMs(0, 1, false);
  assert.equal(low < high, true);
  assert.equal(high <= RECOVERY_MAX_DELAY_MS, true);
  assert.equal(recoveryDelayMs(0, 0, true), RECOVERY_MAX_DELAY_MS);
  assert.equal(recoveryDelayMs(20, 1, false), RECOVERY_MAX_DELAY_MS);
});

test("a failed read backs off to the cap and success starts over", () => {
  let loads = 0;
  const harness = controllableScheduler(() => {
    loads += 1;
  }, 0);

  harness.scheduler.fail();
  assert.equal(harness.delays[0], recoveryDelayMs(0, 0, false));
  harness.fire();
  harness.scheduler.fail();
  harness.fire();
  harness.scheduler.fail();
  harness.fire();
  harness.scheduler.fail();
  assert.equal(loads, 3);
  assert.equal(
    harness.delays[harness.delays.length - 1] <= RECOVERY_MAX_DELAY_MS,
    true
  );
  assert.equal(
    harness.delays[1] > harness.delays[0],
    true
  );

  harness.scheduler.succeed();
  harness.scheduler.fail();
  assert.equal(harness.delays[harness.delays.length - 1], recoveryDelayMs(0, 0, false));
});

test("retry now cancels the wait and loads immediately", () => {
  let loads = 0;
  const harness = controllableScheduler(() => {
    loads += 1;
  });
  harness.scheduler.fail();
  assert.ok(harness.pending());
  harness.scheduler.retryNow();
  assert.equal(loads, 1);
  assert.equal(harness.pending(), null);
  harness.fire();
  assert.equal(loads, 1);
});

test("stop cancels a pending recovery and ignores later failures", () => {
  let loads = 0;
  const harness = controllableScheduler(() => {
    loads += 1;
  });
  harness.scheduler.fail();
  harness.scheduler.stop();
  harness.fire();
  harness.scheduler.fail();
  assert.equal(loads, 0);
  assert.equal(harness.pending(), null);
});

test("offline pauses recovery and coming back online retries immediately", () => {
  let loads = 0;
  const harness = controllableScheduler(() => {
    loads += 1;
  });
  harness.scheduler.fail();
  harness.scheduler.setOnline(false);
  assert.equal(harness.pending(), null);
  harness.fire();
  assert.equal(loads, 0);
  harness.scheduler.setOnline(true);
  assert.equal(loads, 1);
});

test("a hidden tab waits at the cap instead of the short delay", () => {
  const harness = controllableScheduler(() => {});
  harness.scheduler.setVisible(false);
  harness.scheduler.fail();
  assert.equal(harness.delays[0], RECOVERY_MAX_DELAY_MS);
});

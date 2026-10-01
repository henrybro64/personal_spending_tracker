"""Bounded retries for outbound provider calls.

The Next.js proxy does not retry. The browser retries safe GET/HEAD requests.
This helper is only for external AI provider calls.

The proxy aborts a non-batch request after 120 seconds. Retries stay inside
``RETRY_DEADLINE_SECONDS`` and only start another attempt when a full attempt
timeout still fits, so the proxy does not cut off the last try.

A read or write failure is not retried. The provider may already have accepted
the generation, and replaying it can charge the user twice.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({408, 429, 502, 503, 504})
RETRY_AFTER_CAP_SECONDS = 30.0
RETRY_DEADLINE_SECONDS = 100.0
ATTEMPT_TIMEOUT_SECONDS = 45.0
# Connection failures happen before the provider accepts the generation.
_PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def retry_delay_seconds(
    *,
    attempt: int,
    base_delay: float,
    max_delay: float,
    random_value: float,
    retry_after: float | None,
) -> float:
    if retry_after is not None:
        return min(RETRY_AFTER_CAP_SECONDS, max(0.0, retry_after))
    exponential = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    return min(max_delay, max(0.0, exponential * (0.75 + random_value * 0.5)))


def attempt_fits(
    *,
    elapsed: float,
    delay: float,
    deadline_seconds: float,
    attempt_timeout: float,
) -> bool:
    """True when another full attempt can finish before the proxy deadline."""
    return elapsed + delay + attempt_timeout <= deadline_seconds


def retry_after_seconds(response: httpx.Response, now: datetime | None = None) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    trimmed = header.strip()
    if not trimmed:
        return None
    try:
        return min(RETRY_AFTER_CAP_SECONDS, max(0.0, float(trimmed)))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(trimmed)
    except (TypeError, ValueError, IndexError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return min(RETRY_AFTER_CAP_SECONDS, max(0.0, (retry_at - current).total_seconds()))


async def request_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    provider: str,
    max_attempts: int = 3,
    base_delay: float = 0.75,
    max_delay: float = 5.0,
    deadline_seconds: float = RETRY_DEADLINE_SECONDS,
    attempt_timeout: float = ATTEMPT_TIMEOUT_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    random_value: Callable[[], float] | None = None,
    clock: Callable[[], float] | None = None,
) -> httpx.Response:
    sleeper = sleep or asyncio.sleep
    roll = random_value or random.random
    now = clock or time.monotonic
    started = now()
    attempts = max(1, max_attempts)
    last_response: httpx.Response | None = None
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        elapsed = now() - started
        if attempt > 1 and not attempt_fits(
            elapsed=elapsed,
            delay=0.0,
            deadline_seconds=deadline_seconds,
            attempt_timeout=attempt_timeout,
        ):
            break
        try:
            response = await send()
        except _PRE_SEND_ERRORS as exc:
            last_error = exc
            if not await _schedule_retry(
                attempt=attempt,
                attempts=attempts,
                started=started,
                now=now,
                base_delay=base_delay,
                max_delay=max_delay,
                deadline_seconds=deadline_seconds,
                attempt_timeout=attempt_timeout,
                roll=roll,
                retry_after=None,
                provider=provider,
                category="connect",
                sleeper=sleeper,
            ):
                raise
            continue
        last_response = response
        if response.status_code not in TRANSIENT_STATUS_CODES or attempt >= attempts:
            return response
        if not await _schedule_retry(
            attempt=attempt,
            attempts=attempts,
            started=started,
            now=now,
            base_delay=base_delay,
            max_delay=max_delay,
            deadline_seconds=deadline_seconds,
            attempt_timeout=attempt_timeout,
            roll=roll,
            retry_after=retry_after_seconds(response),
            provider=provider,
            category=str(response.status_code),
            sleeper=sleeper,
        ):
            return response
    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{provider} retry loop ended without a response.")


async def _schedule_retry(
    *,
    attempt: int,
    attempts: int,
    started: float,
    now: Callable[[], float],
    base_delay: float,
    max_delay: float,
    deadline_seconds: float,
    attempt_timeout: float,
    roll: Callable[[], float],
    retry_after: float | None,
    provider: str,
    category: str,
    sleeper: Callable[[float], Awaitable[None]],
) -> bool:
    if attempt >= attempts:
        return False
    delay = retry_delay_seconds(
        attempt=attempt,
        base_delay=base_delay,
        max_delay=max_delay,
        random_value=roll(),
        retry_after=retry_after,
    )
    if not attempt_fits(
        elapsed=now() - started,
        delay=delay,
        deadline_seconds=deadline_seconds,
        attempt_timeout=attempt_timeout,
    ):
        return False
    _log_retry(provider, attempt, delay, category)
    await sleeper(delay)
    return True


def _log_retry(provider: str, attempt: int, delay: float, category: str) -> None:
    logger.info(
        "Retrying %s request after attempt %s (%s) in %.2fs",
        provider,
        attempt,
        category,
        delay,
    )

"""Bounded retries for outbound provider calls.

The Next.js proxy does not retry. The browser retries safe GET/HEAD requests.
This helper is only for external AI provider calls.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({408, 429, 502, 503, 504})
RETRY_AFTER_CAP_SECONDS = 30.0


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
    sleep: Callable[[float], Awaitable[None]] | None = None,
    random_value: Callable[[], float] | None = None,
) -> httpx.Response:
    sleeper = sleep or asyncio.sleep
    roll = random_value or random.random
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as exc:
            if attempt >= attempts:
                raise
            delay = retry_delay_seconds(
                attempt=attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                random_value=roll(),
                retry_after=None,
            )
            _log_retry(provider, attempt, delay, "network")
            await sleeper(delay)
            continue
        if response.status_code not in TRANSIENT_STATUS_CODES or attempt >= attempts:
            return response
        delay = retry_delay_seconds(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            random_value=roll(),
            retry_after=retry_after_seconds(response),
        )
        _log_retry(provider, attempt, delay, str(response.status_code))
        await sleeper(delay)
    raise RuntimeError(f"{provider} retry loop ended without a response.")


def _log_retry(provider: str, attempt: int, delay: float, category: str) -> None:
    logger.info(
        "Retrying %s request after attempt %s (%s) in %.2fs",
        provider,
        attempt,
        category,
        delay,
    )

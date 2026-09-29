"""Run CPU-bound work in a child process so the API process keeps its GIL."""

from __future__ import annotations

import atexit
import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import TypeVar

_T = TypeVar("_T")
_executor: ProcessPoolExecutor | None = None
_executor_lock = threading.Lock()


def _executor_instance() -> ProcessPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            # spawn is safe after Uvicorn has started worker threads. fork is not.
            context = multiprocessing.get_context("spawn")
            _executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
        return _executor


def run_cpu_bound(func: Callable[..., _T], /, *args: object) -> _T:
    """Block until ``func(*args)`` finishes in the parser process.

    Call this from a worker thread. Calling it on the event loop would stall
    ``/health`` for the wait, even though the CPU work itself is elsewhere.
    """
    return _executor_instance().submit(func, *args).result()


def shutdown_process_pool() -> None:
    global _executor
    with _executor_lock:
        executor = _executor
        _executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def occupy_child_process(seconds: float) -> int:
    """Burn CPU in the child and return that process id. Used by tests."""
    deadline = time.perf_counter() + seconds
    total = 0
    while time.perf_counter() < deadline:
        total = (total + 1) % 1_000_003
    return os.getpid()


atexit.register(shutdown_process_pool)

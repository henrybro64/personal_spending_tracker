"""Run CPU-bound work in a child process so the API process keeps its GIL."""

from __future__ import annotations

import atexit
import math
import multiprocessing
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from typing import TypeVar

DEFAULT_PDF_PARSE_TIMEOUT_SECONDS = 120.0

_T = TypeVar("_T")


class _FifoLock:
    """A lock that serves waiters in arrival order.

    ``threading.Lock`` lets a thread that releases and re-acquires in a loop
    (batch import) cut in front of a waiting upload. Here release hands the
    lock straight to the oldest waiter.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._waiters: deque[threading.Event] = deque()
        self._held = False

    def __enter__(self) -> None:
        with self._guard:
            if not self._held:
                self._held = True
                return
            turn = threading.Event()
            self._waiters.append(turn)
        turn.wait()  # release() hands the lock over before setting this

    def __exit__(self, *exc_info: object) -> None:
        with self._guard:
            if self._waiters:
                self._waiters.popleft().set()
            else:
                self._held = False

    def waiting_count(self) -> int:
        with self._guard:
            return len(self._waiters)


_executor: ProcessPoolExecutor | None = None
_executor_lock = threading.Lock()
# One task in flight at a time, first come first served. The pool has one
# worker anyway, and this keeps queue time out of the time limit, so a timeout
# only ever kills its own task.
_run_lock = _FifoLock()


class PdfWorkerError(RuntimeError):
    """The PDF worker process stopped before the work finished."""


class PdfWorkerTimeout(PdfWorkerError):
    """The work ran past PDF_PARSE_TIMEOUT_SECONDS and the worker was stopped."""


def pdf_parse_timeout_seconds() -> float:
    raw = os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PDF_PARSE_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_PDF_PARSE_TIMEOUT_SECONDS
    return value


def _executor_instance() -> ProcessPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            # spawn is safe after Uvicorn has started worker threads. fork is not.
            context = multiprocessing.get_context("spawn")
            _executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
        return _executor


def _terminate_workers(executor: ProcessPoolExecutor) -> None:
    # ProcessPoolExecutor cannot cancel a running task, so stop the process.
    try:
        processes = list((getattr(executor, "_processes", None) or {}).values())
    except RuntimeError:  # the pool's manager thread changed the dict
        processes = []
    for process in processes:
        process.terminate()
    for process in processes:
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)


def _discard_executor(executor: ProcessPoolExecutor, *, terminate: bool) -> None:
    """Drop a broken or stuck pool so the next call starts a fresh worker."""
    global _executor
    with _executor_lock:
        if _executor is executor:
            _executor = None
    if terminate:
        _terminate_workers(executor)
    executor.shutdown(wait=False, cancel_futures=True)


def run_cpu_bound(func: Callable[..., _T], /, *args: object) -> _T:
    """Block until ``func(*args)`` finishes in the parser process.

    Call this from a worker thread. Calling it on the event loop would stall
    ``/health`` for the wait, even though the CPU work itself is elsewhere.

    Raises ``PdfWorkerTimeout`` when the work runs past
    ``PDF_PARSE_TIMEOUT_SECONDS`` and ``PdfWorkerError`` when the worker
    process dies. Either way the next call gets a fresh worker.
    """
    timeout = pdf_parse_timeout_seconds()
    with _run_lock:
        executor = _executor_instance()
        try:
            future = executor.submit(func, *args)
        except BrokenProcessPool:
            # The idle worker died (for example it was killed for memory).
            # Nothing has run yet, so a fresh worker can take the task.
            _discard_executor(executor, terminate=False)
            executor = _executor_instance()
            try:
                future = executor.submit(func, *args)
            except BrokenProcessPool as exc:
                _discard_executor(executor, terminate=False)
                raise PdfWorkerError(
                    "The PDF worker process could not be started."
                ) from exc

        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            if future.done():
                raise  # the task itself raised TimeoutError
            _discard_executor(executor, terminate=True)
            raise PdfWorkerTimeout(
                f"PDF processing passed the {timeout:g} s limit."
            ) from None
        except BrokenProcessPool as exc:
            _discard_executor(executor, terminate=False)
            raise PdfWorkerError("The PDF worker process stopped unexpectedly.") from exc


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

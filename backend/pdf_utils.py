import os
from pathlib import Path
from typing import Any, NamedTuple

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException


class PdfPasswordProtectedError(Exception):
    """PDF requires a password, or the supplied password was rejected."""

    def __init__(self, had_password: bool = False) -> None:
        # had_password is a positional arg so ProcessPoolExecutor can unpickle
        # this exception if it ever crosses the process boundary directly.
        self.had_password = bool(had_password)
        super().__init__(self.had_password)


class _PdfWorkerFailure(NamedTuple):
    """Picklable stand-in for PDF errors raised inside the child process.

    Third-party pdfminer exceptions, and exceptions chained with ``from``,
    are not reliable to unpickle. A failed unpickle marks the process pool
    broken, so workers return this value and the parent re-raises a local
    exception.
    """

    kind: str
    had_password: bool
    message: str


class PdfDecryptError(Exception):
    """PDF encryption could not be opened (unsupported scheme, damaged file, etc.)."""


def _best_page_text(page: Any) -> str:
    """Pick extraction mode that preserves CTBC card table rows (dates + amounts)."""
    candidates: list[str] = []
    for kwargs in (
        {"x_tolerance": 2, "y_tolerance": 2},
        {},
    ):
        try:
            text = page.extract_text(**kwargs) or ""
        except TypeError:
            text = page.extract_text() or ""
        if text.strip():
            candidates.append(text)
    try:
        layout = page.extract_text(layout=True) or ""
    except (TypeError, ValueError, AttributeError):
        layout = ""
    if layout.strip():
        candidates.append(layout)
    if not candidates:
        return ""
    return max(candidates, key=lambda text: _ctbc_table_line_count(text))


def _ctbc_table_line_count(text: str) -> int:
    import re

    pattern = re.compile(
        r"^\s*\d{2,3}/\d{1,2}/\d{1,2}\s+\d{2,3}/\d{1,2}/\d{1,2}\s+",
    )
    return sum(1 for raw in text.splitlines() if pattern.match(raw.strip()))


def _unwrap_pdfminer_exception(exc: BaseException) -> BaseException:
    if isinstance(exc, PdfminerException) and exc.args:
        inner = exc.args[0]
        if isinstance(inner, BaseException):
            return inner
    return exc


def extract_pdf_text(
    file_path: str | Path, password: str | None = None
) -> str:
    # pdfminer is CPU-bound. A thread would still hold this process's GIL and
    # stall the event loop, so the extraction runs in a child process.
    from backend.process_pool import run_cpu_bound

    return _raise_pdf_worker_failure(
        run_cpu_bound(
            _extract_pdf_text_in_process,
            os.fspath(file_path),
            password,
        )
    )


def read_statement_pdf(
    file_path: str | Path,
    password: str | None = None,
    *,
    max_wait: float | None = None,
    original_file_name: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Extract and parse one statement without holding the API process GIL.

    ``max_wait`` limits how long to wait for other PDFs to finish first.
    ``original_file_name`` is the user-facing name (uploads are stored under a
    UUID); parsers use it for bank/period hints such as E.SUN file names.
    """
    from backend.process_pool import run_cpu_bound

    return _raise_pdf_worker_failure(
        run_cpu_bound(
            _read_statement_pdf_in_process,
            os.fspath(file_path),
            password,
            (original_file_name or "").strip(),
            max_wait=max_wait,
        )
    )


def _pdf_worker_failure(exc: BaseException) -> _PdfWorkerFailure:
    if isinstance(exc, PdfPasswordProtectedError):
        return _PdfWorkerFailure("password", exc.had_password, "")
    if isinstance(exc, PdfDecryptError):
        return _PdfWorkerFailure("decrypt", False, str(exc))
    if isinstance(exc, PdfminerException):
        return _PdfWorkerFailure("pdfminer", False, str(exc))
    return _PdfWorkerFailure(
        "unexpected",
        False,
        f"{type(exc).__name__}: {exc}",
    )


def _raise_pdf_worker_failure(result: Any) -> Any:
    if not isinstance(result, _PdfWorkerFailure):
        return result
    if result.kind == "password":
        raise PdfPasswordProtectedError(result.had_password)
    if result.kind == "decrypt":
        raise PdfDecryptError(result.message or "Unable to decrypt PDF")
    if result.kind == "pdfminer":
        raise PdfminerException(result.message)
    raise RuntimeError(result.message or "PDF worker failed")


def _read_statement_pdf_in_process(
    file_path: str,
    password: str | None,
    original_file_name: str = "",
) -> tuple[str, list[dict[str, Any]]] | _PdfWorkerFailure:
    from backend.parser import parse_transactions

    try:
        text = _extract_pdf_text_unlocked(file_path, password)
        parse_name = original_file_name.strip() or Path(file_path).name
        return text, list(parse_transactions(text, file_name=parse_name))
    except Exception as exc:
        return _pdf_worker_failure(exc)


def _extract_pdf_text_in_process(
    file_path: str, password: str | None = None
) -> str | _PdfWorkerFailure:
    try:
        return _extract_pdf_text_unlocked(file_path, password)
    except Exception as exc:
        return _pdf_worker_failure(exc)


def _extract_pdf_text_unlocked(
    file_path: str, password: str | None = None
) -> str:
    pdf_path = Path(file_path)
    page_text: list[str] = []
    normalized = (password or "").strip()
    open_password: str | None = normalized or None

    pwd_arg = open_password if open_password is not None else ""
    try:
        with pdfplumber.open(pdf_path, password=pwd_arg) as pdf:
            for page in pdf.pages:
                page_text.append(_best_page_text(page))
    except PdfminerException as e:
        inner = _unwrap_pdfminer_exception(e)
        if isinstance(inner, PDFPasswordIncorrect):
            raise PdfPasswordProtectedError(
                had_password=bool(open_password)
            ) from e
        if isinstance(inner, PDFEncryptionError):
            raise PdfDecryptError() from e
        raise
    except PDFPasswordIncorrect:
        raise PdfPasswordProtectedError(
            had_password=bool(open_password)
        ) from None
    except PDFEncryptionError:
        raise PdfDecryptError() from None

    return "\n".join(page_text)

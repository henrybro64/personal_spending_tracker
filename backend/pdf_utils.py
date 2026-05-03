from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException


class PdfPasswordProtectedError(Exception):
    """PDF requires a password, or the supplied password was rejected."""

    def __init__(self, *, had_password: bool) -> None:
        self.had_password = had_password
        super().__init__()


class PdfDecryptError(Exception):
    """PDF encryption could not be opened (unsupported scheme, damaged file, etc.)."""


def _best_page_text(page: Any) -> str:
    """Pick plain or layout extraction — layout often preserves aligned columns."""
    plain = page.extract_text() or ""
    try:
        layout = page.extract_text(layout=True) or ""
    except (TypeError, ValueError, AttributeError):
        layout = ""
    plain_stripped = plain.strip()
    layout_stripped = layout.strip()
    if len(layout_stripped) > len(plain_stripped):
        return layout
    return plain


def _unwrap_pdfminer_exception(exc: BaseException) -> BaseException:
    if isinstance(exc, PdfminerException) and exc.args:
        inner = exc.args[0]
        if isinstance(inner, BaseException):
            return inner
    return exc


def extract_pdf_text(
    file_path: str | Path, password: str | None = None
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

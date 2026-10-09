from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from backend.transaction_source import statement_content_fingerprint

from .category import CategoryEngine
from .models import BatchImportResult, NormalizedTransaction
from .parsers import resolve_parser
from .paths import (
    default_csv_output_path,
    default_pdf_source_dir,
    default_text_source_dir,
    resolve_allowed_directory,
    resolve_allowed_file,
)
from .storage import BatchImportStore
from .upload_fallback import (
    merge_normalized_transactions,
    transactions_from_upload_parser,
    upload_fallback_supported,
)


class StatementImporter:
    def __init__(
        self,
        *,
        pdf_source_dir: str | Path | None = None,
        text_source_dir: str | Path | None = None,
        csv_output_path: str | Path | None = None,
        store: BatchImportStore | None = None,
        pdf_text_extractor: Callable[[str | Path], str] | None = None,
    ):
        self.pdf_source_dir = resolve_allowed_directory(
            pdf_source_dir,
            default=default_pdf_source_dir(),
        )
        self.text_source_dir = resolve_allowed_directory(
            text_source_dir,
            default=default_text_source_dir(),
        )
        self.csv_output_path = resolve_allowed_file(
            csv_output_path,
            default=default_csv_output_path(),
        )
        self.store = store or BatchImportStore()
        self.pdf_text_extractor = (
            pdf_text_extractor or self._default_pdf_text_extractor
        )

    def import_transactions(self) -> BatchImportResult:
        self.store.initialize()
        if not self.pdf_source_dir.exists() and not self.text_source_dir.exists():
            raise FileNotFoundError(
                "Neither source directory exists: "
                f"{self.pdf_source_dir} or {self.text_source_dir}"
            )

        category_engine = CategoryEngine(self.store.load_learned_rules())
        transactions: list[NormalizedTransaction] = []
        parsed_files: list[str] = []
        skipped_files: list[dict[str, str]] = []

        for source_path in self._iter_source_files():
            try:
                statement_text = self._load_statement_text(source_path)
                content_sha256 = statement_content_fingerprint(statement_text)
                parser = resolve_parser(source_path.name, statement_text)
                if parser is None:
                    skipped_files.append(
                        {
                            "statement_file": source_path.name,
                            "reason": "unsupported_format",
                        }
                    )
                    continue

                parsed_transactions = parser.parse(
                    source_path=source_path,
                    statement_text=statement_text,
                    category_engine=category_engine,
                )
                if upload_fallback_supported(parser.name):
                    fallback_transactions = transactions_from_upload_parser(
                        source_path,
                        statement_text,
                        category_engine,
                        bank=parser.bank,
                    )
                    if not parsed_transactions:
                        parsed_transactions = fallback_transactions
                    elif fallback_transactions:
                        parsed_transactions = merge_normalized_transactions(
                            parsed_transactions,
                            fallback_transactions,
                        )
                if not parsed_transactions:
                    skipped_files.append(
                        {
                            "statement_file": source_path.name,
                            "reason": "no_transactions_found",
                        }
                    )
                    continue

                transactions.extend(
                    replace(transaction, statement_content_sha256=content_sha256)
                    for transaction in parsed_transactions
                )
                parsed_files.append(source_path.name)
            except _PDF_READ_ERRORS as exc:
                skipped_files.append(
                    {
                        "statement_file": source_path.name,
                        "reason": _pdf_skip_reason(exc),
                    }
                )
            except Exception as exc:
                skipped_files.append(
                    {
                        "statement_file": source_path.name,
                        "reason": "processing_error",
                        "detail": str(exc)[:200],
                    }
                )

        run_id, imported_at, needs_review_count = self.store.record_import_run(
            transactions,
            pdf_source_dir=str(self.pdf_source_dir),
            text_source_dir=str(self.text_source_dir),
            csv_output_path=str(self.csv_output_path),
            statement_count=len(parsed_files),
        )
        auto_categorized = self.store.auto_apply_default_categories(
            run_id=run_id,
            category_engine=category_engine,
        )
        if auto_categorized:
            needs_review_count = self.store.count_needs_review(run_id=run_id)
        csv_output_path = self.store.export_csv(run_id, self.csv_output_path)

        return BatchImportResult(
            run_id=run_id,
            imported_at=imported_at,
            statement_count=len(parsed_files),
            transaction_count=len(transactions),
            needs_review_count=needs_review_count,
            csv_output_path=csv_output_path,
            sqlite_output_path=str(self.store.database_path),
            parsed_files=parsed_files,
            skipped_files=skipped_files,
        )

    def _iter_source_files(self) -> list[Path]:
        files: list[Path] = []
        if self.pdf_source_dir.exists():
            files.extend(sorted(self.pdf_source_dir.glob("*.pdf")))
        if self.text_source_dir.exists():
            files.extend(sorted(self.text_source_dir.glob("*.txt")))
        return files

    def _load_statement_text(self, source_path: Path) -> str:
        if source_path.suffix.lower() == ".pdf":
            return self.pdf_text_extractor(source_path)
        return source_path.read_text(encoding="utf-8")

    @staticmethod
    def _default_pdf_text_extractor(source_path: str | Path) -> str:
        from backend.pdf_utils import extract_pdf_text

        return extract_pdf_text(source_path)


def _pdf_skip_reason(exc: BaseException) -> str:
    from backend.pdf_utils import PdfDecryptError, PdfPasswordProtectedError
    from backend.process_pool import PdfWorkerError, PdfWorkerTimeout
    from pdfplumber.utils.exceptions import PdfminerException

    # The worker is replaced after either failure, so the run can go on.
    if isinstance(exc, PdfWorkerTimeout):
        return "timeout"
    if isinstance(exc, PdfWorkerError):
        return "processing_error"
    if isinstance(exc, PdfPasswordProtectedError):
        return "password_required"
    if isinstance(exc, PdfDecryptError):
        return "decrypt_error"
    if isinstance(exc, PdfminerException):
        return "pdf_read_error"
    return "read_error"


def _pdf_read_errors() -> tuple[type[BaseException], ...]:
    from backend.pdf_utils import PdfDecryptError, PdfPasswordProtectedError
    from backend.process_pool import PdfWorkerError
    from pdfplumber.utils.exceptions import PdfminerException

    return (
        PdfWorkerError,
        PdfPasswordProtectedError,
        PdfDecryptError,
        PdfminerException,
        OSError,
        UnicodeDecodeError,
    )


_PDF_READ_ERRORS = _pdf_read_errors()

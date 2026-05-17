from __future__ import annotations

from pathlib import Path
from typing import Callable

from .category import CategoryEngine
from .models import BatchImportResult, NormalizedTransaction
from .parsers import resolve_parser
from .storage import BatchImportStore


DEFAULT_PDF_SOURCE_DIRNAME = "Credit_Card_PDF"
DEFAULT_TEXT_SOURCE_DIRNAME = "credit_card_text"
DEFAULT_CSV_OUTPUT_NAME = "batch_transactions.csv"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_pdf_source_dir() -> Path:
    return _repo_root() / DEFAULT_PDF_SOURCE_DIRNAME


def default_text_source_dir() -> Path:
    return _repo_root() / DEFAULT_TEXT_SOURCE_DIRNAME


def default_csv_output_path() -> Path:
    return _repo_root() / "storage" / DEFAULT_CSV_OUTPUT_NAME


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
        self.pdf_source_dir = Path(pdf_source_dir or default_pdf_source_dir())
        self.text_source_dir = Path(text_source_dir or default_text_source_dir())
        self.csv_output_path = Path(csv_output_path or default_csv_output_path())
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
            statement_text = self._load_statement_text(source_path)
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
            if not parsed_transactions:
                skipped_files.append(
                    {
                        "statement_file": source_path.name,
                        "reason": "no_transactions_found",
                    }
                )
                continue

            transactions.extend(parsed_transactions)
            parsed_files.append(source_path.name)

        run_id, imported_at, needs_review_count = self.store.record_import_run(
            transactions,
            pdf_source_dir=str(self.pdf_source_dir),
            text_source_dir=str(self.text_source_dir),
            csv_output_path=str(self.csv_output_path),
            statement_count=len(parsed_files),
        )
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

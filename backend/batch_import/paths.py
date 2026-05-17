from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PDF_SOURCE_DIRNAME = "Credit_Card_PDF"
DEFAULT_TEXT_SOURCE_DIRNAME = "credit_card_text"
DEFAULT_BATCH_DATA_DIRNAME = "storage"
DEFAULT_CSV_OUTPUT_NAME = "batch_transactions.csv"
DEFAULT_SQLITE_OUTPUT_NAME = "batch_imports.sqlite3"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def batch_data_root() -> Path:
    configured = os.getenv("BATCH_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / DEFAULT_BATCH_DATA_DIRNAME


def default_pdf_source_dir() -> Path:
    configured = os.getenv("BATCH_PDF_SOURCE_DIR")
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / DEFAULT_PDF_SOURCE_DIRNAME


def default_text_source_dir() -> Path:
    configured = os.getenv("BATCH_TEXT_SOURCE_DIR")
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / DEFAULT_TEXT_SOURCE_DIRNAME


def default_csv_output_path() -> Path:
    return batch_data_root() / DEFAULT_CSV_OUTPUT_NAME


def default_sqlite_output_path() -> Path:
    return batch_data_root() / DEFAULT_SQLITE_OUTPUT_NAME


def allowed_roots() -> tuple[Path, ...]:
    return (
        batch_data_root().resolve(),
        default_pdf_source_dir().resolve(),
        default_text_source_dir().resolve(),
    )


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def resolve_allowed_directory(
    path: str | Path | None,
    *,
    default: Path,
) -> Path:
    if path is None:
        return default.resolve()

    resolved = Path(path).expanduser().resolve()

    for root in allowed_roots():
        if _is_within_root(resolved, root):
            return resolved

    allowed = ", ".join(str(root) for root in allowed_roots())
    raise ValueError(
        f"Directory {resolved} is outside allowed batch-import roots: {allowed}"
    )


def resolve_allowed_file(
    path: str | Path | None,
    *,
    default: Path,
) -> Path:
    if path is None:
        return default.resolve()

    resolved = Path(path).expanduser().resolve()
    parent = resolved.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)

    for root in allowed_roots():
        if _is_within_root(resolved, root) or _is_within_root(parent, root):
            return resolved

    allowed = ", ".join(str(root) for root in allowed_roots())
    raise ValueError(
        f"File path {resolved} is outside allowed batch-import roots: {allowed}"
    )

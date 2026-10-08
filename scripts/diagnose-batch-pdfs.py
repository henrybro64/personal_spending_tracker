#!/usr/bin/env python3
"""Find the first PDF in a batch folder that breaks import-style processing.

Run inside the backend pod (or locally with the same env vars):

  python scripts/diagnose-batch-pdfs.py /app/batch/pdf/user-1

Optional: stop after N failures (default 5):

  python scripts/diagnose-batch-pdfs.py /app/batch/pdf/user-1 --max-failures 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/diagnose-batch-pdfs.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.batch_import import StatementImporter  # noqa: E402
from backend.batch_import.parsers import resolve_parser  # noqa: E402
from backend.batch_import.category import CategoryEngine  # noqa: E402
from backend.pdf_utils import (  # noqa: E402
    PdfDecryptError,
    PdfPasswordProtectedError,
    extract_pdf_text,
)


def process_one(path: Path) -> tuple[str, str | None]:
    try:
        if path.suffix.lower() == ".pdf":
            text = extract_pdf_text(path)
        else:
            text = path.read_text(encoding="utf-8")
    except PdfPasswordProtectedError:
        return "password_required", None
    except PdfDecryptError:
        return "decrypt_error", None
    except Exception as exc:
        return "pdf_read_error", str(exc)

    parser = resolve_parser(path.name, text)
    if parser is None:
        return "unsupported_format", None

    try:
        rows = parser.parse(
            source_path=path,
            statement_text=text,
            category_engine=CategoryEngine(),
        )
    except Exception as exc:
        return "processing_error", str(exc)

    if not rows:
        return "no_transactions_found", None
    return "ok", f"{len(rows)} transactions"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path, help="Directory containing *.pdf")
    parser.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="Stop after this many non-ok files (0 = no limit)",
    )
    args = parser.parse_args()

    pdf_dir = args.pdf_dir.expanduser().resolve()
    if not pdf_dir.is_dir():
        print(f"error: not a directory: {pdf_dir}", file=sys.stderr)
        return 1

    files = sorted(pdf_dir.glob("*.pdf"))
    print(f"Scanning {len(files)} PDF(s) in {pdf_dir}\n")

    counts: dict[str, int] = {}
    failures = 0

    for index, path in enumerate(files, start=1):
        status, detail = process_one(path)
        counts[status] = counts.get(status, 0) + 1
        if status != "ok":
            failures += 1
            line = f"[{index}/{len(files)}] {status}: {path.name}"
            if detail:
                line += f" — {detail[:160]}"
            print(line)
            if args.max_failures and failures >= args.max_failures:
                print(f"\nStopped after {failures} failure(s).")
                break
        elif index <= 3 or index == len(files):
            print(f"[{index}/{len(files)}] ok: {path.name} ({detail})")

    print("\nSummary:")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

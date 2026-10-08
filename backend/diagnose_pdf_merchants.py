"""Scan batch PDFs: raw text vs parsed rows (Uber / 優食 / 優步 focus).

Run inside the backend pod (or locally with the same paths):

  python3 -m backend.diagnose_pdf_merchants /app/batch/pdf/user-1

Optional SQLite batch store:

  python3 -m backend.diagnose_pdf_merchants /app/batch/pdf/user-1 \\
      --sqlite /app/batch/data/batch_imports_user_1.sqlite3
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from backend.batch_import.category import CategoryEngine
from backend.merchant_noise import merchant_raw_is_parse_noise
from backend.batch_import.parsers import resolve_parser
from backend.batch_import.upload_fallback import transactions_from_upload_parser
from backend.parser import parse_transactions
from backend.statement_parse_utils import parse_ctbc_roc_statement_line
from backend.pdf_utils import (
    PdfDecryptError,
    PdfPasswordProtectedError,
    extract_pdf_text,
)

_MERCHANT_PROBE = re.compile(
    r"uber|ubereats|eats|優食|優步|优步|foodpanda",
    re.IGNORECASE,
)
_CTBC_LINE = re.compile(
    r"^\s*\d{2,3}/\d{1,2}/\d{1,2}\s+\d{2,3}/\d{1,2}/\d{1,2}\s+.+"
)


def _probe_lines(text: str, limit: int = 8) -> list[str]:
    hits: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or not _MERCHANT_PROBE.search(line):
            continue
        hits.append(line[:200])
        if len(hits) >= limit:
            break
    return hits


def _ctbc_like_line_count(text: str) -> int:
    return sum(1 for raw in text.splitlines() if _CTBC_LINE.match(raw.strip()))


def _unparsed_ctbc_line_samples(text: str, limit: int = 5) -> list[str]:
    samples: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or not _CTBC_LINE.match(line):
            continue
        if parse_ctbc_roc_statement_line(line) is not None:
            continue
        samples.append(line[:220])
        if len(samples) >= limit:
            break
    return samples


def _row_merchant(row: object) -> str:
    if isinstance(row, dict):
        return str(row.get("merchant") or row.get("merchant_raw") or "")
    return str(getattr(row, "merchant_raw", "") or getattr(row, "merchant", ""))


def _merchant_hits(transactions: list) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in transactions:
        merchant = _row_merchant(row)
        if merchant and _MERCHANT_PROBE.search(merchant):
            counts[merchant] += 1
    return counts


def _analyze_pdf(path: Path, engine: CategoryEngine) -> dict[str, object]:
    try:
        text = extract_pdf_text(path)
    except PdfPasswordProtectedError:
        return {"status": "password_required"}
    except PdfDecryptError:
        return {"status": "decrypt_error"}
    except Exception as exc:
        return {"status": "read_error", "detail": str(exc)[:200]}

    parser = resolve_parser(path.name, text)
    if parser is None:
        return {"status": "unsupported_format", "text_chars": len(text)}

    batch_rows = parser.parse(path, text, engine)
    upload_rows = transactions_from_upload_parser(
        path, text, engine, bank=parser.bank
    )
    upload_dict_rows = parse_transactions(text)

    raw_probe_lines = _probe_lines(text)
    batch_probe = _merchant_hits(batch_rows)
    upload_probe = _merchant_hits(upload_rows)
    parser_probe: Counter[str] = Counter()
    for row in upload_dict_rows:
        merchant = row.get("merchant", "")
        if _MERCHANT_PROBE.search(merchant):
            parser_probe[merchant] += 1

    digit_noise = sum(
        1 for row in batch_rows if merchant_raw_is_parse_noise(_row_merchant(row))
    )
    ctbc_like_lines = _ctbc_like_line_count(text)

    return {
        "status": "ok",
        "parser": parser.name,
        "text_chars": len(text),
        "ctbc_like_lines": ctbc_like_lines,
        "unparsed_ctbc_samples": _unparsed_ctbc_line_samples(text),
        "batch_tx_count": len(batch_rows),
        "upload_fallback_count": len(upload_rows),
        "parse_transactions_count": len(upload_dict_rows),
        "parse_noise_batch": digit_noise,
        "raw_probe_lines": raw_probe_lines,
        "batch_merchants": dict(batch_probe),
        "upload_merchants": dict(upload_probe),
        "parser_merchants": dict(parser_probe),
    }


def _sqlite_uber_summary(db_path: Path) -> None:
    if not db_path.is_file():
        print(f"\nSQLite not found: {db_path}")
        return

    print(f"\n=== SQLite merchants ({db_path}) ===")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT id, imported_at, transaction_count, needs_review_count "
            "FROM import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run is None:
            print("  (no import runs)")
            return
        print(
            f"  Latest run id={run['id']} "
            f"imported_at={run['imported_at']} "
            f"tx={run['transaction_count']} "
            f"needs_review={run['needs_review_count']}"
        )
        rows = conn.execute(
            """
            SELECT merchant_raw, COUNT(*) AS n
            FROM imported_transactions
            WHERE import_run_id = ?
              AND (
                merchant_raw LIKE '%UBER%'
                OR merchant_raw LIKE '%uber%'
                OR merchant_raw LIKE '%優食%'
                OR merchant_raw LIKE '%優步%'
                OR merchant_raw LIKE '%EATS%'
                OR merchant_raw LIKE '%Foodpanda%'
                OR merchant_raw LIKE '%FOODPANDA%'
              )
            GROUP BY merchant_raw
            ORDER BY n DESC
            LIMIT 30
            """,
            (run["id"],),
        ).fetchall()
        if not rows:
            print("  No Uber/優食/優步/Eats/Foodpanda merchants in latest run.")
        else:
            for row in rows:
                print(f"  {row['n']:4d}  {row['merchant_raw']}")

        noise = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM imported_transactions
            WHERE import_run_id = ? AND needs_review = 1
            """,
            (run["id"],),
        ).fetchone()
        print(f"  Still needs_review (all merchants): {noise['n']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path, help="Directory with *.pdf")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Batch SQLite path, e.g. /app/batch/data/batch_imports.sqlite3",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Substring filter on PDF file name (case-insensitive)",
    )
    args = parser.parse_args(argv)

    pdf_dir = args.pdf_dir.expanduser().resolve()
    if not pdf_dir.is_dir():
        print(f"error: not a directory: {pdf_dir}", file=sys.stderr)
        return 1

    files = sorted(pdf_dir.glob("*.pdf"))
    name_filter = args.only.strip().lower()
    if name_filter:
        files = [path for path in files if name_filter in path.name.lower()]

    print(f"Scanning {len(files)} PDF(s) in {pdf_dir}\n")
    engine = CategoryEngine()

    for path in files:
        print(f"--- {path.name} ---")
        result = _analyze_pdf(path, engine)
        status = result.get("status", "?")
        if status != "ok":
            detail = result.get("detail", "")
            print(f"  status: {status}" + (f" ({detail})" if detail else ""))
            print()
            continue

        print(f"  parser: {result['parser']}")
        print(f"  extracted chars: {result['text_chars']}")
        ctbc_lines = result["ctbc_like_lines"]
        batch_count = int(result["batch_tx_count"])
        print(f"  CTBC-shaped lines (date date ...): {ctbc_lines}")
        if ctbc_lines:
            parsed_pct = round(100.0 * batch_count / ctbc_lines, 1)
            print(
                f"  parse rate: {batch_count}/{ctbc_lines} ({parsed_pct}%)"
            )
        print(f"  batch parser rows: {batch_count}")
        print(f"  upload-fallback rows: {result['upload_fallback_count']}")
        print(f"  parse_transactions rows: {result['parse_transactions_count']}")
        print(f"  parse noise skipped (batch): {result['parse_noise_batch']}")

        raw_lines = result.get("raw_probe_lines") or []
        if raw_lines:
            print("  sample raw lines containing uber/優食/優步/eats:")
            for line in raw_lines:
                print(f"    | {line}")
        else:
            print("  sample raw lines: (none matching uber/優食/優步/eats)")

        unparsed = result.get("unparsed_ctbc_samples") or []
        if unparsed:
            print("  sample CTBC-shaped lines that did NOT parse:")
            for line in unparsed:
                print(f"    | {line}")

        for label, merchants in (
            ("batch", result.get("batch_merchants")),
            ("upload", result.get("upload_merchants")),
            ("parser", result.get("parser_merchants")),
        ):
            merchants = merchants or {}
            if merchants:
                print(f"  parsed merchants ({label}):")
                for name, count in sorted(
                    merchants.items(), key=lambda item: (-item[1], item[0])
                ):
                    print(f"    {count:3d}  {name}")

        print()

    if args.sqlite:
        _sqlite_uber_summary(args.sqlite.expanduser().resolve())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

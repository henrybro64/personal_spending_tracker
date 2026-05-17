import argparse
import json

from backend.batch_import import (
    BatchImportStore,
    StatementImporter,
    resolve_allowed_file,
)
from backend.batch_import.paths import default_sqlite_output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-import statement archives into CSV and SQLite."
    )
    parser.add_argument("--pdf-dir", dest="pdf_dir")
    parser.add_argument("--text-dir", dest="text_dir")
    parser.add_argument("--csv-out", dest="csv_out")
    parser.add_argument("--sqlite-out", dest="sqlite_out")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = BatchImportStore(
        resolve_allowed_file(args.sqlite_out, default=default_sqlite_output_path())
    )
    importer = StatementImporter(
        pdf_source_dir=args.pdf_dir,
        text_source_dir=args.text_dir,
        csv_output_path=args.csv_out,
        store=store,
    )
    result = importer.import_transactions()
    print(json.dumps(result.to_payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

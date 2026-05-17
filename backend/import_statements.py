import argparse
import json

from backend.batch_import import StatementImporter


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
    importer = StatementImporter(
        pdf_source_dir=args.pdf_dir,
        text_source_dir=args.text_dir,
        csv_output_path=args.csv_out,
    )
    if args.sqlite_out:
        from backend.batch_import import BatchImportStore

        importer.store = BatchImportStore(args.sqlite_out)
    result = importer.import_transactions()
    print(json.dumps(result.to_payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

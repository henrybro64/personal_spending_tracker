# Personal Spending Tracker

Minimal full-stack app for uploading statement PDFs, parsing transactions, storing them in PostgreSQL, and reviewing the parsed results in a Next.js UI built with Microsoft Fluent UI.

## Stack

- Backend: FastAPI
- Frontend: Next.js
- Database: PostgreSQL
- PDF parsing: `pdfplumber`

## Project Layout

```text
backend/
  app/main.py        FastAPI entrypoint (Layout B)
  main.py            Compatibility wrapper for imports/runtime
  batch_import/      Offline batch importer, parser dispatch, categorization, export
  *.py               Parsing/database helpers
frontend/            Next.js app with Fluent UI
storage/             Uploaded PDF files
k8s/                 Single-node k3s manifests and deployment guide
```

## Prerequisites on macOS

Install Homebrew if you do not already have it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install the required tools:

```bash
brew install python@3.12 node postgresql@16
```

Start PostgreSQL:

```bash
brew services start postgresql@16
```

Create the local database:

```bash
createdb personal_spending_tracker
```

## 1. Start the Backend

Create and activate a virtual environment from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Set the database connection string:

```bash
export DATABASE_URL="postgresql://$(whoami)@localhost:5432/personal_spending_tracker"
```

Optional: override the upload directory if you do not want local files written into `storage/`:

```bash
export UPLOAD_DIR="$(pwd)/storage"
```

Optional batch-import directories (defaults shown):

```bash
export BATCH_DATA_DIR="$(pwd)/storage"
export BATCH_PDF_SOURCE_DIR="$(pwd)/Credit_Card_PDF"
export BATCH_TEXT_SOURCE_DIR="$(pwd)/credit_card_text"
```

Run the FastAPI app:

```bash
uvicorn app.main:app --reload --app-dir backend
```

The backend will be available at `http://localhost:8000`.

Useful endpoints:

- `GET /health`
- `GET /summary`
- `GET /uploads/recent`
- `POST /upload`
- `POST /batch-imports/run`
- `POST /batch-imports/merge`
- `GET /batch-imports/latest`
- `GET /batch-imports/transactions`
- `PATCH /batch-imports/transactions/{id}`

## 2. Start the Frontend

Open a second terminal window, then from the repository root:

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:3000`.

By default the frontend proxies same-origin `/api/*` requests to `http://localhost:8000`.
If your backend is running somewhere else, set one of these before `npm run dev`:

```bash
export API_PROXY_TARGET="http://localhost:8000"
export NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"
```

## Automated Checks

Run the backend tests from the repository root:

```bash
python3 -m pytest
```

Run the frontend verification check:

```bash
cd frontend
npm run build
```

Run the Playwright smoke tests locally:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

Current automated coverage is intentionally small:

- backend `pytest` covers parser fixtures and lightweight API validation paths
- frontend CI runs a production build and Playwright smoke tests
- Playwright covers dashboard, data-entry, and batch-review flows with mocked `/api/*` responses
- Playwright does not require the backend or PostgreSQL

The backend CORS policy allows only `http://localhost:3000` (local Next.js dev). Same-origin `/api` proxying and k8s ingress do not rely on CORS.

## 3. Use the App

1. Open `http://localhost:3000`.
2. Choose a PDF file or drag one onto the upload panel.
3. Click **Upload Statement**.
4. The app will:
   - save the PDF into the configured upload directory (`storage/` by default)
   - extract text from the PDF
   - parse transactions
   - insert those transactions into PostgreSQL
   - refresh the dashboard cards, merchant insights, and recent statement history
   - display the parsed transactions in the frontend

You can also check the summary endpoint directly:

```bash
curl http://localhost:8000/summary
```

## 4. Batch Statement Import

The repo now includes a separate offline-first batch importer for archived
statements. It walks these directories relative to the repo root:

- `Credit_Card_PDF/*.pdf`
- `credit_card_text/*.txt`

Supported parser families:

- AMEX
- Chase
- CTBC daily statements
- CTBC card statements
- DBS statements

Run the importer from the command line:

```bash
python -m backend.import_statements
```

Optional paths:

```bash
python -m backend.import_statements \
  --pdf-dir ./Credit_Card_PDF \
  --text-dir ./credit_card_text \
  --csv-out ./storage/batch_transactions.csv \
  --sqlite-out ./storage/batch_imports.sqlite3
```

The batch workflow:

- dispatches to bank-specific parsers by filename and statement metadata
- normalizes every row into a single transaction schema
- applies deterministic merchant rules only
- marks unmatched merchants with `needs_review = true`
- exports the latest run to both SQLite and CSV

Use the frontend batch review page at `http://localhost:3000/batch-review` to:

- run the batch import from the app
- inspect flagged transactions
- correct categories
- save learned merchant rules for future imports
- sync reviewed, non-excluded transactions into PostgreSQL for the main dashboard

Batch-import API paths must stay within `BATCH_DATA_DIR`, `BATCH_PDF_SOURCE_DIR`, and `BATCH_TEXT_SOURCE_DIR`.
Sync is idempotent per batch row via a `postgres_synced` flag in the batch SQLite database.

## API Response Examples

`POST /upload`

```json
{
  "file_path": "/absolute/path/to/storage/abc123.pdf",
  "original_file_name": "April Statement.pdf",
  "transactions": [
    {
      "date": "2019-04-19",
      "merchant": "STARBUCKS",
      "amount": 5.75,
      "category": "Dining"
    }
  ]
}
```

`GET /summary`

```json
{
  "total_spending": 123.45,
  "number_of_transactions": 7,
  "average_transaction_amount": 17.64,
  "number_of_statements": 2
}
```

`GET /uploads/recent`

```json
{
  "uploads": [
    {
      "file_name": "abc123.pdf",
      "display_name": "April Statement.pdf",
      "transaction_count": 7,
      "total_amount": 123.45,
      "uploaded_at": "2026-04-21T09:30:00+00:00"
    }
  ]
}
```

## Troubleshooting

If `createdb` fails, make sure PostgreSQL is running:

```bash
brew services list
```

If `pip install requirements.txt` fails with `No matching distribution found for requirements.txt`, use the requirements-file flag:

```bash
pip install -r requirements.txt
```

If the frontend shows a network error:

- confirm the backend is running on `http://localhost:8000`
- confirm `API_PROXY_TARGET` or `NEXT_PUBLIC_API_BASE_URL` is set correctly before `npm run dev`

If the backend fails at startup with `DATABASE_URL environment variable is required`, export `DATABASE_URL` in the same shell where you run `uvicorn`.

## Kubernetes

For the single-node k3s deployment path, see [k8s/README-k3s.md](k8s/README-k3s.md).

## Stop Services

Stop the backend and frontend with `Ctrl+C`.

If you want to stop PostgreSQL afterward:

```bash
brew services stop postgresql@16
```

# Personal Spending Tracker

Minimal full-stack app for authenticated users to upload statement PDFs, parse transactions, store them in PostgreSQL, and review the results in a Next.js UI built with Microsoft Fluent UI.

## Stack

- Backend: FastAPI
- Frontend: Next.js
- Database: PostgreSQL
- PDF parsing: `pdfplumber`

## API concurrency

The API runs as one Uvicorn process. `/health` stays on the event loop and does not query Postgres. `/ready` applies pending migrations if startup could not reach Postgres, then runs `SELECT 1`. A database outage makes the process NotReady without restarting it, including when Postgres is down at boot. Database calls and import orchestration run in a worker thread pool: synchronous FastAPI endpoints are scheduled there, and handlers that also await network I/O (`/upload`, `/ai/insights`, `/batch-imports/run`) move the blocking section with `asyncio.to_thread`. PDF text extraction and statement parsing run in a child process, so that CPU work does not hold the API process GIL. One PDF is processed at a time, in arrival order. A PDF that takes longer than `PDF_PARSE_TIMEOUT_SECONDS` (default `120`) is stopped: an upload returns 504, and batch import skips the file with reason `timeout`. If the child process crashes, that upload returns 422 (batch import skips the file with `processing_error`), and the next PDF gets a fresh child process. A long import does not freeze unrelated requests such as `/health`, `/transactions`, or `/dashboard/view`.

The dashboard keeps one refresh path. Filter changes share a request generation, so an older response cannot overwrite newer totals. Search waits until typing pauses for 300 ms before it requests `/dashboard/view`; clearing the search or changing another slicer refreshes at once. Recent uploads load separately, at sign-in and after data changes, not on every filter change.

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

## Operations

- Database migrations: `python -m backend.migrate` or
  [scripts/migrate-db.sh](scripts/migrate-db.sh)
- Backup/restore: [docs/backup-restore.md](docs/backup-restore.md)
- Retention/privacy: [docs/data-lifecycle.md](docs/data-lifecycle.md)
- Currency behavior: [docs/currency.md](docs/currency.md)

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

Set the encryption secret used for stored AI provider keys:

```bash
export LLM_SETTINGS_ENCRYPTION_KEY="change-me-before-production"
```

Optional: override the upload directory if you do not want local files written into `storage/`:

```bash
export UPLOAD_DIR="$(pwd)/storage"
```

Each signed-in user gets a separate subdirectory under `UPLOAD_DIR` (for example `storage/user-1/`).

Optional batch-import directories (defaults shown):

```bash
export BATCH_DATA_DIR="$(pwd)/storage"
export BATCH_PDF_SOURCE_DIR="$(pwd)/Credit_Card_PDF"
export BATCH_TEXT_SOURCE_DIR="$(pwd)/credit_card_text"
```

Batch import reads each signed-in user's own subfolder, for example `Credit_Card_PDF/user-1/` and `credit_card_text/user-1/`. The variables above only choose the parent folder. Files placed directly in the parent folder are ignored.

**Upgrading:** earlier releases read the root of `BATCH_PDF_SOURCE_DIR` and `BATCH_TEXT_SOURCE_DIR` for every user when those variables were set. If you set them, move existing statements into the owner's `user-<id>` folder (normally `user-1`). Without the variables, statements were already per user and nothing needs to move.

```bash
for dir in "${BATCH_PDF_SOURCE_DIR:?}" "${BATCH_TEXT_SOURCE_DIR:?}"; do
  mkdir -p "$dir/user-1"
  find "$dir" -maxdepth 1 -type f -exec mv {} "$dir/user-1/" \;
done
```

Run the FastAPI app:

```bash
uvicorn app.main:app --reload --app-dir backend
```

The backend will be available at `http://localhost:8000`.

Useful endpoints:

- `GET /health`
- `GET /ready`
- `GET /diagnostics`
- `GET /auth/session`
- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /ai/settings`
- `PUT /ai/settings/{provider}`
- `DELETE /ai/settings/{provider}`
- `POST /ai/insights`
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

Same-origin `/api/*` requests are proxied to the backend by `frontend/app/api/[...path]/route.ts`
(long timeouts for batch import). Before `npm run dev`, point the server proxy at your API:

```bash
export API_PROXY_TARGET="http://localhost:8000"
```

Keep `NEXT_PUBLIC_API_BASE_URL` as `/api` (default) so the browser calls the Next.js app, not the backend directly.

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

## 3. Sign In and Use the App

1. Open `http://localhost:3000`.
2. Create an account or sign in with an existing email/password.
3. If this repo already contains legacy single-user transaction data with no `user_id`, the first account created will automatically claim those rows and their backfilled upload records.
4. Choose a PDF file or drag one onto the upload panel.
5. Click **Upload Statement**.
6. The app will:
   - save the PDF into the configured upload directory (`storage/` by default)
   - extract text from the PDF
   - parse transactions
   - insert those transactions into PostgreSQL
   - refresh the dashboard cards, merchant insights, and recent statement history
   - display the parsed transactions in the frontend

The frontend now also includes:

- `/settings` for storing your OpenAI or Perplexity API key and selecting the active provider
- `/ai-insights` for requesting LLM-generated observations over the past month, 3 months, 6 months, or year

All transaction, dashboard, upload, and batch-review endpoints now require authenticated session context. You can still check the health endpoint directly:

```bash
curl http://localhost:8000/health
```

To inspect an authenticated session from the command line, sign in through the UI first or post credentials to `/auth/login` and reuse the returned session cookie.

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

The batch-import API does not accept file or directory paths. It always uses the signed-in user's own files: `user-<id>/` under the source folders, and `batch_imports_user_<id>.sqlite3` / `batch_transactions_user_<id>.csv` under `BATCH_DATA_DIR`. Only the command-line importer (`python -m backend.import_statements`) takes paths, and those must stay within `BATCH_DATA_DIR`, `BATCH_PDF_SOURCE_DIR`, and `BATCH_TEXT_SOURCE_DIR`.
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

`number_of_statements` is an all-time count of this user's statement uploads,
including uploads that parsed zero transactions. `unconverted_transaction_count` is how many in-range spending rows were left
out of `total_spending` because they are not USD or TWD and have no exchange
rate into TWD. `unconverted_currencies` lists those original currencies.
The dashboard `limit` shortens the returned row samples and does not change
these totals.

```json
{
  "total_spending": 123.45,
  "number_of_transactions": 7,
  "average_transaction_amount": 17.64,
  "number_of_statements": 2,
  "unconverted_transaction_count": 0,
  "unconverted_currencies": [],
  "statement_count_scope": "all_time"
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

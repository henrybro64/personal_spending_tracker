import sys
from hashlib import sha256
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import copyfileobj
from typing import TypedDict
from uuid import uuid4

# Repo root on sys.path so `from backend.*` works when running
# `uvicorn main:app` from backend/.
_repo_root = Path(__file__).resolve().parent.parent
_repo_root_str = str(_repo_root)
if _repo_root_str not in sys.path:
    sys.path.insert(0, _repo_root_str)

from fastapi import (  # noqa: E402
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.database import (  # noqa: E402
    RecentUpload,
    StoredTransaction,
    TimeRange,
    TransactionsSummary,
    get_existing_upload_by_fingerprint,
    get_recent_uploads,
    get_transactions,
    get_transactions_summary,
    init_db,
    insert_transactions,
)
from backend.parser import Transaction, parse_transactions  # noqa: E402
from backend.pdf_utils import (  # noqa: E402
    PdfDecryptError,
    PdfPasswordProtectedError,
    extract_pdf_text,
)


STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
LOCAL_FRONTEND_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


class UploadResponse(TypedDict):
    file_path: str
    original_file_name: str
    transactions: list[Transaction]
    parse_reason: str | None


class SummaryResponse(TypedDict):
    total_spending: float
    number_of_transactions: int
    average_transaction_amount: float
    number_of_statements: int


class RecentUploadsResponse(TypedDict):
    uploads: list[RecentUpload]


class TransactionsResponse(TypedDict):
    transactions: list[StoredTransaction]


VALID_TIME_RANGES: tuple[TimeRange, ...] = (
    "week",
    "month",
    "quarter",
    "year",
    "5years",
    "all",
)


def _resolve_time_range(range_key: str) -> TimeRange:
    if range_key in VALID_TIME_RANGES:
        return range_key
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid range. Use one of: "
            + ", ".join(VALID_TIME_RANGES)
        ),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Personal Spending Tracker API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def read_root() -> dict[str, str]:
    return {"message": "Personal Spending Tracker API"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/summary")
async def summary(range: str = Query(default="all")) -> SummaryResponse:
    resolved_range = _resolve_time_range(range)
    summary_data: TransactionsSummary = get_transactions_summary(
        range_key=resolved_range
    )
    return summary_data


@app.get("/uploads/recent")
async def recent_uploads(limit: int = 5) -> RecentUploadsResponse:
    limited_value = max(1, min(limit, 12))
    return {"uploads": get_recent_uploads(limit=limited_value)}


@app.get("/transactions")
async def transactions(
    range: str = Query(default="all"),
    limit: int = Query(default=5000, ge=1, le=20000),
) -> TransactionsResponse:
    resolved_range = _resolve_time_range(range)
    return {
        "transactions": get_transactions(
            range_key=resolved_range,
            limit=limit,
        )
    }


@app.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile = File(...),
    password: str | None = Form(default=None),
) -> UploadResponse:
    original_name = file.filename or ""
    has_pdf_extension = Path(original_name).suffix.lower() == ".pdf"
    is_pdf_content = file.content_type == "application/pdf"

    if not has_pdf_extension and not is_pdf_content:
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    password_clean = password.strip() if password else None

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    saved_name = f"{uuid4().hex}.pdf"
    saved_path = STORAGE_DIR / saved_name

    try:
        with saved_path.open("wb") as buffer:
            copyfileobj(file.file, buffer)
    finally:
        await file.close()

    file_fingerprint = sha256(saved_path.read_bytes()).hexdigest()
    existing_upload = get_existing_upload_by_fingerprint(file_fingerprint)
    if existing_upload:
        raise HTTPException(
            status_code=409,
            detail=(
                "This statement was already imported as "
                f"'{existing_upload['display_name']}' on {existing_upload['uploaded_at']}."
            ),
        )

    try:
        extracted_text = extract_pdf_text(saved_path, password=password_clean)
    except PdfPasswordProtectedError as e:
        need_password = (
            "This PDF is password-protected. Enter the document password "
            "below and try again."
        )
        raise HTTPException(
            status_code=400,
            detail="Incorrect PDF password." if e.had_password else need_password,
        ) from e
    except PdfDecryptError:
        cannot_open = (
            "This PDF could not be opened. It may use unsupported "
            "encryption; try removing the password in a PDF app and "
            "export a new file."
        )
        raise HTTPException(status_code=400, detail=cannot_open) from None
    transactions = parse_transactions(extracted_text)
    display_name = original_name or saved_name
    insert_transactions(
        transactions,
        saved_name,
        file_fingerprint=file_fingerprint,
        original_file_name=display_name,
    )

    parse_reason: str | None = None
    if not transactions:
        parse_reason = (
            "empty_text" if not extracted_text.strip() else "no_matching_lines"
        )

    return {
        "file_path": f"./storage/{saved_name}",
        "original_file_name": display_name,
        "transactions": transactions,
        "parse_reason": parse_reason,
    }

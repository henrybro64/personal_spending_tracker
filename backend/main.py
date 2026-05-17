import os
import sys
from datetime import date
from hashlib import sha1, sha256
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import copyfileobj
from typing import Literal, TypedDict
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
from pydantic import BaseModel  # noqa: E402

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
    insert_manual_transaction,
    insert_transactions,
)
from backend.batch_import import (  # noqa: E402
    BatchImportRunSummary,
    BatchImportStore,
    BatchStoredTransaction,
    StatementImporter,
    default_sqlite_output_path,
    merge_run_to_postgres,
    resolve_allowed_file,
)
from backend.parser import Transaction, parse_transactions  # noqa: E402
from backend.pdf_utils import (  # noqa: E402
    PdfDecryptError,
    PdfPasswordProtectedError,
    extract_pdf_text,
)


DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
LOCAL_FRONTEND_ORIGINS = ("http://localhost:3000",)


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


class BatchImportRunExecutionResponse(TypedDict):
    run: dict[str, object]


class BatchImportLatestResponse(TypedDict):
    run: BatchImportRunSummary | None


class BatchImportTransactionsResponse(TypedDict):
    transactions: list[BatchStoredTransaction]


class BatchImportReviewUpdateResponse(TypedDict):
    transaction: BatchStoredTransaction


class BatchImportMergeResponse(TypedDict):
    run_id: int | None
    merged_count: int
    skipped_count: int


AmountFilter = Literal["all", "spend", "refunds"]
SortOption = Literal["amount_desc", "amount_asc", "merchant", "date"]
DisplayCurrency = Literal["USD", "TWD"]


class MerchantBreakdownItem(TypedDict):
    merchant: str
    total: float
    abs_total: float


class CategoryBreakdownItem(TypedDict):
    category: str
    total: float
    share: float


class DateBreakdownItem(TypedDict):
    date: str
    total: float
    abs_total: float


class DashboardMetrics(TypedDict):
    selection_net: float
    selection_spend: float
    selection_refunds: float
    selection_average: float
    upload_net: float
    largest_transaction_amount: float
    selection_coverage: float
    model_total_in_active_currency: float


class DashboardViewResponse(TypedDict):
    active_currency: DisplayCurrency
    summary: SummaryResponse
    transactions: list[StoredTransaction]
    sorted_transactions: list[StoredTransaction]
    merchant_breakdown: list[MerchantBreakdownItem]
    category_breakdown: list[CategoryBreakdownItem]
    date_breakdown: list[DateBreakdownItem]
    metrics: DashboardMetrics


class ManualTransactionRequest(BaseModel):
    date: str
    merchant: str
    amount: float
    category: str | None = None
    payment_method: str | None = None


class BatchImportRequest(BaseModel):
    pdf_source_dir: str | None = None
    text_source_dir: str | None = None
    csv_output_path: str | None = None
    sqlite_output_path: str | None = None


class BatchImportMergeRequest(BaseModel):
    run_id: int | None = None
    include_needs_review: bool = False
    include_excluded: bool = False
    sqlite_output_path: str | None = None


class BatchReviewUpdateRequest(BaseModel):
    category: str
    exclude_from_spending: bool
    save_rule: bool = True


class ManualTransactionResponse(TypedDict):
    transaction: StoredTransaction


VALID_TIME_RANGES: tuple[TimeRange, ...] = (
    "week",
    "month",
    "quarter",
    "year",
    "5years",
    "all",
)
VALID_AMOUNT_FILTERS: tuple[AmountFilter, ...] = ("all", "spend", "refunds")
VALID_SORT_OPTIONS: tuple[SortOption, ...] = (
    "amount_desc",
    "amount_asc",
    "merchant",
    "date",
)
VALID_DISPLAY_CURRENCIES: tuple[DisplayCurrency, ...] = ("USD", "TWD")
USD_TO_TWD_RATE = 32.0


def _get_upload_dir() -> Path:
    configured_upload_dir = os.getenv("UPLOAD_DIR")
    if not configured_upload_dir:
        return DEFAULT_STORAGE_DIR
    return Path(configured_upload_dir).expanduser()


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


def _resolve_amount_filter(filter_key: str) -> AmountFilter:
    if filter_key in VALID_AMOUNT_FILTERS:
        return filter_key
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid amount filter. Use one of: "
            + ", ".join(VALID_AMOUNT_FILTERS)
        ),
    )


def _resolve_sort_option(sort_key: str) -> SortOption:
    if sort_key in VALID_SORT_OPTIONS:
        return sort_key
    raise HTTPException(
        status_code=400,
        detail=("Invalid sort option. Use one of: " + ", ".join(VALID_SORT_OPTIONS)),
    )


def _resolve_display_currency(currency_code: str) -> DisplayCurrency:
    normalized_code = currency_code.upper()
    if normalized_code in VALID_DISPLAY_CURRENCIES:
        return normalized_code  # type: ignore[return-value]
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid display currency. Use one of: "
            + ", ".join(VALID_DISPLAY_CURRENCIES)
        ),
    )


def _convert_amount(
    amount: float, source_currency: str, target_currency: DisplayCurrency
) -> float:
    normalized_source = source_currency.upper()
    if normalized_source == target_currency:
        return amount
    if normalized_source == "USD" and target_currency == "TWD":
        return amount * USD_TO_TWD_RATE
    if normalized_source == "TWD" and target_currency == "USD":
        return amount / USD_TO_TWD_RATE
    return amount


def _sort_transactions(
    transactions: list[StoredTransaction], sort_option: SortOption
) -> list[StoredTransaction]:
    sorted_transactions = list(transactions)
    if sort_option == "amount_asc":
        sorted_transactions.sort(key=lambda item: item["amount"])
    elif sort_option == "merchant":
        sorted_transactions.sort(key=lambda item: item["merchant"].lower())
    elif sort_option == "date":
        sorted_transactions.sort(key=lambda item: item["date"])
    else:
        sorted_transactions.sort(key=lambda item: item["amount"], reverse=True)
    return sorted_transactions


def _get_batch_import_store(sqlite_path: str | None = None) -> BatchImportStore:
    resolved_sqlite_path = resolve_allowed_file(
        sqlite_path,
        default=default_sqlite_output_path(),
    )
    store = BatchImportStore(resolved_sqlite_path)
    store.initialize()
    return store


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        init_db()
    except RuntimeError as exc:
        if "DATABASE_URL environment variable is required" not in str(exc):
            raise
    yield


app = FastAPI(title="Personal Spending Tracker API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(LOCAL_FRONTEND_ORIGINS),
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


@app.get("/dashboard/view")
async def dashboard_view(
    range: str = Query(default="all"),
    search: str = Query(default=""),
    amount_filter: str = Query(default="all"),
    selected_merchant: str = Query(default=""),
    sort: str = Query(default="amount_desc"),
    currency: str = Query(default="USD"),
    limit: int = Query(default=5000, ge=1, le=20000),
) -> DashboardViewResponse:
    resolved_range = _resolve_time_range(range)
    resolved_amount_filter = _resolve_amount_filter(amount_filter)
    resolved_sort_option = _resolve_sort_option(sort)
    resolved_currency = _resolve_display_currency(currency)

    all_transactions = get_transactions(range_key=resolved_range, limit=limit)
    converted_transactions: list[StoredTransaction] = [
        {
            **transaction,
            "amount": _convert_amount(
                transaction["amount"], transaction.get("currency", "USD"), resolved_currency
            ),
            "currency": resolved_currency,
        }
        for transaction in all_transactions
    ]

    normalized_search = search.strip().lower()
    base_filtered = []
    for transaction in converted_transactions:
        matches_search = (
            not normalized_search
            or normalized_search in transaction["merchant"].lower()
            or normalized_search in transaction["category"].lower()
            or normalized_search in transaction["date"].lower()
            or normalized_search in f"{transaction['amount']:.2f}"
        )
        matches_amount_filter = (
            resolved_amount_filter == "all"
            or (
                resolved_amount_filter == "spend"
                and transaction["amount"] >= 0
            )
            or (
                resolved_amount_filter == "refunds"
                and transaction["amount"] < 0
            )
        )
        if matches_search and matches_amount_filter:
            base_filtered.append(transaction)

    normalized_selected_merchant = selected_merchant.strip()
    filtered_transactions = (
        [
            transaction
            for transaction in base_filtered
            if transaction["merchant"] == normalized_selected_merchant
        ]
        if normalized_selected_merchant
        else base_filtered
    )
    sorted_transactions = _sort_transactions(filtered_transactions, resolved_sort_option)

    merchant_totals: dict[str, float] = {}
    for transaction in base_filtered:
        merchant = transaction["merchant"]
        merchant_totals[merchant] = merchant_totals.get(merchant, 0.0) + transaction["amount"]
    merchant_breakdown = sorted(
        [
            {
                "merchant": merchant,
                "total": total,
                "abs_total": abs(total),
            }
            for merchant, total in merchant_totals.items()
        ],
        key=lambda item: item["abs_total"],
        reverse=True,
    )[:7]

    category_totals: dict[str, float] = {}
    for transaction in filtered_transactions:
        if transaction["amount"] <= 0:
            continue
        category = transaction["category"]
        category_totals[category] = category_totals.get(category, 0.0) + transaction["amount"]
    category_total_spend = sum(category_totals.values())
    category_breakdown = [
        {
            "category": category,
            "total": total,
            "share": (total / category_total_spend) if category_total_spend > 0 else 0.0,
        }
        for category, total in sorted(
            category_totals.items(), key=lambda item: item[1], reverse=True
        )
    ]

    date_totals: dict[str, float] = {}
    for transaction in filtered_transactions:
        date_key = transaction["date"]
        date_totals[date_key] = date_totals.get(date_key, 0.0) + transaction["amount"]
    date_breakdown = [
        {
            "date": date_key,
            "total": total,
            "abs_total": abs(total),
        }
        for date_key, total in sorted(date_totals.items(), key=lambda item: item[0])[-10:]
    ]

    selection_net = sum(transaction["amount"] for transaction in filtered_transactions)
    selection_spend = sum(
        transaction["amount"]
        for transaction in filtered_transactions
        if transaction["amount"] > 0
    )
    selection_refunds = sum(
        abs(transaction["amount"])
        for transaction in filtered_transactions
        if transaction["amount"] < 0
    )
    selection_average = (
        selection_net / len(filtered_transactions) if filtered_transactions else 0.0
    )
    upload_net = sum(transaction["amount"] for transaction in converted_transactions)
    largest_transaction_amount = (
        max(abs(transaction["amount"]) for transaction in filtered_transactions)
        if filtered_transactions
        else 0.0
    )
    selection_coverage = (
        (len(filtered_transactions) / len(converted_transactions))
        if converted_transactions
        else 0.0
    )
    model_total_in_active_currency = sum(
        transaction["amount"] for transaction in converted_transactions
    )

    summary_data: TransactionsSummary = get_transactions_summary(range_key=resolved_range)
    summary_converted: SummaryResponse = {
        "total_spending": _convert_amount(
            summary_data["total_spending"], "USD", resolved_currency
        ),
        "number_of_transactions": summary_data["number_of_transactions"],
        "average_transaction_amount": _convert_amount(
            summary_data["average_transaction_amount"], "USD", resolved_currency
        ),
        "number_of_statements": summary_data["number_of_statements"],
    }

    return {
        "active_currency": resolved_currency,
        "summary": summary_converted,
        "transactions": converted_transactions,
        "sorted_transactions": sorted_transactions,
        "merchant_breakdown": merchant_breakdown,
        "category_breakdown": category_breakdown,
        "date_breakdown": date_breakdown,
        "metrics": {
            "selection_net": selection_net,
            "selection_spend": selection_spend,
            "selection_refunds": selection_refunds,
            "selection_average": selection_average,
            "upload_net": upload_net,
            "largest_transaction_amount": largest_transaction_amount,
            "selection_coverage": selection_coverage,
            "model_total_in_active_currency": model_total_in_active_currency,
        },
    }


@app.post("/transactions/manual", status_code=status.HTTP_201_CREATED)
async def create_manual_transaction(
    payload: ManualTransactionRequest,
) -> ManualTransactionResponse:
    transaction_date = payload.date.strip()
    merchant = payload.merchant.strip()
    if not merchant:
        raise HTTPException(status_code=400, detail="Merchant is required.")

    if payload.amount == 0:
        raise HTTPException(status_code=400, detail="Amount must be non-zero.")

    try:
        date.fromisoformat(transaction_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD.") from None

    created = insert_manual_transaction(
        transaction_date=transaction_date,
        merchant=merchant,
        amount=payload.amount,
        category=payload.category,
        payment_method=payload.payment_method,
    )
    return {"transaction": created}


@app.post("/batch-imports/run", status_code=status.HTTP_201_CREATED)
async def run_batch_import(
    payload: BatchImportRequest | None = None,
) -> BatchImportRunExecutionResponse:
    request_payload = payload or BatchImportRequest()
    store = _get_batch_import_store(request_payload.sqlite_output_path)
    try:
        importer = StatementImporter(
            pdf_source_dir=request_payload.pdf_source_dir,
            text_source_dir=request_payload.text_source_dir,
            csv_output_path=request_payload.csv_output_path,
            store=store,
        )
        result = importer.import_transactions()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"run": result.to_payload()}


@app.post("/batch-imports/merge", status_code=status.HTTP_200_OK)
async def merge_batch_import(
    payload: BatchImportMergeRequest | None = None,
) -> BatchImportMergeResponse:
    request_payload = payload or BatchImportMergeRequest()
    store = _get_batch_import_store(request_payload.sqlite_output_path)

    try:
        result = merge_run_to_postgres(
            store,
            run_id=request_payload.run_id,
            include_needs_review=request_payload.include_needs_review,
            include_excluded=request_payload.include_excluded,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "run_id": result["run_id"],
        "merged_count": int(result["merged_count"]),
        "skipped_count": int(result["skipped_count"]),
    }


@app.get("/batch-imports/latest")
async def batch_import_latest(
    sqlite_path: str | None = Query(default=None),
) -> BatchImportLatestResponse:
    store = _get_batch_import_store(sqlite_path)
    return {"run": store.get_latest_run()}


@app.get("/batch-imports/transactions")
async def batch_import_transactions(
    needs_review_only: bool = Query(default=False),
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=500, ge=1, le=5000),
    sqlite_path: str | None = Query(default=None),
) -> BatchImportTransactionsResponse:
    store = _get_batch_import_store(sqlite_path)
    return {
        "transactions": store.list_transactions(
            needs_review_only=needs_review_only,
            run_id=run_id,
            limit=limit,
        )
    }


@app.patch("/batch-imports/transactions/{transaction_id}")
async def update_batch_import_transaction(
    transaction_id: int,
    payload: BatchReviewUpdateRequest,
    sqlite_path: str | None = Query(default=None),
) -> BatchImportReviewUpdateResponse:
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required.")

    store = _get_batch_import_store(sqlite_path)
    try:
        updated_transaction = store.update_transaction_review(
            transaction_id,
            category=category,
            exclude_from_spending=payload.exclude_from_spending,
            save_rule=payload.save_rule,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Batch-import transaction {transaction_id} was not found.",
        ) from None

    return {"transaction": updated_transaction}


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

    upload_dir = _get_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"{uuid4().hex}.pdf"
    saved_path = upload_dir / saved_name

    try:
        with saved_path.open("wb") as buffer:
            copyfileobj(file.file, buffer)
    finally:
        await file.close()

    file_bytes = saved_path.read_bytes()
    sha1_fingerprint = sha1(file_bytes).hexdigest()
    sha256_fingerprint = sha256(file_bytes).hexdigest()
    existing_upload = get_existing_upload_by_fingerprint(
        [sha1_fingerprint, sha256_fingerprint]
    )
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
        file_fingerprint=sha1_fingerprint,
        original_file_name=display_name,
    )

    parse_reason: str | None = None
    if not transactions:
        parse_reason = (
            "empty_text" if not extracted_text.strip() else "no_matching_lines"
        )

    return {
        "file_path": str(saved_path),
        "original_file_name": display_name,
        "transactions": transactions,
        "parse_reason": parse_reason,
    }

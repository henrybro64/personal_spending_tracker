import asyncio
import logging
import os
import sys
from datetime import date
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal, NoReturn, TypedDict, cast
from uuid import uuid4

# Repo root on sys.path so `from backend.*` works when running
# `uvicorn main:app` from backend/.
_repo_root = Path(__file__).resolve().parent.parent
_repo_root_str = str(_repo_root)
if _repo_root_str not in sys.path:
    sys.path.insert(0, _repo_root_str)

from fastapi import (  # noqa: E402
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.auth import (  # noqa: E402
    attach_session_cookie,
    clear_session_cookie,
    display_name_from_username,
    generate_session_token,
    AuditRequestContext,
    get_audit_request_context,
    get_optional_current_user,
    hash_password,
    hash_session_token,
    require_current_user,
    session_expiry,
    validate_username,
    validate_password,
    verify_password,
)
from backend.ai_insights import (  # noqa: E402
    AiProvider,
    InsightLanguage,
    SUPPORTED_AI_PROVIDERS,
    SUPPORTED_INSIGHT_RANGES,
    build_spending_insight_snapshot,
    default_model_for_provider,
    generate_spending_insights,
)
from backend.transaction_exclusions import should_exclude_from_spending
from backend.database import (  # noqa: E402
    ExistingUpload,
    UserAiProviderSettingRecord,
    RecentUpload,
    StoredTransaction,
    TimeRange,
    TransactionsSummary,
    UserRecord,
    DuplicateStatementUploadError,
    DuplicateUsernameError,
    create_session,
    create_statement_upload,
    delete_user_ai_provider_setting,
    get_existing_upload_by_fingerprint,
    get_recent_uploads,
    get_transactions,
    get_transactions_summary,
    get_user_ai_provider_secret,
    get_user_auth_by_email,
    init_db,
    insert_manual_transaction,
    insert_transactions,
    list_user_ai_provider_settings,
    record_audit_event,
    register_new_user,
    revoke_session,
    upsert_user_ai_provider_setting,
    delete_statement_upload_for_user,
    delete_transaction,
    delete_transaction_by_match,
    get_statement_upload_for_user,
    update_transaction as update_stored_transaction,
    update_transaction_category,
    update_user_last_login,
)
from backend.bulk_import import (  # noqa: E402
    BulkImportParseError,
    parse_import_content,
)
from backend.batch_import.paths import (  # noqa: E402
    default_pdf_source_dir,
    default_text_source_dir,
    resolve_allowed_directory,
)
from backend.batch_import import (  # noqa: E402
    BatchImportRunSummary,
    BatchImportStore,
    BatchStoredTransaction,
    CategoryEngine,
    StatementImporter,
    default_sqlite_output_path,
    merge_run_to_postgres,
    resolve_allowed_file,
)
from backend.currency import (  # noqa: E402
    DEFAULT_BASE_CURRENCY,
    DisplayCurrency,
    SupportedCurrency,
    SUPPORTED_CURRENCY_CODES,
    convert_amount,
    derive_base_money_fields,
    exchange_rate_metadata,
    normalize_supported_currency_code,
)
from backend.parser import Transaction  # noqa: E402
from backend.pdf_utils import (  # noqa: E402
    PdfDecryptError,
    PdfPasswordProtectedError,
    read_statement_pdf,
)
from backend.process_pool import shutdown_process_pool  # noqa: E402


DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
LOCAL_FRONTEND_ORIGINS = ("http://localhost:3000",)
logger = logging.getLogger(__name__)


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


class NeedsReviewMerchantSummary(TypedDict):
    merchant_raw: str
    transaction_count: int
    sample_category: str
    sample_excluded: bool
    suggested_category: str
    can_auto_apply: bool


class BatchImportNeedsReviewMerchantsResponse(TypedDict):
    merchants: list[NeedsReviewMerchantSummary]


class BatchImportReviewUpdateResponse(TypedDict):
    transaction: BatchStoredTransaction


class BatchImportBulkReviewUpdateResponse(TypedDict):
    updated_count: int


class BatchImportDigitNoisePendingResponse(TypedDict):
    pending_count: int


class BatchImportDismissDigitNoiseResponse(TypedDict):
    updated_count: int


class BatchImportAutoCategorizeResponse(TypedDict):
    updated_count: int
    needs_review_count: int


class BatchImportMergeResponse(TypedDict):
    run_id: int | None
    merged_count: int
    skipped_count: int


AmountFilter = Literal["all", "spend", "refunds"]
SortOption = Literal["amount_desc", "amount_asc", "merchant", "date"]


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


class DashboardTransaction(StoredTransaction):
    original_amount: float
    original_currency: str
    display_amount: float
    display_currency: DisplayCurrency
    is_converted: bool


class ExchangeRateMetadata(TypedDict):
    usd_twd_rate: float
    rate_source: str


class DashboardViewResponse(TypedDict):
    active_currency: DisplayCurrency
    exchange_rate: ExchangeRateMetadata
    summary: SummaryResponse
    transactions: list[DashboardTransaction]
    sorted_transactions: list[DashboardTransaction]
    merchant_breakdown: list[MerchantBreakdownItem]
    category_breakdown: list[CategoryBreakdownItem]
    date_breakdown: list[DateBreakdownItem]
    metrics: DashboardMetrics


class ManualTransactionRequest(BaseModel):
    date: str
    merchant: str
    amount: float | None = None
    currency: str | None = None
    original_amount: float | None = None
    original_currency: str | None = None
    base_currency: str = DEFAULT_BASE_CURRENCY
    exchange_rate: float | None = None
    exchange_rate_date: str | None = None
    category: str | None = None
    payment_method: str | None = None


class BatchImportRequest(BaseModel):
    pdf_source_dir: str | None = None
    text_source_dir: str | None = None
    csv_output_path: str | None = None
    sqlite_output_path: str | None = None


class BatchImportMergeRequest(BaseModel):
    run_id: int | None = None
    include_needs_review: bool = True
    include_excluded: bool = False
    sqlite_output_path: str | None = None


class BatchReviewUpdateRequest(BaseModel):
    category: str
    exclude_from_spending: bool
    save_rule: bool = True
    merchant_raw: str | None = None


class BatchReviewBulkUpdateRequest(BaseModel):
    run_id: int
    merchant_raw: str
    category: str
    exclude_from_spending: bool
    save_rule: bool = True


class BatchReviewDismissDigitNoiseRequest(BaseModel):
    run_id: int


class BulkContentImportRequest(BaseModel):
    content: str
    format: Literal["auto", "json", "text"] = "auto"
    bank: str = "Manual"
    currency: str = "TWD"
    source_name: str = "import"
    destination: Literal["batch", "dashboard"] = "batch"
    sqlite_output_path: str | None = None


class BulkContentImportResponse(TypedDict):
    destination: str
    imported_count: int
    run: dict[str, object] | None


class ManualTransactionResponse(TypedDict):
    transaction: StoredTransaction


class TransactionUpdateRequest(BaseModel):
    category: str | None = None
    merchant: str | None = None


class AuthRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class SessionResponse(TypedDict):
    authenticated: bool
    user: UserRecord | None
    claimed_legacy_transactions: int
    claimed_legacy_uploads: int


class AiProviderSettingsView(TypedDict):
    provider: AiProvider
    display_name: str
    configured: bool
    selected: bool
    masked_api_key: str | None
    model: str
    created_at: str | None
    updated_at: str | None


class AiSettingsResponse(TypedDict):
    providers: list[AiProviderSettingsView]
    selected_provider: AiProvider | None
    privacy_notice: str


class AiInsightResponse(TypedDict):
    provider: AiProvider
    model: str
    time_range: TimeRange
    generated_at: str
    insight_text: str
    citations: list[str]
    data_shared: list[str]
    snapshot: dict[str, object]


class AiProviderSettingsRequest(BaseModel):
    api_key: str | None = None
    model: str | None = None
    selected: bool = True


class AiInsightsRequest(BaseModel):
    provider: AiProvider | None = None
    range: Literal["month", "quarter", "6months", "year"] = "quarter"
    language: Literal["en", "zh-Hant"] = "en"


VALID_TIME_RANGES: tuple[TimeRange, ...] = (
    "week",
    "month",
    "quarter",
    "6months",
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
AI_PROVIDER_DISPLAY_NAMES: dict[AiProvider, str] = {
    "openai": "OpenAI",
    "perplexity": "Perplexity",
}
AI_PRIVACY_NOTICE = (
    "When AI insights run, the app sends an aggregated snapshot of the selected "
    "transactions to the configured external provider. Raw API keys are never "
    "returned by this API, and file names, payment methods, and session details "
    "are excluded from the model payload."
)


def _get_upload_dir(user_id: int) -> Path:
    configured_upload_dir = os.getenv("UPLOAD_DIR")
    base_dir = (
        Path(configured_upload_dir).expanduser()
        if configured_upload_dir
        else DEFAULT_STORAGE_DIR
    )
    return base_dir / f"user-{user_id}"


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
    requested_code = currency_code.strip().upper()
    if requested_code in VALID_DISPLAY_CURRENCIES:
        return cast(DisplayCurrency, requested_code)
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid display currency. Use one of: "
            + ", ".join(VALID_DISPLAY_CURRENCIES)
        ),
    )


def _resolve_supported_currency(
    currency_code: str | None,
    *,
    field_name: str,
    default: str | None = None,
) -> str:
    try:
        return normalize_supported_currency_code(
            currency_code,
            default=cast(SupportedCurrency, default) if default is not None else None,
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid {field_name}. Use one of: "
            + ", ".join(SUPPORTED_CURRENCY_CODES)
        ) from exc


def _validate_optional_iso_date(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    try:
        date.fromisoformat(normalized_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD.") from exc
    return normalized_value


def _resolve_ai_provider(provider_name: str) -> AiProvider:
    normalized_provider = provider_name.strip().lower()
    if normalized_provider in SUPPORTED_AI_PROVIDERS:
        return cast(AiProvider, normalized_provider)
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid AI provider. Use one of: "
            + ", ".join(SUPPORTED_AI_PROVIDERS)
        ),
    )


def _resolve_ai_insight_range(range_key: str) -> TimeRange:
    if range_key in SUPPORTED_INSIGHT_RANGES:
        return cast(TimeRange, range_key)
    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid AI insight range. Use one of: "
            + ", ".join(SUPPORTED_INSIGHT_RANGES)
        ),
    )


def _build_ai_settings_response(
    stored_settings: list[UserAiProviderSettingRecord],
) -> AiSettingsResponse:
    settings_by_provider = {
        setting["provider"]: setting for setting in stored_settings
    }
    providers: list[AiProviderSettingsView] = []
    selected_provider: AiProvider | None = None

    for provider in SUPPORTED_AI_PROVIDERS:
        existing = settings_by_provider.get(provider)
        if existing and existing["is_selected"]:
            selected_provider = provider
        providers.append(
            {
                "provider": provider,
                "display_name": AI_PROVIDER_DISPLAY_NAMES[provider],
                "configured": existing is not None,
                "selected": bool(existing["is_selected"]) if existing else False,
                "masked_api_key": (
                    f"••••{existing['api_key_last4']}"
                    if existing
                    else None
                ),
                "model": (
                    existing["model"]
                    if existing and existing["model"]
                    else default_model_for_provider(provider)
                ),
                "created_at": existing["created_at"] if existing else None,
                "updated_at": existing["updated_at"] if existing else None,
            }
        )

    return {
        "providers": providers,
        "selected_provider": selected_provider,
        "privacy_notice": AI_PRIVACY_NOTICE,
    }


def _default_batch_pdf_source_dir_for_user(user_id: int) -> Path:
    base_dir = default_pdf_source_dir()
    if os.getenv("BATCH_PDF_SOURCE_DIR"):
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    user_dir = base_dir / f"user-{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _default_batch_text_source_dir_for_user(user_id: int) -> Path:
    base_dir = default_text_source_dir()
    if os.getenv("BATCH_TEXT_SOURCE_DIR"):
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    user_dir = base_dir / f"user-{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _sort_transactions(
    transactions: list[DashboardTransaction], sort_option: SortOption
) -> list[DashboardTransaction]:
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


def _dashboard_transaction_for_currency(
    transaction: StoredTransaction,
    display_currency: DisplayCurrency,
) -> DashboardTransaction | None:
    original_amount = float(transaction.get("original_amount", transaction["amount"]))
    original_currency = transaction.get(
        "original_currency",
        transaction.get("currency", DEFAULT_BASE_CURRENCY),
    ).upper()
    base_amount = transaction.get("base_amount")
    base_currency = transaction.get("base_currency", DEFAULT_BASE_CURRENCY).upper()

    if base_amount is not None:
        display_amount = convert_amount(
            float(base_amount),
            base_currency,
            display_currency,
        )
    elif original_currency == display_currency:
        display_amount = original_amount
    else:
        return None

    return {
        **transaction,
        "amount": display_amount,
        "currency": display_currency,
        "original_amount": original_amount,
        "original_currency": original_currency,
        "display_amount": display_amount,
        "display_currency": display_currency,
        "is_converted": original_currency != display_currency,
    }


def _transaction_matches_dashboard_filters(
    transaction: DashboardTransaction,
    *,
    normalized_search: str,
    amount_filter: AmountFilter,
) -> bool:
    matches_search = (
        not normalized_search
        or normalized_search in transaction["merchant"].lower()
        or normalized_search in transaction["category"].lower()
        or normalized_search in transaction["date"].lower()
        or normalized_search in f"{transaction['amount']:.2f}"
    )
    if amount_filter == "all":
        matches_amount_filter = True
    elif amount_filter == "spend":
        matches_amount_filter = transaction["amount"] >= 0
    else:
        matches_amount_filter = transaction["amount"] < 0
    return matches_search and matches_amount_filter


def _filter_dashboard_transactions(
    transactions: list[DashboardTransaction],
    *,
    search: str,
    amount_filter: AmountFilter,
    selected_merchant: str,
) -> tuple[list[DashboardTransaction], list[DashboardTransaction]]:
    normalized_search = search.strip().lower()
    base_filtered = [
        transaction
        for transaction in transactions
        if _transaction_matches_dashboard_filters(
            transaction,
            normalized_search=normalized_search,
            amount_filter=amount_filter,
        )
    ]
    normalized_selected_merchant = selected_merchant.strip()
    if not normalized_selected_merchant:
        return base_filtered, base_filtered
    filtered_transactions = [
        transaction
        for transaction in base_filtered
        if transaction["merchant"] == normalized_selected_merchant
    ]
    return base_filtered, filtered_transactions


def _build_dashboard_breakdowns(
    base_filtered: list[DashboardTransaction],
    filtered_transactions: list[DashboardTransaction],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
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
    return merchant_breakdown, category_breakdown, date_breakdown


def _build_dashboard_metrics(
    filtered_transactions: list[DashboardTransaction],
    converted_transactions: list[DashboardTransaction],
) -> dict[str, float]:
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
    return {
        "selection_net": selection_net,
        "selection_spend": selection_spend,
        "selection_refunds": selection_refunds,
        "selection_average": selection_average,
        "upload_net": upload_net,
        "largest_transaction_amount": largest_transaction_amount,
        "selection_coverage": selection_coverage,
        "model_total_in_active_currency": model_total_in_active_currency,
    }


def _parse_manual_transaction_fields(
    payload: ManualTransactionRequest,
) -> dict[str, object]:
    transaction_date = payload.date.strip()
    merchant = payload.merchant.strip()
    original_amount = (
        payload.original_amount
        if payload.original_amount is not None
        else payload.amount
    )
    if not merchant:
        raise ValueError("Merchant is required.")
    if original_amount is None or original_amount <= 0:
        raise ValueError("Amount must be positive.")
    try:
        date.fromisoformat(transaction_date)
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD.") from exc

    original_currency = _resolve_supported_currency(
        payload.original_currency or payload.currency,
        field_name="original currency",
        default=DEFAULT_BASE_CURRENCY,
    )
    base_currency = _resolve_supported_currency(
        payload.base_currency,
        field_name="base currency",
        default=DEFAULT_BASE_CURRENCY,
    )
    exchange_rate_date = _validate_optional_iso_date(
        payload.exchange_rate_date,
        field_name="Exchange rate date",
    )

    if payload.exchange_rate is not None and payload.exchange_rate <= 0:
        raise ValueError("Exchange rate must be greater than zero when provided.")
    if original_currency == base_currency or payload.exchange_rate is not None:
        exchange_rate_date = exchange_rate_date or transaction_date

    derive_base_money_fields(
        original_amount=original_amount,
        original_currency=original_currency,
        base_currency=base_currency,
        exchange_rate=payload.exchange_rate,
    )

    return {
        "transaction_date": transaction_date,
        "merchant": merchant,
        "original_amount": original_amount,
        "original_currency": original_currency,
        "base_currency": base_currency,
        "exchange_rate": payload.exchange_rate,
        "exchange_rate_date": exchange_rate_date,
        "category": payload.category,
        "payment_method": payload.payment_method,
    }


def _default_batch_sqlite_path_for_user(user_id: int) -> Path:
    return default_sqlite_output_path().with_name(f"batch_imports_user_{user_id}.sqlite3")


def _default_batch_csv_path_for_user(user_id: int) -> Path:
    return default_sqlite_output_path().with_name(f"batch_transactions_user_{user_id}.csv")


def _get_batch_import_store(
    user_id: int,
    sqlite_path: str | None = None,
) -> BatchImportStore:
    resolved_sqlite_path = resolve_allowed_file(
        sqlite_path,
        default=_default_batch_sqlite_path_for_user(user_id),
    )
    store = BatchImportStore(resolved_sqlite_path)
    store.initialize()
    return store


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        # Schema setup talks to Postgres. Keep it off the event loop.
        await asyncio.to_thread(init_db)
    except RuntimeError as exc:
        if "DATABASE_URL environment variable is required" not in str(exc):
            raise
    try:
        yield
    finally:
        shutdown_process_pool()


# Concurrency model: one Uvicorn worker. `/health` stays on the event loop.
# Database calls and import orchestration use the worker thread pool: plain
# `def` endpoints are scheduled there, and handlers that also await network I/O
# move the blocking section with `asyncio.to_thread`. PDF extraction and
# statement parsing run in a child process so that CPU work does not hold this
# process's GIL. A long import must not freeze unrelated requests.
app = FastAPI(title="Personal Spending Tracker API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(LOCAL_FRONTEND_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def read_root() -> dict[str, str]:
    return {"message": "Personal Spending Tracker API"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    # Stay on the event loop so a saturated worker pool cannot hide a live process.
    return {"status": "ok"}


@app.get("/auth/session")
def auth_session(
    current_user: UserRecord | None = Depends(get_optional_current_user),
) -> SessionResponse:
    return {
        "authenticated": current_user is not None,
        "user": current_user,
        "claimed_legacy_transactions": 0,
        "claimed_legacy_uploads": 0,
    }


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    normalized_username = validate_username(payload.email)
    validate_password(payload.password)
    display_name = (
        payload.display_name.strip()
        if payload.display_name and payload.display_name.strip()
        else display_name_from_username(normalized_username)
    )
    audit_context = get_audit_request_context(request)

    if get_user_auth_by_email(normalized_username):
        record_audit_event(
            action="user_register",
            status="rejected",
            target_type="user",
            target_id=normalized_username,
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": "duplicate_username"},
        )
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that username.",
        )

    try:
        created_user, claimed_counts = register_new_user(
            email=normalized_username,
            password_hash=hash_password(payload.password),
            display_name=display_name,
        )
    except DuplicateUsernameError:
        record_audit_event(
            action="user_register",
            status="rejected",
            target_type="user",
            target_id=normalized_username,
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": "duplicate_username"},
        )
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that username.",
        ) from None

    session_token = generate_session_token()
    create_session(
        created_user["id"],
        hash_session_token(session_token),
        session_expiry(),
    )
    update_user_last_login(created_user["id"])
    attach_session_cookie(response, session_token)
    record_audit_event(
        action="user_register",
        status="success",
        user_id=created_user["id"],
        target_type="user",
        target_id=str(created_user["id"]),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "claimed_legacy_transactions": claimed_counts["claimed_transaction_count"],
            "claimed_legacy_uploads": claimed_counts["claimed_statement_count"],
        },
    )
    record_audit_event(
        action="user_sign_in",
        status="success",
        user_id=created_user["id"],
        target_type="session",
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
    )
    return {
        "authenticated": True,
        "user": created_user,
        "claimed_legacy_transactions": claimed_counts["claimed_transaction_count"],
        "claimed_legacy_uploads": claimed_counts["claimed_statement_count"],
    }


@app.post("/auth/login")
def login(
    payload: AuthRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    normalized_username = validate_username(payload.email)
    validate_password(payload.password)
    audit_context = get_audit_request_context(request)
    user = get_user_auth_by_email(normalized_username)

    if not user or not verify_password(payload.password, user["password_hash"]):
        record_audit_event(
            action="user_sign_in",
            status="rejected",
            target_type="user",
            target_id=normalized_username,
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": "invalid_credentials"},
        )
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    session_token = generate_session_token()
    create_session(user["id"], hash_session_token(session_token), session_expiry())
    update_user_last_login(user["id"])
    attach_session_cookie(response, session_token)
    record_audit_event(
        action="user_sign_in",
        status="success",
        user_id=user["id"],
        target_type="session",
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
    )
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "created_at": user["created_at"],
        },
        "claimed_legacy_transactions": 0,
        "claimed_legacy_uploads": 0,
    }


@app.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    current_user: UserRecord | None = Depends(get_optional_current_user),
) -> SessionResponse:
    session_token = request.cookies.get("pst_session")
    audit_context = get_audit_request_context(request)
    if session_token:
        revoke_session(hash_session_token(session_token))
    clear_session_cookie(response)

    if current_user:
        record_audit_event(
            action="user_sign_out",
            status="success",
            user_id=current_user["id"],
            target_type="session",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
        )

    return {
        "authenticated": False,
        "user": None,
        "claimed_legacy_transactions": 0,
        "claimed_legacy_uploads": 0,
    }


@app.get("/ai/settings")
def ai_settings(
    current_user: UserRecord = Depends(require_current_user),
) -> AiSettingsResponse:
    stored_settings = list_user_ai_provider_settings(user_id=current_user["id"])
    return _build_ai_settings_response(stored_settings)


@app.put("/ai/settings/{provider_name}")
def save_ai_settings(
    provider_name: str,
    payload: AiProviderSettingsRequest,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> AiSettingsResponse:
    resolved_provider = _resolve_ai_provider(provider_name)
    audit_context = get_audit_request_context(request)

    try:
        upsert_user_ai_provider_setting(
            user_id=current_user["id"],
            provider=resolved_provider,
            api_key=payload.api_key,
            model=payload.model,
            is_selected=payload.selected,
        )
    except ValueError as exc:
        record_audit_event(
            action="ai_settings_update",
            status="rejected",
            user_id=current_user["id"],
            target_type="ai_provider",
            target_id=resolved_provider,
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    record_audit_event(
        action="ai_settings_update",
        status="success",
        user_id=current_user["id"],
        target_type="ai_provider",
        target_id=resolved_provider,
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "provider": resolved_provider,
            "selected": payload.selected,
            "api_key_supplied": bool(payload.api_key and payload.api_key.strip()),
        },
    )
    stored_settings = list_user_ai_provider_settings(user_id=current_user["id"])
    return _build_ai_settings_response(stored_settings)


@app.delete("/ai/settings/{provider_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_settings(
    provider_name: str,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> None:
    resolved_provider = _resolve_ai_provider(provider_name)
    deleted = delete_user_ai_provider_setting(
        user_id=current_user["id"],
        provider=resolved_provider,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider settings not found.")

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="ai_settings_delete",
        status="success",
        user_id=current_user["id"],
        target_type="ai_provider",
        target_id=resolved_provider,
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"provider": resolved_provider},
    )


def _load_ai_insight_inputs(
    *,
    user_id: int,
    provider: AiProvider | None,
    resolved_range: TimeRange,
    language: InsightLanguage,
):
    try:
        configured_provider = get_user_ai_provider_secret(
            user_id=user_id,
            provider=provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if configured_provider is None:
        raise HTTPException(
            status_code=400,
            detail="Configure an AI provider in Settings before requesting insights.",
        )

    transactions = [
        transaction
        for transaction in get_transactions(
            user_id=user_id,
            range_key=resolved_range,
            limit=None,
        )
        if not should_exclude_from_spending(
            transaction["merchant"],
            category=transaction.get("category"),
        )
    ]
    if not transactions:
        raise HTTPException(
            status_code=400,
            detail="No spending transactions are available for the selected period.",
        )

    snapshot = build_spending_insight_snapshot(
        transactions,
        range_key=resolved_range,
        language=language,
    )
    return configured_provider, transactions, snapshot


@app.post("/ai/insights")
async def ai_insights(
    payload: AiInsightsRequest,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> AiInsightResponse:
    resolved_range = _resolve_ai_insight_range(payload.range)
    audit_context = get_audit_request_context(request)
    configured_provider, transactions, snapshot = await asyncio.to_thread(
        _load_ai_insight_inputs,
        user_id=current_user["id"],
        provider=payload.provider,
        resolved_range=resolved_range,
        language=cast(InsightLanguage, payload.language),
    )

    try:
        insight_result = await generate_spending_insights(
            provider=configured_provider["provider"],
            api_key=configured_provider["api_key"],
            model=configured_provider["model"],
            snapshot=snapshot,
        )
    except RuntimeError as exc:
        await asyncio.to_thread(
            record_audit_event,
            action="ai_insights_generate",
            status="rejected",
            user_id=current_user["id"],
            target_type="ai_provider",
            target_id=configured_provider["provider"],
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={
                "provider": configured_provider["provider"],
                "range": resolved_range,
                "reason": str(exc)[:400],
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await asyncio.to_thread(
        record_audit_event,
        action="ai_insights_generate",
        status="success",
        user_id=current_user["id"],
        target_type="ai_provider",
        target_id=insight_result["provider"],
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "provider": insight_result["provider"],
            "model": insight_result["model"],
            "range": resolved_range,
            "transaction_count": len(transactions),
            "display_currency": snapshot["display_currency"],
        },
    )
    return {
        "provider": insight_result["provider"],
        "model": insight_result["model"],
        "time_range": resolved_range,
        "generated_at": insight_result["generated_at"],
        "insight_text": insight_result["insight_text"],
        "citations": insight_result["citations"],
        "data_shared": [
            "merchant names",
            "transaction dates",
            "amounts and currencies",
            "derived category, monthly, and concentration summaries",
        ],
        "snapshot": cast(dict[str, object], snapshot),
    }


@app.get("/summary")
def summary(
    range: str = Query(default="all"),
    currency: str = Query(default="USD"),
    current_user: UserRecord = Depends(require_current_user),
) -> SummaryResponse:
    resolved_range = _resolve_time_range(range)
    resolved_currency = _resolve_display_currency(currency)
    return get_transactions_summary(
        user_id=current_user["id"],
        range_key=resolved_range,
        display_currency=resolved_currency,
    )


@app.get("/uploads/recent")
def recent_uploads(
    limit: int = 5,
    current_user: UserRecord = Depends(require_current_user),
) -> RecentUploadsResponse:
    limited_value = max(1, min(limit, 12))
    return {
        "uploads": get_recent_uploads(
            user_id=current_user["id"],
            limit=limited_value,
        )
    }


@app.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_statement_upload(
    upload_id: int,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> None:
    existing_upload = get_statement_upload_for_user(
        user_id=current_user["id"],
        upload_id=upload_id,
    )
    if existing_upload is None:
        raise HTTPException(status_code=404, detail="Statement upload not found.")

    deleted_upload = delete_statement_upload_for_user(
        user_id=current_user["id"],
        upload_id=upload_id,
    )
    if deleted_upload is None:
        raise HTTPException(status_code=404, detail="Statement upload not found.")

    stored_path = _get_upload_dir(current_user["id"]) / deleted_upload["stored_file_name"]
    stored_path.unlink(missing_ok=True)

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="statement_upload_delete",
        status="success",
        user_id=current_user["id"],
        target_type="statement_upload",
        target_id=str(upload_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "display_name": deleted_upload["display_name"],
            "stored_file_name": deleted_upload["stored_file_name"],
        },
    )


@app.get("/transactions")
def transactions(
    range: str = Query(default="all"),
    limit: int = Query(default=5000, ge=1, le=20000),
    current_user: UserRecord = Depends(require_current_user),
) -> TransactionsResponse:
    resolved_range = _resolve_time_range(range)
    return {
        "transactions": get_transactions(
            user_id=current_user["id"],
            range_key=resolved_range,
            limit=limit,
        )
    }


@app.get("/dashboard/view")
def dashboard_view(
    range: str = Query(default="all"),
    search: str = Query(default=""),
    amount_filter: str = Query(default="all"),
    selected_merchant: str = Query(default=""),
    sort: str = Query(default="amount_desc"),
    currency: str = Query(default="USD"),
    limit: int = Query(default=5000, ge=1, le=20000),
    current_user: UserRecord = Depends(require_current_user),
) -> DashboardViewResponse:
    resolved_range = _resolve_time_range(range)
    resolved_amount_filter = _resolve_amount_filter(amount_filter)
    resolved_sort_option = _resolve_sort_option(sort)
    resolved_currency = _resolve_display_currency(currency)

    all_transactions = [
        transaction
        for transaction in get_transactions(
            user_id=current_user["id"],
            range_key=resolved_range,
            limit=limit,
        )
        if not should_exclude_from_spending(
            transaction["merchant"],
            category=transaction.get("category"),
        )
    ]
    converted_transactions = [
        dashboard_transaction
        for transaction in all_transactions
        if (
            dashboard_transaction := _dashboard_transaction_for_currency(
                transaction,
                resolved_currency,
            )
        )
        is not None
    ]
    base_filtered, filtered_transactions = _filter_dashboard_transactions(
        converted_transactions,
        search=search,
        amount_filter=resolved_amount_filter,
        selected_merchant=selected_merchant,
    )
    sorted_transactions = _sort_transactions(filtered_transactions, resolved_sort_option)
    merchant_breakdown, category_breakdown, date_breakdown = _build_dashboard_breakdowns(
        base_filtered,
        filtered_transactions,
    )
    metrics = _build_dashboard_metrics(filtered_transactions, converted_transactions)
    summary_data: TransactionsSummary = get_transactions_summary(
        user_id=current_user["id"],
        range_key=resolved_range,
        display_currency=resolved_currency,
    )
    return {
        "active_currency": resolved_currency,
        "exchange_rate": exchange_rate_metadata(),
        "summary": summary_data,
        "transactions": converted_transactions,
        "sorted_transactions": sorted_transactions,
        "merchant_breakdown": merchant_breakdown,
        "category_breakdown": category_breakdown,
        "date_breakdown": date_breakdown,
        "metrics": metrics,
    }


@app.post(
    "/transactions/manual",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid manual transaction request"}},
)
def create_manual_transaction(
    payload: ManualTransactionRequest,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> ManualTransactionResponse:
    try:
        fields = _parse_manual_transaction_fields(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created = insert_manual_transaction(
        user_id=current_user["id"],
        transaction_date=str(fields["transaction_date"]),
        merchant=str(fields["merchant"]),
        original_amount=float(fields["original_amount"]),
        original_currency=str(fields["original_currency"]),
        base_currency=str(fields["base_currency"]),
        exchange_rate=(
            float(fields["exchange_rate"])
            if fields["exchange_rate"] is not None
            else None
        ),
        exchange_rate_date=(
            str(fields["exchange_rate_date"])
            if fields["exchange_rate_date"] is not None
            else None
        ),
        category=cast(str | None, fields["category"]),
        payment_method=cast(str | None, fields["payment_method"]),
    )
    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="manual_transaction_create",
        status="success",
        user_id=current_user["id"],
        target_type="transaction",
        target_id=created["file_name"],
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "merchant": created["merchant"],
            "date": created["date"],
            "original_amount": created["original_amount"],
            "original_currency": created["original_currency"],
            "base_amount": created["base_amount"],
            "base_currency": created["base_currency"],
        },
    )
    return {"transaction": created}


@app.patch("/transactions/{transaction_id}")
def patch_transaction(
    transaction_id: int,
    payload: TransactionUpdateRequest,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> ManualTransactionResponse:
    category = payload.category.strip() if payload.category is not None else None
    merchant = payload.merchant.strip() if payload.merchant is not None else None
    if category == "":
        raise HTTPException(status_code=400, detail="Category cannot be empty.")
    if merchant == "":
        raise HTTPException(status_code=400, detail="Merchant cannot be empty.")
    if category is None and merchant is None:
        raise HTTPException(
            status_code=400,
            detail="Provide category and/or merchant to update.",
        )

    updated = update_stored_transaction(
        user_id=current_user["id"],
        transaction_id=transaction_id,
        category=category,
        merchant=merchant,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="transaction_update",
        status="success",
        user_id=current_user["id"],
        target_type="transaction",
        target_id=str(transaction_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "merchant": updated["merchant"],
            "category": updated["category"],
        },
    )
    return {"transaction": updated}


@app.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard_transaction(
    transaction_id: int,
    request: Request,
    current_user: UserRecord = Depends(require_current_user),
) -> None:
    deleted = delete_transaction(
        user_id=current_user["id"],
        transaction_id=transaction_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="transaction_delete",
        status="success",
        user_id=current_user["id"],
        target_type="transaction",
        target_id=str(transaction_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
    )


def _execute_batch_import(
    *,
    user_id: int,
    request_payload: BatchImportRequest,
    audit_context: AuditRequestContext,
) -> BatchImportRunExecutionResponse:
    logger.info("batch-imports/run started user_id=%s", user_id)
    try:
        store = _get_batch_import_store(user_id, request_payload.sqlite_output_path)
        pdf_source_dir = resolve_allowed_directory(
            request_payload.pdf_source_dir,
            default=_default_batch_pdf_source_dir_for_user(user_id),
        )
        text_source_dir = resolve_allowed_directory(
            request_payload.text_source_dir,
            default=_default_batch_text_source_dir_for_user(user_id),
        )
        importer = StatementImporter(
            pdf_source_dir=str(pdf_source_dir),
            text_source_dir=str(text_source_dir),
            csv_output_path=resolve_allowed_file(
                request_payload.csv_output_path,
                default=_default_batch_csv_path_for_user(user_id),
            ),
            store=store,
        )
        result = importer.import_transactions()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("batch-imports/run failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch import failed: {exc}",
        ) from exc

    logger.info(
        "batch-imports/run finished user_id=%s statements=%s transactions=%s skipped=%s",
        user_id,
        result.statement_count,
        result.transaction_count,
        len(result.skipped_files),
    )
    record_audit_event(
        action="batch_import_run",
        status="success",
        user_id=user_id,
        target_type="batch_import_run",
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "statement_count": result.statement_count,
            "transaction_count": result.transaction_count,
            "skipped_files": len(result.skipped_files),
        },
    )
    return {"run": result.to_payload()}


@app.post("/batch-imports/run", status_code=status.HTTP_201_CREATED)
async def run_batch_import(
    request: Request,
    payload: BatchImportRequest | None = None,
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportRunExecutionResponse:
    return await asyncio.to_thread(
        _execute_batch_import,
        user_id=current_user["id"],
        request_payload=payload or BatchImportRequest(),
        audit_context=get_audit_request_context(request),
    )


@app.post("/batch-imports/import", status_code=status.HTTP_201_CREATED)
def import_batch_content(
    payload: BulkContentImportRequest,
    current_user: UserRecord = Depends(require_current_user),
) -> BulkContentImportResponse:
    store = _get_batch_import_store(
        current_user["id"],
        payload.sqlite_output_path,
    )
    store.initialize()
    category_engine = CategoryEngine(store.load_learned_rules())
    try:
        transactions = parse_import_content(
            payload.content,
            format=payload.format,
            bank=payload.bank,
            currency=payload.currency,
            source_name=payload.source_name,
            category_engine=category_engine,
        )
    except BulkImportParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.destination == "dashboard":
        upload_record = create_statement_upload(
            user_id=current_user["id"],
            original_file_name=payload.source_name,
            stored_file_name=f"import-{uuid4().hex[:12]}.json",
            file_fingerprint_sha256=sha256(
                payload.content.encode("utf-8")
            ).hexdigest(),
            parse_status="imported",
            currency=payload.currency.upper(),
        )
        dashboard_rows: list[Transaction] = [
            {
                "date": item.transaction_date,
                "merchant": item.merchant_raw,
                "amount": item.amount,
                "category": item.category,
                "currency": item.currency,
            }
            for item in transactions
        ]
        inserted = insert_transactions(
            dashboard_rows,
            user_id=current_user["id"],
            file_name=upload_record["file_name"],
            file_fingerprint=sha256(payload.content.encode("utf-8")).hexdigest(),
            original_file_name=payload.source_name,
            statement_upload_id=upload_record["id"],
        )
        return {
            "destination": "dashboard",
            "imported_count": inserted,
            "run": None,
        }

    run_id, _imported_at, _needs_review_count = store.record_import_run(
        transactions,
        pdf_source_dir="",
        text_source_dir="",
        csv_output_path=str(_default_batch_csv_path_for_user(current_user["id"])),
        statement_count=1,
    )
    store.auto_apply_default_categories(
        run_id=run_id,
        category_engine=category_engine,
    )
    store.export_csv(run_id, _default_batch_csv_path_for_user(current_user["id"]))
    created_run = store.get_run(run_id)
    if created_run is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import run {run_id} was not found after creation.",
        )
    return {
        "destination": "batch",
        "imported_count": len(transactions),
        "run": created_run,
    }


@app.post("/batch-imports/merge", status_code=status.HTTP_200_OK)
def merge_batch_import(
    request: Request,
    payload: BatchImportMergeRequest | None = None,
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportMergeResponse:
    request_payload = payload or BatchImportMergeRequest()
    store = _get_batch_import_store(
        current_user["id"],
        request_payload.sqlite_output_path,
    )

    try:
        result = merge_run_to_postgres(
            store,
            user_id=current_user["id"],
            run_id=request_payload.run_id,
            include_needs_review=request_payload.include_needs_review,
            include_excluded=request_payload.include_excluded,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="batch_import_merge",
        status="success",
        user_id=current_user["id"],
        target_type="batch_import_run",
        target_id=str(result["run_id"]) if result["run_id"] is not None else None,
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "merged_count": int(result["merged_count"]),
            "skipped_count": int(result["skipped_count"]),
        },
    )

    return {
        "run_id": result["run_id"],
        "merged_count": int(result["merged_count"]),
        "skipped_count": int(result["skipped_count"]),
    }


@app.get("/batch-imports/latest")
def batch_import_latest(
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportLatestResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    return {"run": store.get_latest_run()}


@app.get("/batch-imports/transactions")
def batch_import_transactions(
    needs_review_only: bool = Query(default=False),
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=500, ge=1, le=5000),
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportTransactionsResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    return {
        "transactions": store.list_transactions(
            needs_review_only=needs_review_only,
            run_id=run_id,
            limit=limit,
        )
    }


@app.patch("/batch-imports/transactions/{transaction_id}")
def update_batch_import_transaction(
    transaction_id: int,
    payload: BatchReviewUpdateRequest,
    request: Request,
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportReviewUpdateResponse:
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required.")

    store = _get_batch_import_store(current_user["id"], sqlite_path)
    merchant_raw = (
        payload.merchant_raw.strip() if payload.merchant_raw is not None else None
    )
    if merchant_raw == "":
        raise HTTPException(status_code=400, detail="Merchant cannot be empty.")

    try:
        updated_transaction = store.update_transaction_review(
            transaction_id,
            category=category,
            exclude_from_spending=payload.exclude_from_spending,
            save_rule=payload.save_rule,
            merchant_raw=merchant_raw,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Batch-import transaction {transaction_id} was not found.",
        ) from None

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="batch_import_review_update",
        status="success",
        user_id=current_user["id"],
        target_type="batch_import_transaction",
        target_id=str(transaction_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"category": category},
    )
    return {"transaction": updated_transaction}


@app.delete(
    "/batch-imports/transactions/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_batch_import_transaction(
    transaction_id: int,
    request: Request,
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> None:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    if store.is_transaction_synced(transaction_id):
        row = store.get_transaction(transaction_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Batch-import transaction {transaction_id} was not found.",
            )
        delete_transaction_by_match(
            user_id=current_user["id"],
            transaction_date=row["transaction_date"],
            merchant=row["merchant_raw"],
            amount=float(row["amount"]),
        )

    run_id = store.delete_imported_transaction(transaction_id)
    if run_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Batch-import transaction {transaction_id} was not found.",
        )

    audit_context = get_audit_request_context(request)
    record_audit_event(
        action="batch_import_review_delete",
        status="success",
        user_id=current_user["id"],
        target_type="batch_import_transaction",
        target_id=str(transaction_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"run_id": run_id},
    )


@app.get("/batch-imports/needs-review-merchants")
def batch_import_needs_review_merchants(
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportNeedsReviewMerchantsResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    category_engine = CategoryEngine(store.load_learned_rules())
    merchants: list[NeedsReviewMerchantSummary] = []
    for item in store.list_needs_review_merchants(run_id=run_id, limit=limit):
        category_match = category_engine.categorize(item["merchant_raw"], 100.0)
        merchants.append(
            {
                **item,
                "suggested_category": category_match.category,
                "can_auto_apply": not category_match.needs_review,
            }
        )
    return {"merchants": merchants}


@app.post("/batch-imports/review/bulk", status_code=status.HTTP_200_OK)
def bulk_update_batch_import_review(
    payload: BatchReviewBulkUpdateRequest,
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportBulkReviewUpdateResponse:
    category = payload.category.strip()
    merchant_raw = payload.merchant_raw.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required.")
    if not merchant_raw:
        raise HTTPException(status_code=400, detail="Merchant is required.")

    store = _get_batch_import_store(current_user["id"], sqlite_path)
    updated_count = store.bulk_update_merchant_review(
        run_id=payload.run_id,
        merchant_raw=merchant_raw,
        category=category,
        exclude_from_spending=payload.exclude_from_spending,
        save_rule=payload.save_rule,
    )
    return {"updated_count": int(updated_count)}


@app.get("/batch-imports/digit-noise-pending-count")
def batch_import_digit_noise_pending_count(
    run_id: int | None = Query(default=None, ge=1),
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportDigitNoisePendingResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    return {
        "pending_count": store.count_digit_only_noise_pending(run_id=run_id),
    }


@app.post("/batch-imports/review/dismiss-digit-noise", status_code=status.HTTP_200_OK)
def dismiss_batch_import_digit_noise(
    payload: BatchReviewDismissDigitNoiseRequest,
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportDismissDigitNoiseResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    updated_count = store.bulk_dismiss_digit_only_noise(run_id=payload.run_id)
    return {"updated_count": int(updated_count)}


@app.post("/batch-imports/review/auto-categorize", status_code=status.HTTP_200_OK)
def auto_categorize_batch_import_review(
    payload: BatchReviewDismissDigitNoiseRequest,
    sqlite_path: str | None = Query(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportAutoCategorizeResponse:
    store = _get_batch_import_store(current_user["id"], sqlite_path)
    category_engine = CategoryEngine(store.load_learned_rules())
    updated_count = store.auto_apply_default_categories(
        run_id=payload.run_id,
        category_engine=category_engine,
    )
    return {
        "updated_count": int(updated_count),
        "needs_review_count": store.count_needs_review(run_id=payload.run_id),
    }


def _load_uploaded_statement(
    file_path: Path, password: str | None
) -> tuple[str, list[Transaction]]:
    # Tests replace this helper. Production runs PDF CPU in a child process.
    return read_statement_pdf(file_path, password)


MAX_STATEMENT_UPLOAD_BYTES = 32 * 1024 * 1024


class _StatementUploadTooLarge(Exception):
    pass


def _copy_upload_stream(source: BinaryIO, dest: Path) -> str:
    """Copy an upload to disk in chunks and return its SHA-256 hex digest."""
    digest = sha256()
    total = 0
    with dest.open("wb") as buffer:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_STATEMENT_UPLOAD_BYTES:
                raise _StatementUploadTooLarge()
            buffer.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_upload(
    *,
    existing_upload: ExistingUpload | None,
    user_id: int,
    original_name: str,
    audit_context: AuditRequestContext,
) -> NoReturn:
    record_audit_event(
        action="duplicate_upload_rejection",
        status="rejected",
        user_id=user_id,
        target_type="statement_upload",
        target_id=str(existing_upload["id"]) if existing_upload else None,
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"file_name": original_name},
    )
    if existing_upload:
        detail = (
            "This statement was already imported as "
            f"'{existing_upload['display_name']}' on {existing_upload['uploaded_at']}."
        )
    else:
        detail = "This statement was already imported."
    raise HTTPException(status_code=409, detail=detail)


def _store_uploaded_pdf(
    *,
    upload_stream: BinaryIO,
    original_name: str,
    content_type: str | None,
    password: str | None,
    user_id: int,
    audit_context: AuditRequestContext,
) -> UploadResponse:
    has_pdf_extension = Path(original_name).suffix.lower() == ".pdf"
    is_pdf_content = content_type == "application/pdf"

    if not has_pdf_extension and not is_pdf_content:
        record_audit_event(
            action="statement_upload_attempt",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": "invalid_file_type", "file_name": original_name},
        )
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    password_clean = password.strip() if password else None
    record_audit_event(
        action="statement_upload_attempt",
        status="started",
        user_id=user_id,
        target_type="statement_upload",
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"file_name": original_name},
    )

    upload_dir = _get_upload_dir(user_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f".tmp-{uuid4().hex}.pdf"
    try:
        sha256_fingerprint = _copy_upload_stream(upload_stream, temp_path)
    except _StatementUploadTooLarge:
        temp_path.unlink(missing_ok=True)
        record_audit_event(
            action="statement_upload_attempt",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"reason": "file_too_large", "file_name": original_name},
        )
        raise HTTPException(
            status_code=413,
            detail="PDF uploads must be 32 MB or smaller.",
        ) from None
    existing_upload = get_existing_upload_by_fingerprint(
        user_id=user_id,
        file_fingerprint_sha256=sha256_fingerprint,
    )
    if existing_upload:
        temp_path.unlink(missing_ok=True)
        _reject_duplicate_upload(
            existing_upload=existing_upload,
            user_id=user_id,
            original_name=original_name,
            audit_context=audit_context,
        )

    saved_name = f"{uuid4().hex}.pdf"
    saved_path = upload_dir / saved_name
    temp_path.rename(saved_path)

    try:
        extracted_text, transactions = _load_uploaded_statement(
            saved_path,
            password_clean,
        )
    except PdfPasswordProtectedError as e:
        saved_path.unlink(missing_ok=True)
        need_password = (
            "This PDF is password-protected. Enter the document password "
            "below and try again."
        )
        record_audit_event(
            action="statement_upload_parse_failure",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={
                "file_name": original_name,
                "reason": "incorrect_password" if e.had_password else "password_required",
            },
        )
        raise HTTPException(
            status_code=400,
            detail="Incorrect PDF password." if e.had_password else need_password,
        ) from e
    except PdfDecryptError:
        saved_path.unlink(missing_ok=True)
        cannot_open = (
            "This PDF could not be opened. It may use unsupported "
            "encryption; try removing the password in a PDF app and "
            "export a new file."
        )
        record_audit_event(
            action="statement_upload_parse_failure",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"file_name": original_name, "reason": "decrypt_error"},
        )
        raise HTTPException(status_code=400, detail=cannot_open) from None
    display_name = original_name or saved_name

    parse_reason: str | None = None
    parse_status = "parsed"
    if not transactions:
        parse_reason = (
            "empty_text" if not extracted_text.strip() else "no_matching_lines"
        )
        parse_status = "no_transactions"

    try:
        upload_record = create_statement_upload(
            user_id=user_id,
            original_file_name=display_name,
            stored_file_name=saved_name,
            file_fingerprint_sha256=sha256_fingerprint,
            parse_status=parse_status,
            parse_reason=parse_reason,
            currency=transactions[0].get("currency") if transactions else None,
        )
    except DuplicateStatementUploadError:
        saved_path.unlink(missing_ok=True)
        _reject_duplicate_upload(
            existing_upload=get_existing_upload_by_fingerprint(
                user_id=user_id,
                file_fingerprint_sha256=sha256_fingerprint,
            ),
            user_id=user_id,
            original_name=original_name,
            audit_context=audit_context,
        )
    insert_transactions(
        transactions,
        user_id=user_id,
        file_name=saved_name,
        file_fingerprint=sha256_fingerprint,
        original_file_name=display_name,
        statement_upload_id=upload_record["id"],
    )
    record_audit_event(
        action="statement_upload_success",
        status="success",
        user_id=user_id,
        target_type="statement_upload",
        target_id=str(upload_record["id"]),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={
            "file_name": display_name,
            "parse_status": parse_status,
            "transaction_count": len(transactions),
        },
    )

    return {
        "file_path": str(saved_path),
        "original_file_name": display_name,
        "transactions": transactions,
        "parse_reason": parse_reason,
    }


@app.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    password: str | None = Form(default=None),
    current_user: UserRecord = Depends(require_current_user),
) -> UploadResponse:
    # Stream the body to disk on a worker thread so a large PDF is not held in
    # memory on the event loop. Database writes stay on that thread. PDF
    # extraction and parsing run in a child process so CPU work cannot hold
    # this process's GIL and block /health.
    try:
        return await asyncio.to_thread(
            _store_uploaded_pdf,
            upload_stream=file.file,
            original_name=file.filename or "",
            content_type=file.content_type,
            password=password,
            user_id=current_user["id"],
            audit_context=get_audit_request_context(request),
        )
    finally:
        await file.close()

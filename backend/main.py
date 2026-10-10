import asyncio
import heapq
import logging
import os
import sys
import threading
from datetime import date
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn, TypedDict, cast
from uuid import uuid4

# Repo root on sys.path so `from backend.*` works when running
# `uvicorn main:app` from backend/.
_repo_root = Path(__file__).resolve().parent.parent
_repo_root_str = str(_repo_root)
if _repo_root_str not in sys.path:
    sys.path.insert(0, _repo_root_str)

from fastapi import (  # noqa: E402
    Cookie,
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
from psycopg_pool import PoolTimeout  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import psycopg  # noqa: E402

from backend.auth import (  # noqa: E402
    attach_session_cookie,
    clear_session_cookie,
    display_name_from_username,
    generate_session_token,
    AuditRequestContext,
    SESSION_COOKIE_NAME,
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
from backend.database import (  # noqa: E402
    ExistingUpload,
    UserAiProviderSettingRecord,
    RecentUpload,
    StoredTransaction,
    TimeRange,
    UserRecord,
    DuplicateStatementUploadError,
    DuplicateUsernameError,
    count_statement_uploads,
    create_session,
    delete_user_ai_provider_setting,
    get_existing_upload_by_fingerprint,
    get_recent_uploads,
    get_transactions,
    get_transactions_summary,
    iter_transactions,
    get_user_ai_provider_secret,
    get_user_auth_by_email,
    import_statement,
    init_db,
    insert_manual_transaction,
    list_user_ai_provider_settings,
    record_audit_event,
    register_new_user,
    revoke_session,
    upsert_user_ai_provider_setting,
    delete_statement_upload_for_user,
    delete_transaction,
    delete_transaction_by_source_key,
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
)
from backend.batch_import import (  # noqa: E402
    BatchImportRunSummary,
    BatchImportStore,
    BatchStoredTransaction,
    CategoryEngine,
    StatementImporter,
    default_sqlite_output_path,
    merge_run_to_postgres,
)
from backend.currency import (  # noqa: E402
    DEFAULT_BASE_CURRENCY,
    DisplayCurrency,
    SupportedCurrency,
    SUPPORTED_CURRENCY_CODES,
    derive_base_money_fields,
    exchange_rate_metadata,
    normalize_supported_currency_code,
)
from backend.parser import Transaction  # noqa: E402
from backend.transaction_source import (  # noqa: E402
    statement_content_fingerprint,
    with_statement_card_last4,
)
from backend.spending import (  # noqa: E402
    UnconvertedTransaction,
    iter_spending_partition,
    summary_from_totals,
)
from backend.pdf_utils import (  # noqa: E402
    PdfDecryptError,
    PdfPasswordProtectedError,
    read_statement_pdf,
)
from backend.process_pool import (  # noqa: E402
    PdfWorkerBusy,
    PdfWorkerError,
    PdfWorkerTimeout,
    pdf_parse_timeout_seconds,
    shutdown_process_pool,
)
from backend.db import (  # noqa: E402
    bind_request_context,
    clear_request_context,
    close_all_pools,
    failure_category,
    ping_database,
    pool_stats,
)


DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
LOCAL_FRONTEND_ORIGINS = ("http://localhost:3000",)
logger = logging.getLogger(__name__)


def _audit_after_commit(**kwargs: Any) -> None:
    """Record a success audit without failing a change that already saved."""
    try:
        record_audit_event(**kwargs)
    except (psycopg.Error, PoolTimeout):
        logger.exception(
            "audit log failed after the change was saved action=%s",
            kwargs.get("action"),
        )


_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema() -> None:
    """Apply migrations once. Later calls are a flag check."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        init_db()
        _schema_ready = True


def prepare_schema_on_startup() -> None:
    """Try migrations, but keep the process up when Postgres is unreachable.

    ``/ready`` calls :func:`ensure_schema` again, so a database that was down
    at boot becomes ready without restarting this process.
    """
    try:
        ensure_schema()
    except RuntimeError as exc:
        if "DATABASE_URL environment variable is required" not in str(exc):
            raise
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        logger.warning(
            "database schema setup deferred category=%s error_type=%s",
            failure_category(exc),
            type(exc).__name__,
        )


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
    unconverted_transaction_count: int
    unconverted_currencies: list[str]
    statement_count_scope: Literal["all_time"]


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
    sorted_transactions: list[DashboardTransaction]
    unconverted_transactions: list[UnconvertedTransaction]
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


class BatchImportMergeRequest(BaseModel):
    run_id: int | None = None
    include_needs_review: bool = True
    include_excluded: bool = False


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
    # The env var only picks the parent folder. Each user reads their own
    # subfolder so one user's statements never reach another user's import.
    user_dir = default_pdf_source_dir() / f"user-{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _default_batch_text_source_dir_for_user(user_id: int) -> Path:
    # The env var only picks the parent folder. Each user reads their own
    # subfolder so one user's statements never reach another user's import.
    user_dir = default_text_source_dir() / f"user-{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _warn_about_statements_in_shared_batch_root() -> None:
    # Older releases read the shared root. Tell the operator in the server
    # log; the API response must not hint at files outside the user's folder.
    stray_count = sum(1 for _ in default_pdf_source_dir().glob("*.pdf")) + sum(
        1 for _ in default_text_source_dir().glob("*.txt")
    )
    if stray_count:
        logger.warning(
            "batch-imports/run: %s statement file(s) in the root of %s or %s are "
            "ignored. Move them into the owner's user-<id> folder.",
            stray_count,
            default_pdf_source_dir(),
            default_text_source_dir(),
        )


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


def _get_batch_import_store(user_id: int) -> BatchImportStore:
    # Paths come only from the signed-in user's id, never from the request.
    store = BatchImportStore(_default_batch_sqlite_path_for_user(user_id))
    store.initialize()
    return store


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema setup talks to Postgres. Keep it off the event loop.
    # A down database must not kill the process: /health stays up and
    # /ready retries migrations.
    await asyncio.to_thread(prepare_schema_on_startup)
    try:
        yield
    finally:
        shutdown_process_pool()
        close_all_pools()


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
    bind_request_context(request_id=request_id, endpoint=request.url.path)
    try:
        response = await call_next(request)
    finally:
        clear_request_context()
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def read_root() -> dict[str, str]:
    return {"message": "Personal Spending Tracker API"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    # Liveness only. Do not query Postgres.
    return {"status": "ok"}


_DATABASE_UNAVAILABLE = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    PoolTimeout,
)


def _diagnostics_snapshot(
    *,
    database_ok: bool,
    latency_ms: float | None,
    exc: BaseException | None,
) -> dict[str, object]:
    stats = pool_stats()
    if not database_ok:
        return {
            "api": "ok",
            "database": "unavailable",
            "database_ok": False,
            "failure_category": failure_category(exc) if exc else "database_error",
            "database_latency_ms": None,
            "pool_size": stats["pool_size"],
            "pool_available": stats["pool_available"],
        }
    return {
        "api": "ok",
        "database": "ok",
        "database_ok": True,
        "database_latency_ms": round(latency_ms or 0, 1),
        "pool_size": stats["pool_size"],
        "pool_available": stats["pool_available"],
    }


def _reject_unauthenticated_diagnostics(request: Request) -> None:
    """Return 401. A down database must not turn this into a 500."""
    audit_context = get_audit_request_context(request)
    try:
        record_audit_event(
            action="authorization_failure",
            status="rejected",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"path": request.url.path, "method": request.method},
        )
    except _DATABASE_UNAVAILABLE:
        pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
    )


@app.get(
    "/ready",
    responses={503: {"description": "Postgres cannot serve queries."}},
)
def ready() -> dict[str, object]:
    """Readiness. SELECT 1, or 503 when Postgres cannot serve queries."""
    try:
        ensure_schema()
        latency_ms = ping_database()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "failure_category": failure_category(exc),
            },
        ) from exc
    return {"status": "ok", "database_latency_ms": round(latency_ms, 1)}


@app.get(
    "/diagnostics",
    responses={401: {"description": "Authentication is required."}},
)
def diagnostics(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, object]:
    """Safe ops snapshot for a signed-in user. No credentials or spending rows.

    Session lookup uses Postgres. When that lookup cannot run, a presented
    session cookie still receives the unavailable snapshot instead of a 500.
    """
    try:
        current_user = get_optional_current_user(request, session_token)
    except _DATABASE_UNAVAILABLE as exc:
        if session_token:
            return _diagnostics_snapshot(
                database_ok=False,
                latency_ms=None,
                exc=exc,
            )
        _reject_unauthenticated_diagnostics(request)
    if current_user is None:
        _reject_unauthenticated_diagnostics(request)
    try:
        latency_ms = ping_database()
    except Exception as exc:
        return _diagnostics_snapshot(
            database_ok=False,
            latency_ms=None,
            exc=exc,
        )
    return _diagnostics_snapshot(
        database_ok=True,
        latency_ms=latency_ms,
        exc=None,
    )


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


@app.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "An account already exists for that username."},
    },
)
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
    _audit_after_commit(
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
    _audit_after_commit(
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
    _audit_after_commit(
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
        _audit_after_commit(
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

    _audit_after_commit(
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
    _audit_after_commit(
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

    transactions = get_transactions(
        user_id=user_id,
        range_key=resolved_range,
        limit=None,
    )
    if not transactions:
        raise HTTPException(
            status_code=400,
            detail="No spending transactions are available for the selected period.",
        )

    try:
        snapshot = build_spending_insight_snapshot(
            transactions,
            range_key=resolved_range,
            language=language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        _audit_after_commit,
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
            "transaction_count": snapshot["transaction_count"],
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
    _audit_after_commit(
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


class _WorstFirst:
    __slots__ = ("key",)

    def __init__(self, key: tuple[object, ...]) -> None:
        self.key = key

    def __lt__(self, other: "_WorstFirst") -> bool:
        return self.key > other.key


def _desired_sort_key(
    transaction: DashboardTransaction,
    sort_option: SortOption,
    sequence: int,
) -> tuple[object, ...]:
    if sort_option == "amount_asc":
        primary: object = transaction["amount"]
    elif sort_option == "merchant":
        primary = transaction["merchant"].lower()
    elif sort_option == "date":
        primary = transaction["date"]
    else:
        primary = -transaction["amount"]
    return (primary, sequence)


def _keep_limited_row(rows: list, row: object, limit: int) -> None:
    if len(rows) < limit:
        rows.append(row)


def _keep_sorted_row(
    heap: list[tuple[_WorstFirst, int, DashboardTransaction]],
    transaction: DashboardTransaction,
    sort_option: SortOption,
    limit: int,
    sequence: int,
) -> None:
    desired = _desired_sort_key(transaction, sort_option, sequence)
    entry = (_WorstFirst(desired), sequence, transaction)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif desired < heap[0][0].key:
        heapq.heapreplace(heap, entry)


def _format_dashboard_breakdowns(
    merchant_totals: dict[str, float],
    category_totals: dict[str, float],
    date_totals: dict[str, float],
) -> tuple[
    list[MerchantBreakdownItem],
    list[CategoryBreakdownItem],
    list[DateBreakdownItem],
]:
    merchant_breakdown: list[MerchantBreakdownItem] = sorted(
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
    category_total_spend = sum(category_totals.values())
    category_breakdown: list[CategoryBreakdownItem] = [
        {
            "category": category,
            "total": total,
            "share": (total / category_total_spend) if category_total_spend > 0 else 0.0,
        }
        for category, total in sorted(
            category_totals.items(), key=lambda item: item[1], reverse=True
        )
    ]
    date_breakdown: list[DateBreakdownItem] = [
        {
            "date": date_key,
            "total": total,
            "abs_total": abs(total),
        }
        for date_key, total in sorted(date_totals.items(), key=lambda item: item[0])[-10:]
    ]
    return merchant_breakdown, category_breakdown, date_breakdown


def _dashboard_metrics(
    *,
    model_total: float,
    model_count: int,
    selection_net: float,
    selection_spend: float,
    selection_refunds: float,
    selection_count: int,
    largest_amount: float,
) -> DashboardMetrics:
    return {
        "selection_net": selection_net,
        "selection_spend": selection_spend,
        "selection_refunds": selection_refunds,
        "selection_average": (
            selection_net / selection_count if selection_count else 0.0
        ),
        "upload_net": model_total,
        "largest_transaction_amount": largest_amount,
        "selection_coverage": (
            selection_count / model_count if model_count else 0.0
        ),
        "model_total_in_active_currency": model_total,
    }


def _collect_limited_dashboard(
    *,
    user_id: int,
    range_key: TimeRange,
    search: str,
    amount_filter: AmountFilter,
    selected_merchant: str,
    sort_option: SortOption,
    limit: int,
    currency: DisplayCurrency,
) -> DashboardViewResponse:
    normalized_search = search.strip().lower()
    merchant_filter = selected_merchant.strip()
    unconverted_rows: list[UnconvertedTransaction] = []
    unconverted_currencies: set[str] = set()
    merchant_totals: dict[str, float] = {}
    category_totals: dict[str, float] = {}
    date_totals: dict[str, float] = {}
    sorted_heap: list[tuple[_WorstFirst, int, DashboardTransaction]] = []
    model_total = 0.0
    model_count = 0
    selection_net = 0.0
    selection_spend = 0.0
    selection_refunds = 0.0
    selection_count = 0
    largest_amount = 0.0
    unconverted_count = 0
    sequence = 0

    for kind, row in iter_spending_partition(
        iter_transactions(user_id=user_id, range_key=range_key, limit=None),
        currency,
    ):
        if kind == "unconverted":
            unconverted = cast(UnconvertedTransaction, row)
            unconverted_count += 1
            unconverted_currencies.add(unconverted["original_currency"])
            _keep_limited_row(unconverted_rows, unconverted, limit)
            continue
        transaction = cast(DashboardTransaction, row)
        amount_value = float(transaction["amount"])
        model_total += amount_value
        model_count += 1
        if not _transaction_matches_dashboard_filters(
            transaction,
            normalized_search=normalized_search,
            amount_filter=amount_filter,
        ):
            continue
        merchant_totals[transaction["merchant"]] = (
            merchant_totals.get(transaction["merchant"], 0.0) + amount_value
        )
        if merchant_filter and transaction["merchant"] != merchant_filter:
            continue
        selection_net += amount_value
        selection_count += 1
        if amount_value > 0:
            selection_spend += amount_value
            category_totals[transaction["category"]] = (
                category_totals.get(transaction["category"], 0.0) + amount_value
            )
        elif amount_value < 0:
            selection_refunds += abs(amount_value)
        largest_amount = max(largest_amount, abs(amount_value))
        date_totals[transaction["date"]] = (
            date_totals.get(transaction["date"], 0.0) + amount_value
        )
        _keep_sorted_row(sorted_heap, transaction, sort_option, limit, sequence)
        sequence += 1

    merchant_breakdown, category_breakdown, date_breakdown = (
        _format_dashboard_breakdowns(merchant_totals, category_totals, date_totals)
    )
    kept_rows = [
        item[2] for item in sorted(sorted_heap, key=lambda item: item[1])
    ]
    return {
        "active_currency": currency,
        "exchange_rate": exchange_rate_metadata(),
        "summary": summary_from_totals(
            total_spending=model_total,
            transaction_count=model_count,
            statement_count=count_statement_uploads(user_id=user_id),
            unconverted_count=unconverted_count,
            unconverted_currencies=sorted(unconverted_currencies),
        ),
        "sorted_transactions": _sort_transactions(kept_rows, sort_option),
        "unconverted_transactions": unconverted_rows,
        "merchant_breakdown": merchant_breakdown,
        "category_breakdown": category_breakdown,
        "date_breakdown": date_breakdown,
        "metrics": _dashboard_metrics(
            model_total=model_total,
            model_count=model_count,
            selection_net=selection_net,
            selection_spend=selection_spend,
            selection_refunds=selection_refunds,
            selection_count=selection_count,
            largest_amount=largest_amount,
        ),
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
    return _collect_limited_dashboard(
        user_id=current_user["id"],
        range_key=_resolve_time_range(range),
        search=search,
        amount_filter=_resolve_amount_filter(amount_filter),
        selected_merchant=selected_merchant,
        sort_option=_resolve_sort_option(sort),
        limit=limit,
        currency=_resolve_display_currency(currency),
    )


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
    _audit_after_commit(
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
    _audit_after_commit(
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
    _audit_after_commit(
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
    audit_context: AuditRequestContext,
) -> BatchImportRunExecutionResponse:
    logger.info("batch-imports/run started user_id=%s", user_id)
    try:
        store = _get_batch_import_store(user_id)
        importer = StatementImporter(
            pdf_source_dir=_default_batch_pdf_source_dir_for_user(user_id),
            text_source_dir=_default_batch_text_source_dir_for_user(user_id),
            csv_output_path=_default_batch_csv_path_for_user(user_id),
            store=store,
        )
        result = importer.import_transactions()
        _warn_about_statements_in_shared_batch_root()
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
    _audit_after_commit(
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
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportRunExecutionResponse:
    return await asyncio.to_thread(
        _execute_batch_import,
        user_id=current_user["id"],
        audit_context=get_audit_request_context(request),
    )


@app.post("/batch-imports/import", status_code=status.HTTP_201_CREATED)
def import_batch_content(
    payload: BulkContentImportRequest,
    current_user: UserRecord = Depends(require_current_user),
) -> BulkContentImportResponse:
    store = _get_batch_import_store(current_user["id"])
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
        content_fingerprint = sha256(payload.content.encode("utf-8")).hexdigest()
        dashboard_rows: list[Transaction] = [
            {
                "date": item.transaction_date,
                "merchant": item.merchant_raw,
                "amount": item.amount,
                "category": item.category,
                "currency": item.currency,
                "card_last4": item.card_last4 or "",
            }
            for item in transactions
        ]
        try:
            _upload_record, insert_result = import_statement(
                user_id=current_user["id"],
                original_file_name=payload.source_name,
                stored_file_name=f"import-{uuid4().hex[:12]}.json",
                file_fingerprint_sha256=content_fingerprint,
                parse_status="imported",
                currency=(
                    dashboard_rows[0]["currency"] if dashboard_rows else None
                ),
                transactions=dashboard_rows,
                statement_scope=content_fingerprint,
            )
        except DuplicateStatementUploadError:
            existing_upload = get_existing_upload_by_fingerprint(
                user_id=current_user["id"],
                file_fingerprint_sha256=content_fingerprint,
            )
            raise HTTPException(
                status_code=409,
                detail=_duplicate_upload_detail(existing_upload),
            ) from None
        return {
            "destination": "dashboard",
            "imported_count": insert_result.inserted_count,
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
    store = _get_batch_import_store(current_user["id"])

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
    _audit_after_commit(
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
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportLatestResponse:
    store = _get_batch_import_store(current_user["id"])
    return {"run": store.get_latest_run()}


@app.get("/batch-imports/transactions")
def batch_import_transactions(
    needs_review_only: bool = Query(default=False),
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=500, ge=1, le=5000),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportTransactionsResponse:
    store = _get_batch_import_store(current_user["id"])
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
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportReviewUpdateResponse:
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required.")

    store = _get_batch_import_store(current_user["id"])
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
    _audit_after_commit(
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
    current_user: UserRecord = Depends(require_current_user),
) -> Response:
    store = _get_batch_import_store(current_user["id"])
    synced = store.is_transaction_synced(transaction_id)
    source_key: str | None = None
    if synced:
        if store.get_transaction(transaction_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Batch-import transaction {transaction_id} was not found.",
            )
        source_key = store.get_postgres_source_key(transaction_id)
        if source_key:
            delete_transaction_by_source_key(
                user_id=current_user["id"],
                source_key=source_key,
            )

    run_id = store.delete_imported_transaction(transaction_id)
    if run_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Batch-import transaction {transaction_id} was not found.",
        )

    audit_context = get_audit_request_context(request)
    _audit_after_commit(
        action="batch_import_review_delete",
        status="success",
        user_id=current_user["id"],
        target_type="batch_import_transaction",
        target_id=str(transaction_id),
        request_id=audit_context.request_id,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        metadata={"run_id": run_id, "dashboard_row_deleted": bool(source_key)},
    )
    if synced and not source_key:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "dashboard_row_deleted": False,
                "detail": (
                    "Removed the batch row. The dashboard transaction was "
                    "left in place because this import has no link to it."
                ),
            },
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/batch-imports/needs-review-merchants")
def batch_import_needs_review_merchants(
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportNeedsReviewMerchantsResponse:
    store = _get_batch_import_store(current_user["id"])
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
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportBulkReviewUpdateResponse:
    category = payload.category.strip()
    merchant_raw = payload.merchant_raw.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required.")
    if not merchant_raw:
        raise HTTPException(status_code=400, detail="Merchant is required.")

    store = _get_batch_import_store(current_user["id"])
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
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportDigitNoisePendingResponse:
    store = _get_batch_import_store(current_user["id"])
    return {
        "pending_count": store.count_digit_only_noise_pending(run_id=run_id),
    }


@app.post("/batch-imports/review/dismiss-digit-noise", status_code=status.HTTP_200_OK)
def dismiss_batch_import_digit_noise(
    payload: BatchReviewDismissDigitNoiseRequest,
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportDismissDigitNoiseResponse:
    store = _get_batch_import_store(current_user["id"])
    updated_count = store.bulk_dismiss_digit_only_noise(run_id=payload.run_id)
    return {"updated_count": int(updated_count)}


@app.post("/batch-imports/review/auto-categorize", status_code=status.HTTP_200_OK)
def auto_categorize_batch_import_review(
    payload: BatchReviewDismissDigitNoiseRequest,
    current_user: UserRecord = Depends(require_current_user),
) -> BatchImportAutoCategorizeResponse:
    store = _get_batch_import_store(current_user["id"])
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
    file_path: Path,
    password: str | None,
    *,
    original_file_name: str = "",
) -> tuple[str, list[Transaction]]:
    # Tests replace this helper. Production runs PDF CPU in a child process.
    # Wait at most one parse limit for other PDFs, so the whole upload ends
    # within twice PDF_PARSE_TIMEOUT_SECONDS. The frontend proxy relies on it.
    return read_statement_pdf(
        file_path,
        password,
        max_wait=pdf_parse_timeout_seconds(),
        original_file_name=original_file_name,
    )


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
    raise HTTPException(
        status_code=409,
        detail=_duplicate_upload_detail(existing_upload),
    )


def _duplicate_upload_detail(existing_upload: ExistingUpload | None) -> str:
    """Same wording for a duplicate PDF and a duplicate pasted statement."""
    if existing_upload is None:
        return "This statement was already imported."
    name = existing_upload["display_name"]
    uploaded_at = existing_upload["uploaded_at"]
    return f"This statement was already imported as '{name}' on {uploaded_at}."


def _fingerprint_uploaded_pdf(
    upload_stream: BinaryIO,
    temp_path: Path,
    *,
    user_id: int,
    original_name: str,
    audit_context: AuditRequestContext,
) -> str:
    try:
        return _copy_upload_stream(upload_stream, temp_path)
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


def _open_uploaded_statement(
    saved_path: Path,
    password: str | None,
    *,
    user_id: int,
    original_name: str,
    audit_context: AuditRequestContext,
) -> tuple[str, list[Transaction]]:
    try:
        return _load_uploaded_statement(
            saved_path,
            password,
            original_file_name=original_name,
        )
    except PdfPasswordProtectedError as exc:
        saved_path.unlink(missing_ok=True)
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
                "reason": (
                    "incorrect_password" if exc.had_password else "password_required"
                ),
            },
        )
        if exc.had_password:
            detail = "Incorrect PDF password."
        else:
            detail = (
                "This PDF is password-protected. Enter the document password "
                "below and try again."
            )
        raise HTTPException(status_code=400, detail=detail) from exc
    except PdfDecryptError:
        saved_path.unlink(missing_ok=True)
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
        raise HTTPException(
            status_code=400,
            detail=(
                "This PDF could not be opened. It may use unsupported "
                "encryption; try removing the password in a PDF app and "
                "export a new file."
            ),
        ) from None
    except PdfWorkerError as exc:
        # After a timeout or crash the worker is already replaced.
        reason, outcome, status_code, detail = _pdf_worker_failure_response(exc)
        saved_path.unlink(missing_ok=True)
        logger.warning("statement upload parse %s user_id=%s", outcome, user_id)
        record_audit_event(
            action="statement_upload_parse_failure",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"file_name": original_name, "reason": reason},
        )
        raise HTTPException(status_code=status_code, detail=detail) from None
    except Exception:
        saved_path.unlink(missing_ok=True)
        logger.exception(
            "statement upload parse failed user_id=%s",
            user_id,
        )
        record_audit_event(
            action="statement_upload_parse_failure",
            status="rejected",
            user_id=user_id,
            target_type="statement_upload",
            request_id=audit_context.request_id,
            ip_address=audit_context.ip_address,
            user_agent=audit_context.user_agent,
            metadata={"file_name": original_name, "reason": "unreadable"},
        )
        raise HTTPException(
            status_code=422,
            detail="This PDF could not be read",
        ) from None


def _pdf_worker_failure_response(exc: PdfWorkerError) -> tuple[str, str, int, str]:
    """Return the audit reason, log wording, status and message for a failure."""
    if isinstance(exc, PdfWorkerBusy):
        return (
            "busy",
            "waited too long",
            503,
            "Other statements are still being read. Try again in a few minutes.",
        )
    if isinstance(exc, PdfWorkerTimeout):
        return (
            "timeout",
            "timed out",
            504,
            "This PDF took too long to read. Try again, or export the "
            "statement from your bank again.",
        )
    return (
        "worker_error",
        "worker crashed",
        422,
        "This PDF could not be read. Please try again or use another file.",
    )


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
    pdf_fingerprint = _fingerprint_uploaded_pdf(
        upload_stream,
        temp_path,
        user_id=user_id,
        original_name=original_name,
        audit_context=audit_context,
    )
    # Exact same PDF bytes (legacy or re-upload) still short-circuit early.
    existing_upload = get_existing_upload_by_fingerprint(
        user_id=user_id,
        file_fingerprint_sha256=pdf_fingerprint,
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
    try:
        temp_path.rename(saved_path)
        extracted_text, transactions = _open_uploaded_statement(
            saved_path,
            password_clean,
            user_id=user_id,
            original_name=original_name,
            audit_context=audit_context,
        )
        display_name = original_name or saved_name
        transactions = with_statement_card_last4(
            transactions,
            extracted_text,
            file_name=display_name,
        )
        # Shared with batch merge: fingerprint extracted text, not PDF bytes.
        text_fingerprint = statement_content_fingerprint(extracted_text)
        existing_by_text = get_existing_upload_by_fingerprint(
            user_id=user_id,
            file_fingerprint_sha256=text_fingerprint,
        )
        if existing_by_text:
            _reject_duplicate_upload(
                existing_upload=existing_by_text,
                user_id=user_id,
                original_name=original_name,
                audit_context=audit_context,
            )

        parse_reason: str | None = None
        parse_status = "parsed"
        if not transactions:
            parse_reason = (
                "empty_text"
                if not extracted_text.strip()
                else "no_matching_lines"
            )
            parse_status = "no_transactions"

        try:
            upload_record, _inserted = import_statement(
                user_id=user_id,
                original_file_name=display_name,
                stored_file_name=saved_name,
                file_fingerprint_sha256=text_fingerprint,
                parse_status=parse_status,
                parse_reason=parse_reason,
                currency=(
                    transactions[0].get("currency") if transactions else None
                ),
                transactions=transactions,
                statement_scope=text_fingerprint,
            )
        except DuplicateStatementUploadError:
            _reject_duplicate_upload(
                existing_upload=get_existing_upload_by_fingerprint(
                    user_id=user_id,
                    file_fingerprint_sha256=text_fingerprint,
                ),
                user_id=user_id,
                original_name=original_name,
                audit_context=audit_context,
            )
        _audit_after_commit(
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
    except BaseException:
        temp_path.unlink(missing_ok=True)
        saved_path.unlink(missing_ok=True)
        raise


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

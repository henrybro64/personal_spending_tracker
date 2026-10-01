from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal, TypedDict

import httpx

from backend.currency import DisplayCurrency
from backend.http_retry import ATTEMPT_TIMEOUT_SECONDS, request_with_retry
from backend.database import StoredTransaction, TimeRange
from backend.spending import (
    DisplayTransaction,
    UnconvertedTransaction,
    convertible_home_amount,
    partition_spending_transactions,
)

AiProvider = Literal["openai", "perplexity"]
InsightLanguage = Literal["en", "zh-Hant"]

SUPPORTED_AI_PROVIDERS: tuple[AiProvider, ...] = ("openai", "perplexity")
SUPPORTED_INSIGHT_RANGES: tuple[TimeRange, ...] = (
    "month",
    "quarter",
    "6months",
    "year",
)
DEFAULT_PROVIDER_MODELS: dict[AiProvider, str] = {
    "openai": "gpt-4.1",
    "perplexity": "sonar-pro",
}


class InsightSnapshotItem(TypedDict):
    label: str
    amount: float
    share: float | None
    count: int | None


class MonthlyInsightItem(TypedDict):
    month: str
    net: float
    spend: float
    refunds: float
    transaction_count: int


class LargeExpenseItem(TypedDict):
    date: str
    merchant: str
    amount: float
    currency: str
    category: str


class SpendingInsightSnapshot(TypedDict):
    range_key: TimeRange
    language: InsightLanguage
    display_currency: str
    transaction_count: int
    date_start: str | None
    date_end: str | None
    totals: dict[str, object]
    top_categories: list[InsightSnapshotItem]
    top_merchants: list[InsightSnapshotItem]
    recurring_merchants: list[InsightSnapshotItem]
    monthly_totals: list[MonthlyInsightItem]
    largest_expenses: list[LargeExpenseItem]
    merchant_concentration: dict[str, object]


class GeneratedInsightResult(TypedDict):
    provider: AiProvider
    model: str
    generated_at: str
    insight_text: str
    citations: list[str]


def _provider_http_timeout() -> httpx.Timeout:
    """Read budget matches one retry attempt. Connect failures stay short."""
    return httpx.Timeout(
        connect=10.0,
        read=ATTEMPT_TIMEOUT_SECONDS,
        write=20.0,
        pool=10.0,
    )


def default_model_for_provider(provider: AiProvider) -> str:
    return DEFAULT_PROVIDER_MODELS[provider]


def _choose_insight_display_currency(
    transactions: list[StoredTransaction],
) -> DisplayCurrency | None:
    totals = {"USD": 0.0, "TWD": 0.0}
    seen: set[DisplayCurrency] = set()
    for transaction in transactions:
        home = convertible_home_amount(transaction)
        if home is None:
            continue
        currency, amount = home
        totals[currency] += abs(amount)
        seen.add(currency)
    if not seen:
        return None
    if totals["TWD"] > totals["USD"]:
        return "TWD"
    if totals["USD"] > totals["TWD"] or "TWD" not in seen:
        return "USD"
    return "TWD"


_NO_CONVERTIBLE_SPENDING = (
    "No spending transactions can be shown in USD or TWD for the selected period."
)


def _insight_population(
    transactions: list[StoredTransaction],
) -> tuple[DisplayCurrency, list[DisplayTransaction], list[UnconvertedTransaction]]:
    display_currency = _choose_insight_display_currency(transactions)
    if display_currency is None:
        raise ValueError(_NO_CONVERTIBLE_SPENDING)
    model_rows, unconverted = partition_spending_transactions(
        transactions,
        display_currency,
    )
    if not model_rows:
        raise ValueError(_NO_CONVERTIBLE_SPENDING)
    return display_currency, model_rows, unconverted


def _prepare_insight_rows(
    model_rows: list[DisplayTransaction],
) -> tuple[list[dict[str, object]], dict[str, float], list[str]]:
    prepared_rows: list[dict[str, object]] = []
    abs_totals_by_currency: dict[str, float] = defaultdict(float)
    valid_dates: list[str] = []
    for transaction in model_rows:
        original_amount = float(transaction["original_amount"])
        original_currency = str(transaction["original_currency"])
        abs_totals_by_currency[original_currency] += abs(original_amount)
        transaction_date = str(transaction["date"])
        if _looks_like_iso_date(transaction_date):
            valid_dates.append(transaction_date)
            month_key = transaction_date[:7]
        else:
            month_key = "unknown"
        prepared_rows.append(
            {
                "amount": float(transaction["display_amount"]),
                "category": str(transaction["category"]),
                "date": transaction_date,
                "merchant": str(transaction["merchant"]),
                "month": month_key,
            }
        )
    return prepared_rows, abs_totals_by_currency, valid_dates


def build_spending_insight_snapshot(
    transactions: list[StoredTransaction],
    *,
    range_key: TimeRange,
    language: InsightLanguage,
) -> SpendingInsightSnapshot:
    if not transactions:
        raise ValueError("No transactions are available for the selected period.")

    display_currency, model_rows, unconverted = _insight_population(transactions)
    prepared_rows, abs_totals_by_currency, valid_dates = _prepare_insight_rows(
        model_rows
    )

    monthly_totals: dict[str, MonthlyInsightItem] = {}
    category_totals: dict[str, float] = defaultdict(float)
    merchant_totals: dict[str, float] = defaultdict(float)
    merchant_counts: dict[str, int] = defaultdict(int)
    merchant_months: dict[str, set[str]] = defaultdict(set)
    largest_expenses: list[LargeExpenseItem] = []
    net_total = 0.0
    spend_total = 0.0
    refund_total = 0.0

    for row in prepared_rows:
        display_amount = round(float(row["amount"]), 2)
        month_key = str(row["month"])
        if month_key not in monthly_totals:
            monthly_totals[month_key] = {
                "month": month_key,
                "net": 0.0,
                "spend": 0.0,
                "refunds": 0.0,
                "transaction_count": 0,
            }
        monthly_item = monthly_totals[month_key]
        monthly_item["net"] = round(monthly_item["net"] + display_amount, 2)
        monthly_item["transaction_count"] += 1
        net_total = round(net_total + display_amount, 2)

        merchant = str(row["merchant"])
        merchant_counts[merchant] += 1
        merchant_months[merchant].add(month_key)

        if display_amount >= 0:
            spend_total = round(spend_total + display_amount, 2)
            monthly_item["spend"] = round(monthly_item["spend"] + display_amount, 2)
            category_totals[str(row["category"])] += display_amount
            merchant_totals[merchant] += display_amount
            largest_expenses.append(
                {
                    "date": str(row["date"]),
                    "merchant": merchant,
                    "amount": display_amount,
                    "currency": display_currency,
                    "category": str(row["category"]),
                }
            )
        else:
            refund_total = round(refund_total + abs(display_amount), 2)
            monthly_item["refunds"] = round(
                monthly_item["refunds"] + abs(display_amount),
                2,
            )

    largest_expenses.sort(key=lambda item: item["amount"], reverse=True)
    largest_expenses = largest_expenses[:5]

    sorted_months = sorted(
        (
            item
            for item in monthly_totals.values()
            if item["month"] != "unknown"
        ),
        key=lambda item: item["month"],
    )
    if "unknown" in monthly_totals:
        sorted_months.append(monthly_totals["unknown"])

    top_categories = _top_snapshot_items(category_totals, spend_total)
    top_merchants = _top_snapshot_items(
        merchant_totals,
        spend_total,
        counts=merchant_counts,
    )
    recurring_merchants = _top_snapshot_items(
        {
            merchant: merchant_totals.get(merchant, 0.0)
            for merchant, months in merchant_months.items()
            if len(months) >= 2 and merchant_counts[merchant] >= 2
        },
        spend_total,
        counts=merchant_counts,
    )

    by_original_currency = [
        {
            "currency": currency,
            "absolute_total": round(total, 2),
        }
        for currency, total in sorted(abs_totals_by_currency.items())
    ]
    top_five_share = 0.0
    if spend_total > 0:
        top_five_share = round(
            sum(item["amount"] for item in top_merchants[:5]) / spend_total,
            4,
        )

    valid_dates.sort()
    return {
        "range_key": range_key,
        "language": language,
        "display_currency": display_currency,
        "transaction_count": len(prepared_rows),
        "date_start": valid_dates[0] if valid_dates else None,
        "date_end": valid_dates[-1] if valid_dates else None,
        "totals": {
            "net": net_total,
            "spend": spend_total,
            "refunds": refund_total,
            "by_original_currency": by_original_currency,
            "unconverted_transaction_count": len(unconverted),
            "unconverted_currencies": sorted(
                {item["original_currency"] for item in unconverted}
            ),
        },
        "top_categories": top_categories,
        "top_merchants": top_merchants,
        "recurring_merchants": recurring_merchants,
        "monthly_totals": sorted_months,
        "largest_expenses": largest_expenses,
        "merchant_concentration": {
            "distinct_merchants": len(merchant_counts),
            "top_5_share_of_spend": top_five_share,
        },
    }


async def generate_spending_insights(
    *,
    provider: AiProvider,
    api_key: str,
    model: str | None,
    snapshot: SpendingInsightSnapshot,
) -> GeneratedInsightResult:
    resolved_model = model.strip() if model and model.strip() else default_model_for_provider(provider)
    system_prompt, user_prompt = _build_prompts(snapshot)
    if provider == "openai":
        insight_text, citations = await _request_openai_insights(
            api_key=api_key,
            model=resolved_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    else:
        insight_text, citations = await _request_perplexity_insights(
            api_key=api_key,
            model=resolved_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            language=snapshot["language"],
        )

    return {
        "provider": provider,
        "model": resolved_model,
        "generated_at": datetime.now(UTC).isoformat(),
        "insight_text": insight_text,
        "citations": citations,
    }


def _looks_like_iso_date(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def _top_snapshot_items(
    totals: dict[str, float],
    total_spend: float,
    *,
    counts: dict[str, int] | None = None,
) -> list[InsightSnapshotItem]:
    items: list[InsightSnapshotItem] = []
    for label, amount in sorted(
        totals.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:8]:
        share = round(amount / total_spend, 4) if total_spend > 0 else None
        items.append(
            {
                "label": label,
                "amount": round(amount, 2),
                "share": share,
                "count": counts.get(label) if counts else None,
            }
        )
    return items


def _build_prompts(snapshot: SpendingInsightSnapshot) -> tuple[str, str]:
    language_name = (
        "Traditional Chinese"
        if snapshot["language"] == "zh-Hant"
        else "English"
    )
    system_prompt = (
        "You analyze personal spending snapshots for a budgeting app. "
        "Use only the provided data. "
        "Do not invent unsupported facts. "
        "Do not give tax, legal, or investment advice. "
        "Keep the tone direct and practical. "
        "Acknowledge currency caveats when the snapshot mixes currencies. "
        f"Write the final answer in {language_name}. "
        "Return plain text with exactly these section headings:\n"
        "Observations\n"
        "- ...\n"
        "Recommendations\n"
        "- ...\n"
        "Watchouts\n"
        "- ..."
    )
    user_prompt = (
        "Analyze this spending summary and produce concise insights for the user. "
        "Highlight major category changes, recurring merchants, unusual spending, "
        "merchant concentration, and realistic savings opportunities when the data supports them.\n\n"
        "Snapshot JSON:\n"
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


async def _post_openai(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> httpx.Response:
    return await request_with_retry(
        lambda: client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "store": False,
                "input": [
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": system_prompt},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_prompt},
                        ],
                    },
                ],
            },
        ),
        provider="OpenAI",
    )


async def _request_openai_insights(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, list[str]]:
    async with httpx.AsyncClient(timeout=_provider_http_timeout()) as client:
        response = await _post_openai(
            client,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    if response.status_code >= 400:
        raise RuntimeError(_provider_error_message("OpenAI", response))

    payload = response.json()
    texts: list[str] = []
    citations: list[str] = []

    direct_output_text = payload.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        texts.append(direct_output_text.strip())

    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            content_type = content.get("type")
            if content_type == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                for annotation in content.get("annotations", []):
                    if not isinstance(annotation, dict):
                        continue
                    url = annotation.get("url")
                    if isinstance(url, str) and url.strip():
                        citations.append(url.strip())
            elif content_type == "refusal":
                refusal = content.get("refusal")
                if isinstance(refusal, str) and refusal.strip():
                    texts.append(refusal.strip())

    insight_text = "\n\n".join(text for text in texts if text).strip()
    if not insight_text:
        raise RuntimeError("OpenAI returned an empty insights response.")

    return insight_text, _dedupe_preserve_order(citations)


async def _post_perplexity(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    language: InsightLanguage,
) -> httpx.Response:
    return await request_with_retry(
        lambda: client.post(
            "https://api.perplexity.ai/v1/sonar",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "disable_search": True,
                "language_preference": "zh" if language == "zh-Hant" else "en",
            },
        ),
        provider="Perplexity",
    )


async def _request_perplexity_insights(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    language: InsightLanguage,
) -> tuple[str, list[str]]:
    async with httpx.AsyncClient(timeout=_provider_http_timeout()) as client:
        response = await _post_perplexity(
            client,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            language=language,
        )

    if response.status_code >= 400:
        raise RuntimeError(_provider_error_message("Perplexity", response))

    payload = response.json()
    choices = payload.get("choices", [])
    insight_text = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                insight_text = content.strip()
        if not insight_text:
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    insight_text = content.strip()

    if not insight_text:
        raise RuntimeError("Perplexity returned an empty insights response.")

    citations = [
        url.strip()
        for url in payload.get("citations", [])
        if isinstance(url, str) and url.strip()
    ]
    return insight_text, _dedupe_preserve_order(citations)


def _provider_error_message(provider_name: str, response: httpx.Response) -> str:
    detail = response.text.strip()
    if len(detail) > 400:
        detail = detail[:400].rstrip()
    return f"{provider_name} request failed ({response.status_code}): {detail}"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped

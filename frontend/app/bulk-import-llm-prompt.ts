import type { AppLocale } from "./locale-provider";

const CATEGORY_OPTIONS = [
  "Payment / Credit",
  "Education / Certification",
  "Tax / Accounting",
  "Shopping / Department Store",
  "Shopping / Retail",
  "Shopping / Online Retail",
  "Subscription / Membership",
  "Government / Consular",
  "Insurance",
  "Groceries",
  "Dining / Food",
  "Transportation",
  "Travel",
  "Utilities / Telecom",
  "Healthcare / Medical",
  "Bank Fees / Interest",
  "Cash Advance / ATM",
  "Merchant Missing From PDF",
  "Other Spending",
] as const;

export function buildBulkImportLlmPrompt(options: {
  bank: string;
  currency: string;
  source: string;
  locale: AppLocale;
}): string {
  const bank = options.bank.trim() || "CTBC";
  const currency = options.currency.trim() || "TWD";
  const source = options.source.trim() || "statement-import";
  const categories = CATEGORY_OPTIONS.map((item) => `  - ${item}`).join("\n");

  if (options.locale === "zh-Hant") {
    return buildZhPrompt(bank, currency, source, categories);
  }
  return buildEnPrompt(bank, currency, source, categories);
}

function buildEnPrompt(
  bank: string,
  currency: string,
  source: string,
  categories: string
): string {
  return `You are converting credit-card statement text into JSON for a personal spending tracker import.

INPUT: I will paste raw text extracted from a PDF statement (often CTBC 中國信託, Taiwan). Parse only real purchase/charge rows. Skip headers, totals, credit limits, marketing text, and page numbers.

OUTPUT RULES (strict):
1. Return ONLY valid JSON — no markdown fences, no comments, no prose before or after.
2. Use this exact top-level shape:
{
  "source": "${source}",
  "bank": "${bank}",
  "currency": "${currency}",
  "transactions": [ ... ]
}
3. Each object in "transactions" must include:
   - date (required): ISO YYYY-MM-DD (convert ROC 民國 dates, e.g. 115/05/13 → 2026-05-13)
   - merchant (required): store/description as shown; do NOT invent names
   - amount (required): positive number for purchases/fees; use payment_or_credit for payments (see below)
   Optional fields:
   - posted_date: ISO YYYY-MM-DD if a separate posting date exists
   - card_last4: four digits if visible
   - category: pick ONE from the list below, or omit to let the app auto-categorize
   - transaction_type: "purchase" | "fee_or_interest" | "payment_or_credit"
   - exclude_from_spending: true for card payments/credits that are not new spending
4. Do not duplicate the same charge. Do not merge unrelated lines.
5. If the PDF line has date + amount but no merchant name, set merchant to "(merchant not in PDF)".
6. Installment notation (e.g. 12/12): keep merchant text as printed; do not split into future months unless the statement shows separate monthly rows.
7. Payments to the card (繳款, payment thank you, etc.): transaction_type "payment_or_credit", category "Payment / Credit", exclude_from_spending true.

Allowed categories (use exact spelling):
${categories}

Example output:
{
  "source": "${source}",
  "bank": "${bank}",
  "currency": "${currency}",
  "transactions": [
    {
      "date": "2026-05-13",
      "posted_date": "2026-05-15",
      "merchant": "IKEA 宜家家居",
      "amount": 813,
      "card_last4": "1234",
      "category": "Shopping / Online Retail",
      "transaction_type": "purchase"
    }
  ]
}

When ready, reply with the JSON only. I will paste the statement text in my next message.

--- STATEMENT TEXT BELOW (paste after this line) ---`;
}

function buildZhPrompt(
  bank: string,
  currency: string,
  source: string,
  categories: string
): string {
  return `請將信用卡帳單文字轉成「個人支出追蹤 App」可匯入的 JSON。

輸入：我會貼上從 PDF 擷取的文字（常為 CTBC 中國信託）。只解析真實消費/費用列，略過表頭、總計、信用額度、廣告與頁碼。

輸出規則（必須遵守）：
1. 只回傳合法 JSON，不要 markdown 圍欄、不要註解、不要前後說明文字。
2. 最外層格式固定為：
{
  "source": "${source}",
  "bank": "${bank}",
  "currency": "${currency}",
  "transactions": [ ... ]
}
3. transactions 陣列中每筆必含：
   - date（必填）：ISO YYYY-MM-DD（民國日期需轉西元，例 115/05/13 → 2026-05-13）
   - merchant（必填）：商家名稱照原文；不可捏造
   - amount（必填）：消費/費用為正數；繳款見下方 transaction_type
   選填：
   - posted_date：入帳日 ISO 日期
   - card_last4：卡號末四碼
   - category：從下方清單擇一，或可省略讓 App 自動分類
   - transaction_type："purchase" | "fee_or_interest" | "payment_or_credit"
   - exclude_from_spending：繳款/回饋等非新支出設 true
4. 勿重複同一筆。勿合併不相關列。
5. 若只有日期與金額、沒有商家，merchant 設為 "(merchant not in PDF)"。
6. 分期標示（如 12/12）保留在 merchant 文字中；除非帳單已列出各期獨立列，否則勿拆成多個月。
7. 繳款（繳款、Payment Thank You 等）：transaction_type 為 "payment_or_credit"，category 為 "Payment / Credit"，exclude_from_spending 為 true。

可用 category（請完全一致）：
${categories}

範例：
{
  "source": "${source}",
  "bank": "${bank}",
  "currency": "${currency}",
  "transactions": [
    {
      "date": "2026-05-13",
      "posted_date": "2026-05-15",
      "merchant": "IKEA 宜家家居",
      "amount": 813,
      "card_last4": "1234",
      "category": "Shopping / Online Retail",
      "transaction_type": "purchase"
    }
  ]
}

準備好後請只回 JSON。我會在下一則訊息貼上帳單文字。

--- 帳單文字貼在下方 ---`;
}

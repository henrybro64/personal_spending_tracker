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

  if (bank.toUpperCase() === "CTBC") {
    return buildCtbcPrompt(bank, currency, source);
  }
  if (options.locale === "zh-Hant") {
    return buildZhPrompt(bank, currency, source, categories);
  }
  return buildEnPrompt(bank, currency, source, categories);
}

function buildCtbcPrompt(
  bank: string,
  currency: string,
  source: string
): string {
  return `請將我上傳的 CTBC 信用卡 PDF 帳單轉成個人支出追蹤 App 可匯入的 JSON。

PDF password：若 PDF 有密碼，請使用我在同一則訊息中另外提供的密碼。不要猜測密碼。

重要要求：

你必須同時使用：

* PDF OCR 文字層
* PDF 頁面影像內容

不得只依賴 OCR。

若 OCR 與頁面影像衝突：

以頁面影像為準。

若 OCR 缺字、跑版、欄位錯位：

必須回頭查看 PDF 頁面影像確認交易內容。

────────────────────

商家名稱規則

* 商家名稱以帳單頁面實際顯示內容為準
* 不得自行補字
* 不得猜測完整店名
* 不得依據網路資料補充商家名稱
* 不得依據 Uber Eats、Google Maps 或其他資料來源推測完整名稱

例如帳單顯示：

優食-山水米餐酒

則直接輸出：

優食-山水米餐酒

例如帳單顯示：

優食-JAPOLI 義

則直接輸出：

優食-JAPOLI 義

例如帳單顯示：

優食-添好運 和億

則直接輸出：

優食-添好運 和億

若帳單本身已截斷：

保留截斷內容。

不得補字。

只有在：

* OCR 無法辨識
* 頁面影像也無法辨識

時才允許：

"(merchant not in PDF)"

────────────────────

解析規則

* 逐筆人工判讀交易列
* 不得只依靠 regex
* 不得依賴 OCR 欄位位置
* 保留帳單顯示的商家名稱
* 保留分期資訊
* 保留卡號末四碼
* 保留交易日
* 保留入帳日
* 保留退款與負數交易
* 保留國外交易手續費
* 保留調整與退款項目

────────────────────

日期格式

所有日期必須轉換成 ISO 8601：

113/05/14 → 2024-05-14

113/06/01 → 2024-06-01

不得輸出民國日期。

────────────────────

JSON Schema

{
  "source": "${source}",
  "bank": "${bank}",
  "currency": "${currency}",
  "card_counts": {
    "0051": 0,
    "6232": 0,
    "6764": 0,
    "3037": 0
  },
  "transactions": [
    {
      "date": "2024-06-01",
      "posted_date": "2024-06-03",
      "merchant": "Spotify P2CA51F9B3",
      "amount": 198,
      "card_last4": "0051",
      "country": "SE"
    }
  ]
}

注意：App 匯入欄位名稱是 "posted_date"，不得輸出 "post_date"。

────────────────────

每筆交易必須包含

* date
* posted_date
* merchant
* amount
* card_last4

country 為選填。

────────────────────

國別規則

若帳單顯示：

TW → "TW"
SG → "SG"
SE → "SE"
GB → "GB"
US → "US"
HK → "HK"
IE → "IE"

則輸出 country。

若帳單未顯示國別：

不要輸出 country 欄位。

────────────────────

負數交易規則

退款、折讓、調整項目：

amount 必須保留負號。

例如：

{
  "amount": -577
}

不得轉成正數。

不得刪除。

────────────────────

CTBC 特殊規則

下列交易通常會拆成兩筆：

* Disney PLUS
* Spotify
* Microsoft*Skype
* SM.NORD* PASSCOM
* 其他國外刷卡

即：

主交易

國外交易手續費

必須保留兩筆。

不得合併。

────────────────────

驗證步驟

1. 統計各卡號交易數量。card_counts 必須由實際抽出的交易列計算，不得猜測，也不得只抄帳單小計。

2. 建立 transactions。

3. 驗證：

sum(card_counts.values()) == transactions.length

4. 再逐一卡號確認 card_counts 與 transactions 中相同 card_last4 的實際筆數一致。

5. 若不相等：

停止輸出 JSON

改輸出：

ERROR_CARD_COUNT_MISMATCH

6. 若某張卡交易數異常偏少：

重新檢查該頁 PDF 影像。

────────────────────

頁面完整性驗證

對每一個含交易明細的 PDF 頁面，都必須同時檢查文字/OCR 與頁面影像。

每個含交易列的頁面都必須有至少一筆對應交易輸出；若該頁實際沒有交易列，才可標記為無交易頁。

若任何頁面肉眼可見交易列，但 transactions 中沒有該頁的交易，停止輸出並回報：

ERROR_MISSING_PAGE

────────────────────

輸出前再次確認以下交易是否存在：

* Spotify
* Disney PLUS
* Microsoft*Skype
* LeetCode
* Udemy
* 房屋稅
* eTag ASV-5598
* 悠遊卡加值
* 電信費
* 全聯
* 康是美
* Uber Eats（優食）
* 國外交易手續費
* 所有負數退款交易
* 所有分期交易

只有在帳單影像中確實存在時才輸出；此清單只用來做漏項檢查，不得憑空新增交易。

────────────────────

輸出規則

只輸出合法 JSON。

不要 markdown。

不要解釋。

不要摘要。

不要統計文字。

不要額外輸出驗證結果。

不要遺漏任何交易。

如果驗證失敗，只輸出指定錯誤字串 ERROR_CARD_COUNT_MISMATCH 或 ERROR_MISSING_PAGE。`;
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

import assert from "node:assert/strict";
import { test } from "node:test";

import { buildBulkImportLlmPrompt } from "../app/bulk-import-llm-prompt";

test("CTBC prompt requires OCR plus page-image verification", () => {
  const prompt = buildBulkImportLlmPrompt({
    bank: "CTBC",
    currency: "TWD",
    source: "ctbc-import",
    locale: "zh-Hant",
  });

  assert.match(prompt, /PDF OCR 文字層/);
  assert.match(prompt, /PDF 頁面影像內容/);
  assert.match(prompt, /以頁面影像為準/);
  assert.match(prompt, /ERROR_MISSING_PAGE/);
});

test("CTBC prompt uses importer-compatible posted_date and card count validation", () => {
  const prompt = buildBulkImportLlmPrompt({
    bank: "CTBC",
    currency: "TWD",
    source: "ctbc-import",
    locale: "en",
  });

  assert.match(prompt, /"posted_date"/);
  assert.doesNotMatch(prompt, /^\s*"post_date"\s*:/m);
  assert.match(prompt, /"card_counts"/);
  assert.match(prompt, /ERROR_CARD_COUNT_MISMATCH/);
  assert.match(prompt, /"1234"/);
  assert.match(prompt, /"5678"/);
  assert.match(prompt, /實際出現的所有卡號末四碼/);
  assert.doesNotMatch(prompt, /"0051"/);
  assert.doesNotMatch(prompt, /"6232"/);
  assert.doesNotMatch(prompt, /"6764"/);
  assert.doesNotMatch(prompt, /"3037"/);
});

test("CTBC prompt does not hard-code a PDF password", () => {
  const prompt = buildBulkImportLlmPrompt({
    bank: "CTBC",
    currency: "TWD",
    source: "ctbc-import",
    locale: "zh-Hant",
  });

  assert.match(prompt, /另外提供的密碼/);
  assert.doesNotMatch(prompt, /D121370565/);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  keepProviderSelection,
  mergeProviderDraft,
} from "../app/settings-drafts";

const typed = {
  apiKey: "sk-user-typing",
  model: "gpt-user",
  saving: false,
  removing: false,
};

test("a recovered settings load keeps a key and model the user is editing", () => {
  const kept = mergeProviderDraft(typed, "gpt-4.1", "gpt-4.1", false);
  assert.equal(kept.apiKey, "sk-user-typing");
  assert.equal(kept.model, "gpt-user");
});

test("a recovered settings load takes a new server model when the field is untouched", () => {
  const kept = mergeProviderDraft(
    { ...typed, apiKey: "", model: "gpt-4.1" },
    "gpt-4.2",
    "gpt-4.1",
    false
  );
  assert.equal(kept.apiKey, "");
  assert.equal(kept.model, "gpt-4.2");
});

test("saving clears the secret and uses the server model", () => {
  const cleared = mergeProviderDraft(typed, "gpt-4.1", "gpt-4.1", true);
  assert.equal(cleared.apiKey, "");
  assert.equal(cleared.model, "gpt-4.1");
});

test("the first settings load fills an empty draft from the server", () => {
  const filled = mergeProviderDraft(
    { apiKey: "", model: "", saving: false, removing: false },
    "gpt-4.1",
    null,
    false
  );
  assert.equal(filled.model, "gpt-4.1");
});

test("insight recovery keeps the provider the user already picked", () => {
  const providers = [
    { provider: "openai", configured: true },
    { provider: "perplexity", configured: true },
  ];
  assert.equal(keepProviderSelection("perplexity", providers, "openai"), "perplexity");
  assert.equal(keepProviderSelection("", providers, "openai"), "openai");
  assert.equal(
    keepProviderSelection("perplexity", [{ provider: "openai", configured: true }], "openai"),
    "openai"
  );
});

import assert from "node:assert/strict";
import test from "node:test";

import { TarkaApiError, TarkaClient } from "../dist/index.js";

test("sends authenticated constrained completion to proxy endpoint", async () => {
  let observed;
  const client = new TarkaClient({
    baseUrl: "https://proxy.example/private",
    apiKey: "test-secret",
    fetchImpl: async (url, init) => {
      observed = { url: String(url), init };
      return new Response('{"choices":[]}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await client.chatCompletions({
    model: "test-model",
    messages: [{ role: "user", content: "Check the plan." }],
    logic_constraints: {
      rules: ["amount <= limit"],
      bindings: { limit: 10 },
    },
  });

  assert.deepEqual(result, { choices: [] });
  assert.equal(observed.url, "https://proxy.example/v1/chat/completions");
  assert.equal(observed.init.method, "POST");
  assert.equal(observed.init.headers.Authorization, "Bearer test-secret");
  assert.equal(
    JSON.parse(observed.init.body).logic_constraints.rules[0],
    "amount <= limit",
  );
});

test("surfaces proxy errors without returning an unverified response", async () => {
  const client = new TarkaClient({
    baseUrl: "https://proxy.example",
    apiKey: "test-secret",
    fetchImpl: async () => new Response('{"error":{"type":"logic_violation"}}', { status: 422 }),
  });

  await assert.rejects(
    client.chatCompletions({ model: "test-model", messages: [] }),
    (error) =>
      error instanceof TarkaApiError &&
      error.status === 422 &&
      error.responseBody.includes("logic_violation"),
  );
});

test("rejects remote HTTP and empty API keys", () => {
  assert.throws(
    () => new TarkaClient({ baseUrl: "http://proxy.example", apiKey: "key" }),
    /HTTPS/,
  );
  assert.throws(
    () => new TarkaClient({ baseUrl: "https://proxy.example", apiKey: "" }),
    /apiKey/,
  );
});

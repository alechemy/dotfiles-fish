import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const piRoot = process.env.PI_PACKAGE_ROOT;
const fragment = JSON.parse(readFileSync(new URL("../../stow/pi/.pi/agent/models.fragment.json", import.meta.url)));
const id = "Qwen3.8-27B-oQ8e-mtp";

test("Pi sends explicit Qwen thinking controls", {
  skip: !piRoot && "Set PI_PACKAGE_ROOT to the installed Pi package",
  timeout: 10000,
}, async () => {
  const { streamSimple } = await import(pathToFileURL(join(piRoot,
    "node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js")));
  const model = {
    id, name: id, provider: "omlx", api: "openai-completions",
    baseUrl: "http://127.0.0.1:1/v1", input: ["text"],
    contextWindow: 262144, maxTokens: 32768,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    ...fragment.providers.omlx.modelOverrides[id],
  };
  const context = {
    systemPrompt: "Return the requested fixture value.",
    messages: [{ role: "user", content: "Read the fixture.", timestamp: 0 }],
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("Network access is forbidden"); };
  try {
    for (const reasoning of [undefined, "off", "high"]) {
      let calls = 0;
      const result = await streamSimple(model, context, {
        apiKey: "fixture", reasoning, maxRetries: 0,
        fetch: async (_url, options) => {
          calls++;
          const payload = JSON.parse(options.body);
          assert.deepEqual(payload.chat_template_kwargs, {
            enable_thinking: reasoning === "high", preserve_thinking: true,
          });
          assert.equal(payload.messages[0].role, "system");
          const chunk = { id: "fixture", choices: [{
            index: 0, delta: { content: "fixture" }, finish_reason: "stop",
          }] };
          return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`, {
            headers: { "Content-Type": "text/event-stream" },
          });
        },
      }).result();
      assert.equal(calls, 1);
      assert.equal(result.stopReason, "stop", result.errorMessage);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const piRoot = process.env.PI_PACKAGE_ROOT;
const extensionRoot = process.env.PI_ANTHROPIC_AUTH_ROOT;
const version = "3.3.1";
const versionVariable = "PI_ANTHROPIC_AUTH_CLAUDE_CODE_VERSION";
const fishEnvironment = readFileSync(new URL("../../stow/fish/.config/fish/conf.d/env.fish", import.meta.url), "utf8");

function textBlocks(content) {
  return typeof content === "string" ? content : content.map((block) => block.text ?? "").join("\n");
}

function billingVersion(payload) {
  return textBlocks(payload.system).match(/cc_version=(\d+\.\d+\.\d+)\.[a-f0-9]{3};/)?.[1];
}

function compareVersions(left, right) {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
}

function errorResponse(requiredVersion) {
  const error = requiredVersion ? {
    type: "invalid_request_error",
    message: `Claude Code 2.1.260 does not support this model; version ${requiredVersion} or newer is required.`,
    details: { error_code: "claude_code_version_too_old" },
  } : { type: "fixture", message: "Offline fixture" };
  return new Response(JSON.stringify({ error }), {
    status: 400, headers: { "Content-Type": "application/json" },
  });
}

test("Anthropic auth is pinned without a fixed compatibility-version override", () => {
  const fragment = JSON.parse(readFileSync(new URL("../../stow/pi/.pi/agent/settings.fragment.json", import.meta.url), "utf8"));
  assert.ok(fragment.packages.includes(`npm:@gotgenes/pi-anthropic-auth@${version}`));
  assert.doesNotMatch(fishEnvironment, /^set\s+-gx\s+PI_ANTHROPIC_AUTH_CLAUDE_CODE_VERSION\s/m);
});

test("Anthropic auth uses Pi's current transcript and transport contracts", {
  skip: (!piRoot || !extensionRoot) && "Set PI_PACKAGE_ROOT and PI_ANTHROPIC_AUTH_ROOT to reviewed packages",
  timeout: 20000,
}, async (t) => {
  const loaderUrl = pathToFileURL(resolve(piRoot, "dist/core/extensions/loader.js"));
  const entry = resolve(extensionRoot, "src/index.ts");
  const home = mkdtempSync(join(tmpdir(), "pi-anthropic-auth-test-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let networkAttempts = 0;
  try {
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, {
      HOME: home, PATH: environment.PATH, PI_CODING_AGENT_DIR: join(home, ".pi/agent"),
      PI_OFFLINE: "1", PI_TELEMETRY: "0",
    });
    process.chdir(home);
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    const { loadExtensions } = await import(loaderUrl);
    const loaded = await loadExtensions([entry], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    const extension = loaded.extensions[0];
    assert.equal(extension.tools.size, 0);
    assert.equal(loaded.runtime.pendingProviderRegistrations.length, 1);
    const { name, config } = loaded.runtime.pendingProviderRegistrations[0];
    assert.equal(name, "anthropic");
    assert.deepEqual(Object.keys(config).sort(), ["api", "streamSimple"]);
    assert.equal(config.api, "anthropic-messages");
    let diagnostics;
    await extension.commands.get("anthropic-auth:status").handler("", {
      hasUI: true, ui: { notify: (text) => { diagnostics = text; } },
    });
    assert.ok(diagnostics.includes(`version: ${version}`));
    assert.match(diagnostics, /built-in Anthropic transport: resolved/);
    assert.ok(diagnostics.includes(entry));

    const model = {
      id: "claude-haiku-4-5", name: "Fixture", provider: "anthropic", api: "anthropic-messages",
      baseUrl: "http://127.0.0.1:1", input: ["text"], reasoning: false,
      contextWindow: 200000, maxTokens: 4096,
      compat: { supportsMidConvoSystemMessages: true },
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    };
    const { buildSystemPromptState } = await import(pathToFileURL(resolve(piRoot, "dist/core/system-prompt.js")));
    const prompt = buildSystemPromptState({
      cwd: home,
      selectedTools: ["read"],
      toolSnippets: { read: "Read a fixture file" },
      contextFiles: [{ path: "/fixture/AGENTS.md", content: "Preserve this fictional project instruction." }],
      promptGuidelines: ["Never publish fixture data."],
      appendSystemPrompt: "Preserve this appended instruction.",
      sections: { fixture: "Preserve this custom section." },
    });
    const context = {
      messages: [
        { role: "system", ...prompt, timestamp: 0 },
        { role: "user", content: "Return a fictional fixture.", timestamp: 0 },
        { role: "system", content: "", sections: {
          docs: prompt.sections.docs,
          rules: "<rules>\nPreserve this updated fixture rule.\n</rules>",
        }, timestamp: 1 },
        { role: "user", content: "Apply the updated rule.", timestamp: 2 },
      ],
    };
    const originalContext = structuredClone(context);
    for (const oauth of [false, true]) {
      await t.test(oauth ? "OAuth shapes initial and updated system messages" : "API-key requests preserve system messages", async () => {
        let captured;
        let userAgent;
        let payloadCallbacks = 0;
        let fetchCalls = 0;
        const result = await config.streamSimple(model, context, {
          apiKey: oauth ? "sk-ant-oat-fixture" : "fixture-api-key", maxRetries: 0,
          onPayload: (payload) => { payloadCallbacks++; return { ...payload, max_tokens: 123 }; },
          fetch: async (_url, options) => {
            fetchCalls++;
            captured = JSON.parse(options.body);
            userAgent = new Headers(options.headers).get("user-agent");
            return errorResponse();
          },
        }).result();
        assert.equal(payloadCallbacks, 1);
        assert.equal(fetchCalls, 1);
        assert.equal(captured.max_tokens, 123);
        assert.equal(result.stopReason, "error");
        assert.match(result.errorMessage, /Offline fixture/);
        const systemText = textBlocks(captured.system);
        for (const section of ["project_context", "rules", "addendum", "fixture", "cwd"]) {
          assert.ok(systemText.includes(prompt.sections[section]), section);
        }
        assert.equal(systemText.includes(prompt.sections.docs), !oauth);
        assert.equal(systemText.includes(prompt.sections.preamble), !oauth);
        assert.ok(systemText.includes("Read a fixture file"));
        const update = captured.messages.find((message) => message.role === "system");
        assert.ok(update);
        const updateText = textBlocks(update.content);
        assert.ok(updateText.includes("Preserve this updated fixture rule."));
        assert.equal(updateText.includes(prompt.sections.docs), !oauth);
        const reported = billingVersion(captured);
        assert.equal(Boolean(reported), oauth);
        if (oauth) {
          assert.ok(compareVersions(reported, "2.1.280") >= 0);
          const hostVersion = userAgent?.match(/claude-cli\/(\d+\.\d+\.\d+)/)?.[1];
          assert.ok(hostVersion);
          assert.ok(compareVersions(reported, hostVersion) >= 0);
        }
        assert.deepEqual(context, originalContext);
      });
    }

    await t.test("Models without mid-conversation system support retain the current instructions", async () => {
      let captured;
      await config.streamSimple({ ...model, compat: { supportsMidConvoSystemMessages: false } }, context, {
        apiKey: "sk-ant-oat-fixture", maxRetries: 0,
        fetch: async (_url, options) => {
          captured = JSON.parse(options.body);
          return errorResponse();
        },
      }).result();
      const systemText = textBlocks(captured.system);
      assert.ok(systemText.includes(prompt.sections.project_context));
      assert.ok(systemText.includes("Preserve this updated fixture rule."));
      assert.ok(!systemText.includes(prompt.sections.docs));
      assert.ok(!captured.messages.some((message) => message.role === "system"));
    });

    await t.test("Version-floor recovery retries once and remembers the required version", async () => {
      const versions = [];
      let payloadCallbacks = 0;
      const result = await config.streamSimple(model, context, {
        apiKey: "sk-ant-oat-fixture", maxRetries: 0,
        onPayload: (payload) => { payloadCallbacks++; return payload; },
        fetch: async (_url, options) => {
          versions.push(billingVersion(JSON.parse(options.body)));
          return errorResponse(versions.length === 1 ? "99.0.0" : "100.0.0");
        },
      }).result();
      assert.equal(payloadCallbacks, 1);
      assert.equal(versions.length, 2);
      assert.ok(compareVersions(versions[0], "99.0.0") < 0);
      assert.equal(versions[1], "99.0.0");
      assert.equal(result.stopReason, "error");
      assert.match(result.errorMessage, /Automatic recovery did not succeed/);
      let nextVersion;
      await config.streamSimple(model, context, {
        apiKey: "sk-ant-oat-fixture", maxRetries: 0,
        fetch: async (_url, options) => {
          nextVersion = billingVersion(JSON.parse(options.body));
          return errorResponse();
        },
      }).result();
      assert.equal(nextVersion, "100.0.0");
    });

    await t.test("An explicit override disables automatic version recovery", async () => {
      process.env[versionVariable] = "2.1.280";
      try {
        const versions = [];
        const result = await config.streamSimple(model, context, {
          apiKey: "sk-ant-oat-fixture", maxRetries: 0,
          fetch: async (_url, options) => {
            versions.push(billingVersion(JSON.parse(options.body)));
            return errorResponse("99.0.0");
          },
        }).result();
        assert.deepEqual(versions, ["2.1.280"]);
        assert.match(result.errorMessage, /sent verbatim; raise it/);
      } finally {
        delete process.env[versionVariable];
      }
    });
    assert.deepEqual(context, originalContext);
    assert.equal(networkAttempts, 0);
  } finally {
    globalThis.fetch = originalFetch;
    process.chdir(cwd);
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

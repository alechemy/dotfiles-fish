import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { BudgetLedger } from "../../stow/pi/.pi/agent/extensions/copilot-guard/ledger.mjs";
import { capModel, CREDIT_SCALE, ratesFor, requestUnits, usageUnits, validateConfig } from "../../stow/pi/.pi/agent/extensions/copilot-guard/policy.mjs";
import { wrapProvider } from "../../stow/pi/.pi/agent/extensions/copilot-guard/provider.mjs";

const root = new URL("../../stow/pi/.pi/agent/extensions/copilot-guard/", import.meta.url);
const config = validateConfig(JSON.parse(readFileSync(new URL("config.json", root), "utf8")));
const model = {
  id: "claude-opus-5", name: "fixture", api: "anthropic-messages", provider: "github-copilot",
  contextWindow: 272000, maxTokens: 128000,
  input: ["text"], reasoning: true, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
const rates = ratesFor(model);
const piRoot = process.env.PI_PACKAGE_ROOT;
const subagentsRoot = process.env.PI_SUBAGENTS_ROOT;

async function fixture(run, overrides = {}) {
  const directory = mkdtempSync(join(tmpdir(), "copilot-budget-"));
  try { return await run(new BudgetLedger(directory, { ...config, ...overrides }), directory); }
  finally { rmSync(directory, { recursive: true, force: true }); }
}

function unitsUsage(input = 1000, output = 1000) {
  return { input, output, cacheRead: 0, cacheWrite: 0, totalTokens: input + output,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
}

test("ceilings never increase smaller windows and leave other providers unchanged", () => {
  assert.equal(capModel({ ...model, contextWindow: 1000000 }, config).contextWindow, 272000);
  assert.equal(capModel({ ...model, contextWindow: 128000 }, config).contextWindow, 128000);
  assert.equal(capModel({ ...model, id: "future-model", contextWindow: 2000000 }, config).contextWindow, 272000);
  const codex = { ...model, provider: "openai-codex", contextWindow: 1000000 };
  assert.equal(capModel(codex, config), codex);
  for (const key of ["dailyCredits", "sessionCredits", "contextCeiling"]) {
    assert.throws(() => validateConfig({ ...config, [key]: 0 }), /invalid/);
  }
});

test("credit estimates include cached input, writes, output and a conservative margin", () => {
  assert.equal(usageUnits({ input: 10000, output: 1000, cacheRead: 10000, cacheWrite: 10000 }, rates, 1), 15.5 * CREDIT_SCALE);
  assert.equal(usageUnits(unitsUsage(), rates, 1.1), 3.4375 * CREDIT_SCALE);
  assert.equal(usageUnits(unitsUsage(0, 0), rates, 1.1), undefined);
  assert.equal(usageUnits({ ...unitsUsage(), output: NaN }, rates, 1.1), undefined);
  assert.throws(() => ratesFor({ ...model, id: "future-model" }), /pricing.*blocked/);
  assert.equal(ratesFor({ ...model, id: "gpt-5.6-luna" }).input, 0.4);
  assert.equal(ratesFor({ ...model, id: "grok-4.7" }).output, 12);
});

test("daily cap is shared across sessions, survives restarts, and resets only at UTC midnight", async () => {
  await fixture(async (ledger, directory) => {
    let now = new Date("2026-10-01T23:59:00Z");
    ledger.now = () => now;
    const first = await ledger.reserve("session-a", model, {}, rates);
    await ledger.settle(first, 1550 * CREDIT_SCALE);
    await assert.rejects(ledger.reserve("session-b", model, {}, rates), /budget stopped/);
    const restarted = new BudgetLedger(directory, config, () => now);
    await assert.rejects(restarted.reserve("session-b", model, {}, rates), /budget stopped/);
    now = new Date("2026-10-02T00:00:00Z");
    await restarted.reserve("session-b", model, {}, rates);
    await assert.rejects(restarted.reserve("session-a", model, {}, rates), /budget stopped/);
    assert.equal(statSync(ledger.path).mode & 0o777, 0o600);
    const saved = readFileSync(ledger.path, "utf8");
    assert.ok(!saved.includes("session-a"));
  });
});

test("enforcement defaults on; disabling persists across processes without resetting charges", async () => {
  await fixture(async (ledger, directory) => {
    assert.equal((await ledger.status("parent")).enforcementEnabled, true);
    const first = await ledger.reserve("parent", model, {}, rates);
    await ledger.settle(first, 1550 * CREDIT_SCALE);
    await ledger.setEnforcement(false);
    const restarted = new BudgetLedger(directory, config);
    assert.equal((await restarted.status("parent")).enforcementEnabled, false);
    const next = await restarted.reserve("parent", model, {}, rates);
    assert.equal(next.maxTokens, model.maxTokens);
    const small = await ledger.reserve("child", model, { maxTokens: 1 }, rates);
    assert.equal(small.maxTokens, 1);
    await restarted.settle(next, 3 * CREDIT_SCALE);
    await ledger.setEnforcement(true);
    const status = await restarted.status("parent");
    assert.equal(status.enforcementEnabled, true);
    assert.equal(status.session, 1553 * CREDIT_SCALE);
    assert.equal(status.daily, 1553 * CREDIT_SCALE + small.units);
    assert.equal(status.reservations, 1);
    await assert.rejects(restarted.reserve("child", model, {}, rates), /budget stopped/);
    await restarted.settle(small, undefined);
    await assert.rejects(ledger.setEnforcement("false"), /invalid.*enforcement/);
    const state = JSON.parse(readFileSync(ledger.path, "utf8"));
    state.enforcementEnabled = "false";
    writeFileSync(ledger.path, JSON.stringify(state));
    await assert.rejects(ledger.reserve("parent", model, {}, rates), /invalid budget ledger/);
    await assert.rejects(ledger.setEnforcement(false), /invalid budget ledger/);
  });
});

test("parallel reservations cannot allocate the same credits and abandoned requests remain charged", async () => {
  await fixture(async (ledger, directory) => {
    const results = await Promise.allSettled(Array.from({ length: 8 }, (_, index) =>
      new BudgetLedger(directory, config).reserve(`session-${index}`, model, {}, rates)));
    const successful = results.filter((result) => result.status === "fulfilled");
    assert.ok(successful.length >= 1);
    assert.ok(successful.length < 8);
    const reserved = successful.reduce((sum, result) => sum + result.value.units, 0);
    assert.ok(reserved <= 1550 * CREDIT_SCALE);
    const status = await new BudgetLedger(directory, config).status("new-session");
    assert.equal(status.daily, reserved);
    assert.equal(status.reservations, successful.length);
  });
});

test("cross-process reservations cannot overspend", async () => {
  await fixture(async (ledger, directory) => {
    const script = `
      import { BudgetLedger } from ${JSON.stringify(new URL("ledger.mjs", root).href)};
      const ledger = new BudgetLedger(process.argv[1], ${JSON.stringify(config)});
      try {
        const result = await ledger.reserve(process.argv[2], ${JSON.stringify(model)}, {}, ${JSON.stringify(rates)});
        console.log(JSON.stringify({ units: result.units }));
      } catch { console.log(JSON.stringify({ units: 0 })); }
    `;
    const children = Array.from({ length: 8 }, (_, index) => new Promise((resolve, reject) => {
      const child = spawn(process.execPath, ["--input-type=module", "-e", script, directory, `worker-${index}`]);
      let text = "";
      child.stdout.on("data", (data) => { text += data; });
      child.on("error", reject);
      child.on("exit", (code) => code === 0 ? resolve(JSON.parse(text)) : reject(new Error(`Worker exit ${code}`)));
    }));
    const results = await Promise.all(children);
    const allocated = results.reduce((sum, result) => sum + result.units, 0);
    assert.ok(allocated > 0 && allocated <= 1550 * CREDIT_SCALE);
    assert.equal((await ledger.status("fixture")).daily, allocated);
  });
});

test("usage replaces reservations; missing usage charges the full reservation; corrupt state blocks", async () => {
  await fixture(async (ledger) => {
    const reservation = await ledger.reserve("fixture", model, {}, rates);
    await ledger.settle(reservation, usageUnits(unitsUsage(), rates, config.estimateMargin));
    assert.equal((await ledger.status("fixture")).daily, 3.4375 * CREDIT_SCALE);
    const missing = await ledger.reserve("fixture", model, {}, rates);
    await ledger.settle(missing, undefined);
    assert.equal((await ledger.status("fixture")).daily, missing.units + 3.4375 * CREDIT_SCALE);
    writeFileSync(ledger.path, '{"version":1,"days":{},"sessions":{},"reservations":{"bad":{"units":-1}}}');
    await assert.rejects(ledger.reserve("fixture", model, {}, rates), /invalid budget ledger/);
  });
});

test("one-token cache refreshes preserve their requested output limit", async () => {
  await fixture(async (ledger) => {
    const reservation = await ledger.reserve("fixture", model, { maxTokens: 1 }, rates);
    assert.equal(reservation.maxTokens, 1);
  });
});

test("output limits fit the remaining reserved budget", async () => {
  await fixture(async (ledger) => {
    const first = await ledger.reserve("fixture", model, {}, rates);
    await ledger.settle(first, 1200 * CREDIT_SCALE);
    const next = await ledger.reserve("fixture", model, {}, rates);
    assert.ok(next.maxTokens < model.maxTokens);
    assert.equal(next.units, requestUnits(model, next.maxTokens, rates, config.estimateMargin));
    assert.ok(next.units <= 350 * CREDIT_SCALE);
  });
});

test("guard forwards streams and blocks the transport, including compaction and cache refresh calls", {
  skip: !piRoot && "Set PI_PACKAGE_ROOT to the installed Pi package",
}, async () => {
  const { createAssistantMessageEventStream } = await import(pathToFileURL(join(piRoot, "node_modules/@earendil-works/pi-ai/dist/index.js")));
  await fixture(async (ledger) => {
    let requests = 0;
    let requestOptions;
    const base = {
      id: "github-copilot", auth: {},
      getModels: () => [{ ...model, contextWindow: 1000000 }],
      stream(requestModel, _context, options) {
        requests++;
        requestOptions = options;
        assert.equal(requestModel.contextWindow, 272000);
        assert.equal(requestModel.maxTokens, options.maxTokens);
        const stream = createAssistantMessageEventStream();
        const message = { role: "assistant", content: [{ type: "text", text: "fixture" }],
          ...model, model: model.id, usage: unitsUsage(), stopReason: "stop", timestamp: 0 };
        stream.push({ type: "start", partial: message });
        stream.push({ type: "done", reason: "stop", message });
        stream.end();
        return stream;
      },
      streamSimple(...args) { return this.stream(...args); },
    };
    const guarded = wrapProvider(base, ledger, config, createAssistantMessageEventStream, () => "parent");
    const guardedAgain = wrapProvider(guarded, ledger, config, createAssistantMessageEventStream, () => "parent");
    assert.equal(guardedAgain.getModels()[0].contextWindow, 272000);
    const context = { messages: [] };
    const events = [];
    for await (const event of guardedAgain.streamSimple(model, context, { sessionId: "child-or-summary-routing-id" })) events.push(event.type);
    assert.deepEqual(events, ["start", "done"]);
    assert.equal(requestOptions.maxRetries, 0);
    assert.equal((await ledger.status("parent")).daily, 3.4375 * CREDIT_SCALE);
    const remaining = await ledger.reserve("parent", model, {}, rates);
    await ledger.settle(remaining, 1550 * CREDIT_SCALE);
    for (const method of ["stream", "streamSimple"]) {
      const stopped = await guarded[method](model, context, { sessionId: "compaction-or-cache-refresh" }).result();
      assert.equal(stopped.stopReason, "error");
      assert.match(stopped.errorMessage, /budget stopped/);
    }
    assert.equal(requests, 1);
    await ledger.setEnforcement(false);
    const continued = await guarded.stream(model, context).result();
    assert.equal(continued.stopReason, "stop");
    assert.equal(requests, 2);
    assert.equal(requestOptions.maxTokens, model.maxTokens);
    assert.equal(requestOptions.maxRetries, 0);
    assert.equal((await ledger.status("parent")).session, 1556.875 * CREDIT_SCALE);
    const unknown = await guarded.stream({ ...model, id: "future-model" }, context).result();
    assert.match(unknown.errorMessage, /pricing.*blocked/);
    assert.equal(requests, 2);
    await ledger.setEnforcement(true);
    assert.match((await guarded.stream(model, context).result()).errorMessage, /budget stopped/);
    assert.equal(requests, 2);
  });
});

test("only successful provider completion reconciles reported usage", {
  skip: !piRoot && "Set PI_PACKAGE_ROOT to the installed Pi package",
}, async () => {
  const { createAssistantMessageEventStream } = await import(pathToFileURL(join(piRoot, "node_modules/@earendil-works/pi-ai/dist/index.js")));
  for (const method of ["stream", "streamSimple"]) {
    for (const outcome of ["error", "aborted", "throw", "incomplete", "done"]) {
      await fixture(async (ledger) => {
        const message = { role: "assistant", content: [], api: model.api,
          provider: model.provider, model: model.id, usage: unitsUsage(),
          stopReason: outcome === "done" ? "stop" : "error", timestamp: 0 };
        const base = {
          getModels: () => [model],
          async *stream() {
            yield { type: "start", partial: message };
            if (outcome === "throw") throw new Error("fixture interruption");
            if (outcome === "incomplete") return;
            if (outcome === "done") yield { type: "done", reason: "stop", message };
            else yield { type: "error", reason: outcome,
              error: { ...message, stopReason: outcome, errorMessage: "fixture interruption" } };
          },
          streamSimple(...args) { return this.stream(...args); },
        };
        const guarded = wrapProvider(base, ledger, config, createAssistantMessageEventStream, () => "fixture");
        const events = [];
        for await (const event of guarded[method](model, { messages: [] })) events.push(event);
        const expected = outcome === "done" ? usageUnits(message.usage, rates, config.estimateMargin) :
          requestUnits(model, model.maxTokens, rates, config.estimateMargin);
        const status = await ledger.status("fixture");
        assert.equal(status.daily, expected, `${method}/${outcome}: daily`);
        assert.equal(status.session, expected, `${method}/${outcome}: session`);
        assert.equal(events.at(-1).type, outcome === "done" ? "done" : "error");
        if (outcome === "error" || outcome === "aborted") assert.equal(events.at(-1).reason, outcome);
      });
    }
  }
});

test("Pi SDK installs the native guard before prompting, without changing other providers", {
  skip: !piRoot && "Set PI_PACKAGE_ROOT to the installed Pi package",
  timeout: 20000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-copilot-sdk-"));
  const environment = { ...process.env };
  const originalFetch = globalThis.fetch;
  let session;
  try {
    const agentDir = join(home, ".pi/agent");
    mkdirSync(agentDir, { recursive: true });
    process.env.HOME = home;
    process.env.PI_CODING_AGENT_DIR = agentDir;
    process.env.PI_OFFLINE = "1";
    delete process.env.COPILOT_GITHUB_TOKEN;
    delete process.env.PI_COPILOT_BUDGET_OWNER;
    globalThis.fetch = () => { throw new Error("Network access is forbidden"); };
    const sdk = await import(pathToFileURL(join(piRoot, "dist/index.js")));
    const ai = await import(pathToFileURL(join(piRoot, "node_modules/@earendil-works/pi-ai/dist/index.js")));
    const runtime = await sdk.ModelRuntime.create({ modelsPath: null, refreshOnCreate: false });
    const faux = ai.fauxProvider({ provider: "github-copilot", models: [{ ...model, contextWindow: 1000000 }] });
    faux.setResponses([ai.fauxAssistantMessage("fixture")]);
    runtime.registerNativeProvider(faux.provider);
    await runtime.refresh({ allowNetwork: false });
    const otherWindow = runtime.getModel("openai-codex", "gpt-6.1-sol").contextWindow;
    const settings = sdk.SettingsManager.inMemory({ compaction: { enabled: false }, cacheWarming: "off",
      modelThinkingLevels: { "github-copilot/claude-opus-5": "high" } });
    const loader = new sdk.DefaultResourceLoader({
      cwd: home, agentDir, settingsManager: settings, noExtensions: true, noSkills: true,
      noPromptTemplates: true, noThemes: true, noContextFiles: true,
      additionalExtensionPaths: [new URL("index.ts", root).pathname],
    });
    await loader.reload();
    assert.deepEqual(loader.getExtensions().errors, []);
    assert.equal(loader.getExtensions().extensions.length, 1);
    const created = await sdk.createAgentSession({
      cwd: home, agentDir, modelRuntime: runtime, model: runtime.getModel("github-copilot", model.id),
      sessionManager: sdk.SessionManager.inMemory(home), settingsManager: settings,
      resourceLoader: loader, noTools: true, thinkingLevel: "off",
    });
    session = created.session;
    await session.bindExtensions({ onError: (error) => assert.fail(error.error) });
    assert.equal(session.model.contextWindow, 272000);
    assert.equal(runtime.getModel("openai-codex", "gpt-6.1-sol").contextWindow, otherWindow);
    assert.equal(runtime.getModel("github-copilot", model.id).contextWindow, 272000);
    await session.setModel({ ...session.model, contextWindow: 1000000 });
    session.setThinkingLevel("off");
    await session.prompt("Return the fixture.");
    assert.equal(session.model.contextWindow, 272000);
    assert.equal(session.thinkingLevel, "off");
    assert.equal(faux.state.callCount, 1);
    const ledger = new BudgetLedger(join(agentDir, "copilot-budget"), config);
    const reservation = await ledger.reserve(session.sessionId, model, {}, rates);
    await ledger.settle(reservation, 1550 * CREDIT_SCALE);
    await session.prompt("This request must not reach the provider.");
    assert.equal(faux.state.callCount, 1);
    const last = session.messages.filter((message) => message.role === "assistant").at(-1);
    assert.equal(last.stopReason, "error");
    assert.match(last.errorMessage, /budget stopped/);
    await session.reload();
    await session.prompt("Reload must not reset the budget.");
    assert.equal(faux.state.callCount, 1);
    if (subagentsRoot) {
      const fragment = JSON.parse(readFileSync(new URL("../../stow/pi/.pi/agent/settings.fragment.json", import.meta.url), "utf8"));
      mkdirSync(join(agentDir, "extensions"));
      symlinkSync(root.pathname, join(agentDir, "extensions/copilot-guard"));
      writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ subagents: fragment.subagents, cacheWarming: "off" }));
      const require = createRequire(join(piRoot, "package.json"));
      const { createJiti } = require("jiti");
      const jiti = createJiti(import.meta.url, { fsCache: false, alias: {
        "@earendil-works/pi-ai": join(piRoot, "node_modules/@earendil-works/pi-ai/dist/index.js"),
        "@earendil-works/pi-coding-agent": join(piRoot, "dist/index.js"),
      } });
      const { discoverAgents } = await jiti.import(join(subagentsRoot, "src/agents/agents.ts"));
      const discovery = discoverAgents(home, "user", "github-copilot");
      for (const name of ["delegate", "reviewer", "worker", "researcher", "oracle"]) {
        const agent = discovery.agents.find((agent) => agent.name === name);
        assert.ok(agent.extensions.some((path) => path.endsWith("/extensions/copilot-guard")), name);
      }
      for (const name of ["scout", "local-editor"]) {
        assert.deepEqual(discovery.agents.find((agent) => agent.name === name).extensions, []);
      }
      const { createDefaultChildSessionFactory } = await jiti.import(join(subagentsRoot, "src/runs/shared/child-session.ts"));
      const state = JSON.parse(readFileSync(ledger.path, "utf8"));
      state.days = { "2000-01-01": Object.values(state.days).reduce((sum, value) => sum + value, 0) };
      writeFileSync(ledger.path, JSON.stringify(state));
      const factory = createDefaultChildSessionFactory({
        loadPiCodingAgent: async () => ({ ...sdk, ModelRuntime: { create: async () => runtime } }),
      });
      try {
        const child = await factory.create({
          cwd: home, storage: { kind: "memory" }, model: "github-copilot/claude-opus-5",
          tools: [], extensionPaths: discovery.agents.find((agent) => agent.name === "delegate").extensions,
          ambientExtensions: false, hooks: [],
          noSkills: true, noContextFiles: true, runtime: {},
          onExtensionError: (error) => assert.fail(String(error.error)),
        });
        await child.prompt("A foreground child must not bypass its parent's session cap on a new day.");
        assert.equal(faux.state.callCount, 1);
        assert.match(child.messages.filter((message) => message.role === "assistant").at(-1).errorMessage, /budget stopped/);
        await session.prompt("/copilot-budget off");
        await child.prompt("An existing child must observe disabled enforcement.");
        assert.equal(faux.state.callCount, 2);
        await session.prompt("/copilot-budget on");
        await child.prompt("An existing child must observe re-enabled enforcement.");
        assert.equal(faux.state.callCount, 2);
        assert.match(child.messages.filter((message) => message.role === "assistant").at(-1).errorMessage, /budget stopped/);
      } finally { await factory.dispose(); }
    }
    const calls = faux.state.callCount;
    await session.prompt("/copilot-budget invalid");
    assert.equal((await ledger.status(session.sessionId)).enforcementEnabled, true);
    await session.prompt("/copilot-budget off");
    assert.equal((await ledger.status(session.sessionId)).enforcementEnabled, false);
    const before = (await ledger.status(session.sessionId)).session;
    await session.prompt("Disabled enforcement must allow the request.");
    assert.equal(faux.state.callCount, calls + 1);
    assert.ok((await ledger.status(session.sessionId)).session > before);
    await session.reload();
    await session.prompt("Reload must preserve disabled enforcement.");
    assert.equal(faux.state.callCount, calls + 2);
    assert.equal(session.model.contextWindow, 272000);
    await session.prompt("/copilot-budget on");
    assert.equal((await ledger.status(session.sessionId)).enforcementEnabled, true);
    await session.prompt("Re-enabled enforcement must block again.");
    assert.equal(faux.state.callCount, calls + 2);
  } finally {
    session?.dispose();
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

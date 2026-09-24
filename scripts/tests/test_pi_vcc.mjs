import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const configText = readFileSync(join(repo, "stow/pi/.pi/agent/pi-vcc-config.json"), "utf8");
const config = JSON.parse(configText);
const piRoot = process.env.PI_PACKAGE_ROOT;
const extensionRoot = process.env.PI_VCC_ROOT;

test("VCC is pinned for automatic compaction on all providers", () => {
  const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
  assert.ok(fragment.packages.includes("npm:@sting8k/pi-vcc@0.8.0"));
  assert.deepEqual(config, {
    overrideDefaultCompaction: true,
    smartKeepTail: true,
    continueAfterThresholdCompact: false,
    debug: false,
    skipForProviders: [],
    skipCustomTypes: [],
  });
});

test("VCC handles automatic and manual compaction across local and cloud providers", {
  skip: (!piRoot || !extensionRoot) && "Set PI_PACKAGE_ROOT and PI_VCC_ROOT to reviewed packages",
  timeout: 20000,
}, async () => {
  const loaderUrl = pathToFileURL(resolve(piRoot, "dist/core/extensions/loader.js"));
  const entry = resolve(extensionRoot, "index.ts");
  const home = mkdtempSync(join(tmpdir(), "pi-vcc-test-"));
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
    const settingsPath = join(home, ".pi/agent/pi-vcc-config.json");
    mkdirSync(dirname(settingsPath), { recursive: true });
    writeFileSync(settingsPath, configText);
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    const { loadExtensions } = await import(loaderUrl);
    const loaded = await loadExtensions([entry], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    const extension = loaded.extensions[0];
    assert.deepEqual([...extension.tools.keys()], ["vcc_recall"]);
    assert.deepEqual([...extension.commands.keys()], ["pi-vcc", "pi-vcc-recall"]);
    assert.equal(loaded.runtime.pendingProviderRegistrations.length, 0);
    assert.equal(readFileSync(settingsPath, "utf8"), configText);

    const entries = [
      ["user", "Fix the fictional cache bug. Always preserve offline operation."],
      ["assistant", "The cache uses an incorrect fixture key."],
      ["user", "Add a regression test."],
      ["assistant", "The synthetic regression covers the cache key."],
      ["user", "Check the remaining work."],
      ["assistant", "The final fixture check is pending."],
    ].map(([role, text], index) => ({
      type: "message", id: `entry-${index}`, parentId: index ? `entry-${index - 1}` : null,
      timestamp: "2026-01-01T00:00:00.000Z",
      message: { role, content: [{ type: "text", text }], timestamp: 0 },
    }));
    const sessionFile = join(home, "synthetic.jsonl");
    writeFileSync(sessionFile, entries.map((value) => JSON.stringify(value)).join("\n") + "\n");
    const ctx = {
      model: { provider: "omlx" },
      sessionManager: {
        getEntries: () => entries,
        getBranch: () => entries,
        getSessionFile: () => sessionFile,
      },
    };
    const event = {
      reason: "manual", willRetry: false, branchEntries: entries,
      preparation: {
        tokensBefore: 2000, firstKeptEntryId: "entry-4",
        fileOps: { read: new Set(["cache.ts"]), written: new Set(), edited: new Set() },
      },
    };
    const hooks = extension.handlers.get("session_before_compact");
    assert.equal(hooks.length, 1);
    for (const provider of ["omlx", "openai-codex", "github-copilot", "anthropic", "fixture-new-provider", "omlx"]) {
      for (const reason of ["manual", "threshold", "overflow"]) {
        const automatic = await hooks[0]({ ...event, reason, willRetry: reason === "overflow" }, {
          ...ctx, model: { provider },
        });
        assert.equal(automatic?.compaction.details.compactor, "pi-vcc");
        assert.ok(automatic.compaction.summary.length > 0);
      }
    }
    let compactOptions;
    await extension.commands.get("pi-vcc").handler("keep:1", {
      ...ctx, compact: (options) => { compactOptions = options; },
    });
    const result = await hooks[0]({ ...event, customInstructions: compactOptions.customInstructions }, ctx);
    assert.equal(result.compaction.details.compactor, "pi-vcc");
    assert.equal(result.compaction.firstKeptEntryId, "entry-4");
    assert.match(result.compaction.summary, /fictional cache bug/);
    assert.match(result.compaction.summary, /cache\.ts/);
    assert.equal(result.compaction.tokensBefore, 2000);
    const explicitCloud = await hooks[0]({ ...event, customInstructions: compactOptions.customInstructions }, {
      ...ctx, model: { provider: "openai-codex" },
    });
    assert.equal(explicitCloud.compaction.details.compactor, "pi-vcc");
    assert.equal(readFileSync(sessionFile, "utf8"), entries.map((value) => JSON.stringify(value)).join("\n") + "\n");
    const recall = extension.tools.get("vcc_recall").definition;
    const recalled = await recall.execute("fixture-call", { query: "fictional cache" }, undefined, undefined, ctx);
    assert.ok(recalled.content.some((block) => block.type === "text" && block.text.includes("fictional cache bug")));
    assert.equal(networkAttempts, 0);
  } finally {
    globalThis.fetch = originalFetch;
    process.chdir(cwd);
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

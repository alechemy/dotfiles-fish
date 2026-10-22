import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const piRoot = process.env.PI_PACKAGE_ROOT;
const extensionRoot = process.env.PI_AUTORESEARCH_ROOT;
const vccRoot = process.env.PI_VCC_ROOT;
const experimentTools = ["init_experiment", "run_experiment", "log_experiment"];

test("autoresearch follows stable updates with bundled extensions and skills", () => {
  const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
  assert.equal(fragment.packages.filter((entry) => entry === "npm:pi-autoresearch").length, 1);
});

test("autoresearch loads, gates its tools, and composes with VCC without executing experiments", {
  skip: (!piRoot || !extensionRoot || !vccRoot) && "Set PI_PACKAGE_ROOT, PI_AUTORESEARCH_ROOT and PI_VCC_ROOT",
  timeout: 20000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-autoresearch-test-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let networkAttempts = 0;
  let extension;
  let ctx;
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
    const configPath = join(home, ".pi/agent/pi-vcc-config.json");
    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, readFileSync(join(repo, "stow/pi/.pi/agent/pi-vcc-config.json")));
    const manifest = JSON.parse(readFileSync(join(extensionRoot, "package.json"), "utf8"));
    assert.equal(manifest.version, "1.9.0");
    assert.deepEqual(manifest.pi, { extensions: ["./extensions"], skills: ["./skills"] });
    assert.equal(Object.keys(manifest.dependencies ?? {}).length, 0);
    assert.ok(!["preinstall", "install", "postinstall", "prepare"].some((key) => manifest.scripts?.[key]));
    const { loadExtensions } = await import(pathToFileURL(join(piRoot, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([
      join(vccRoot, "index.ts"),
      join(extensionRoot, "extensions/pi-autoresearch/index.ts"),
    ], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 2);
    extension = loaded.extensions[1];
    assert.deepEqual([...extension.tools.keys()], experimentTools);
    assert.deepEqual([...extension.commands.keys()], ["autoresearch"]);
    assert.equal(extension.shortcuts.size, 0);
    assert.equal(loaded.runtime.pendingProviderRegistrations.length, 0);
    const { loadSkillsFromDir } = await import(pathToFileURL(join(piRoot, "dist/core/skills.js")));
    const skills = loadSkillsFromDir({ dir: join(extensionRoot, "skills"), source: "user" });
    assert.deepEqual(skills.diagnostics, []);
    assert.deepEqual(skills.skills.map((skill) => skill.name).sort(), [
      "autoresearch-create", "autoresearch-finalize", "autoresearch-hooks",
    ]);
    const entries = ["user", "assistant", "user", "assistant"].map((role, index) => ({
      type: "message", id: `synthetic-${index}`, parentId: index ? `synthetic-${index - 1}` : null,
      timestamp: "2026-01-01T00:00:00.000Z",
      message: { role, content: [{ type: "text", text: `Fictional fixture turn ${index}.` }], timestamp: 0 },
    }));
    const messages = [];
    const notices = [];
    let activeTools = ["read", "bash", "edit", "write", "vcc_recall", ...experimentTools];
    loaded.runtime.getActiveTools = () => activeTools;
    loaded.runtime.setActiveTools = (tools) => { activeTools = tools; };
    loaded.runtime.appendEntry = (customType, data) => { entries.push({ type: "custom", customType, data }); };
    loaded.runtime.sendUserMessage = (message, options) => { messages.push({ message, options }); };
    ctx = {
      cwd: home, hasUI: false, isIdle: () => true, hasPendingMessages: () => false,
      abort: () => { throw new Error("Unexpected abort"); },
      ui: { notify: (message) => { notices.push(message); } },
      sessionManager: {
        getSessionId: () => "synthetic-autoresearch", getBranch: () => entries,
        getEntries: () => entries, getSessionFile: () => join(home, "synthetic.jsonl"),
      },
    };
    await extension.handlers.get("session_start")[0]({}, ctx);
    const ordinaryTools = ["read", "bash", "edit", "write", "vcc_recall"];
    assert.deepEqual(activeTools, ordinaryTools);
    const event = {
      reason: "manual", willRetry: false, branchEntries: entries,
      preparation: {
        tokensBefore: 1000, firstKeptEntryId: "synthetic-entry",
        fileOps: { read: new Set(), written: new Set(), edited: new Set() },
      },
    };
    const compact = async () => {
      let result;
      for (const item of loaded.extensions) {
        for (const handler of item.handlers.get("session_before_compact") ?? []) {
          const next = await handler(event, ctx);
          if (next?.cancel) return next;
          if (next) result = next;
        }
      }
      return result;
    };
    assert.equal((await compact()).compaction.details.compactor, "pi-vcc");
    const command = extension.commands.get("autoresearch");
    for (const help of ["", "help", "--help", "-h"]) await command.handler(help, ctx);
    assert.deepEqual(activeTools, ordinaryTools);
    assert.equal(messages.length, 0);
    assert.equal(entries.length, 4);
    assert.ok(notices.every((message) => message.startsWith("Usage: /autoresearch")));
    assert.equal(existsSync(join(home, ".auto")), false);
    await command.handler("optimize the fictional fixture", ctx);
    assert.deepEqual(activeTools, [...ordinaryTools, ...experimentTools]);
    assert.match(messages[0].message, /^\/skill:autoresearch-create optimize the fictional fixture/);
    assert.equal(messages[0].options.expandPromptTemplates, true);
    assert.match((await compact()).compaction.summary, /Autoresearch Compaction Summary/);
    await command.handler("off", ctx);
    assert.deepEqual(activeTools, ordinaryTools);
    assert.equal((await compact()).compaction.details.compactor, "pi-vcc");
    const logPath = join(home, ".auto/log.jsonl");
    mkdirSync(dirname(logPath), { recursive: true });
    const log = JSON.stringify({ type: "config", name: "Fictional session" }) + "\n";
    writeFileSync(logPath, log);
    await extension.handlers.get("session_start")[0]({}, ctx);
    assert.deepEqual(activeTools, ordinaryTools);
    assert.equal(readFileSync(logPath, "utf8"), log);
    assert.equal(networkAttempts, 0);
  } finally {
    try {
      if (extension && ctx) await extension.handlers.get("session_shutdown")[0]({}, ctx);
    } finally {
      globalThis.fetch = originalFetch;
      process.chdir(cwd);
      for (const key of Object.keys(process.env)) delete process.env[key];
      Object.assign(process.env, environment);
      rmSync(home, { recursive: true, force: true });
    }
  }
});

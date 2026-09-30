import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const entry = join(repo, "stow/pi/.pi/agent/extensions/pi-cmux.ts");
const piRoot = process.env.PI_PACKAGE_ROOT;
const extensionRoot = process.env.PI_CMUX_ROOT;
const expected = {
  PI_CMUX_NOTIFY_LEVEL: "disabled",
  PI_CMUX_SIDEBAR: "0",
  PI_CMUX_AUTOTITLE_DISABLED: "1",
};

test("pi-cmux is pinned and loaded only through the lifecycle preferences entry point", () => {
  const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
  const declaration = fragment.packages.find((value) => value.source === "npm:pi-cmux@0.1.24");
  assert.deepEqual(declaration, { source: "npm:pi-cmux@0.1.24", extensions: [] });
  assert.ok(existsSync(entry));
});

test("pi-cmux loads without automatic cmux or model calls", {
  skip: (!piRoot || !extensionRoot) && "Set PI_PACKAGE_ROOT and PI_CMUX_ROOT to reviewed packages",
  timeout: 20000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-cmux-loader-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const commandLog = join(home, "command.log");
  let networkAttempts = 0;
  try {
    const agentDir = join(home, ".pi/agent");
    const bin = join(home, "bin");
    const packageDir = join(agentDir, "npm/node_modules");
    mkdirSync(packageDir, { recursive: true });
    symlinkSync(resolve(extensionRoot), join(packageDir, "pi-cmux"), "dir");
    mkdirSync(bin);
    writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ "pi-cmux": { autotitle: true } }));
    for (const command of ["cmux", "pi", "git"]) {
      writeFileSync(join(bin, command), '#!/bin/sh\nprintf "%s\\n" "$0" >> "$HOME/command.log"\nexit 1\n', { mode: 0o755 });
    }
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, {
      HOME: home, PATH: `${bin}:${environment.PATH}`, PI_CODING_AGENT_DIR: agentDir,
      PI_OFFLINE: "1", PI_TELEMETRY: "0",
      CMUX_WORKSPACE_ID: "11111111-1111-4111-8111-111111111111",
      CMUX_SURFACE_ID: "22222222-2222-4222-8222-222222222222",
      PI_CMUX_NOTIFY_LEVEL: "all", PI_CMUX_SIDEBAR: "1", PI_CMUX_AUTOTITLE: "1",
    });
    process.chdir(home);
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    const { loadExtensions } = await import(pathToFileURL(resolve(piRoot, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([entry], home);
    assert.deepEqual(Object.fromEntries(Object.keys(expected).map((key) => [key, process.env[key]])), expected);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    const extension = loaded.extensions[0];
    assert.deepEqual([...extension.tools.keys()].sort(), [
      "cmux_annotate_browser", "cmux_open_browser", "cmux_open_terminal", "cmux_start_pi",
    ]);
    for (const command of ["cmn", "cmv", "cmh", "cmo", "cmt", "cmb", "cmcv", "cmch"]) {
      assert.ok(extension.commands.has(command), command);
    }
    assert.equal(loaded.runtime.pendingProviderRegistrations.length, 0);
    const ctx = {
      cwd: home, mode: "tui", hasUI: true, isIdle: () => true,
      sessionManager: { getSessionId: () => "synthetic-session" },
      ui: { notify: () => assert.fail("Unexpected UI notification") },
    };
    for (const [type, event] of [
      ["session_start", {}],
      ["before_agent_start", { prompt: "Synthetic installation check" }],
      ["agent_start", {}],
      ["agent_end", { messages: [{ role: "assistant", stopReason: "stop", content: [{ type: "text", text: "Synthetic result" }] }] }],
      ["agent_settled", {}],
      ["session_info_changed", { name: "Synthetic task" }],
      ["session_shutdown", {}],
    ]) {
      for (const handler of extension.handlers.get(type) ?? []) await handler(event, ctx);
    }
    assert.equal(existsSync(commandLog), false, "No automatic cmux, Pi, or Git execution is allowed");
    assert.equal(networkAttempts, 0);
  } finally {
    globalThis.fetch = originalFetch;
    process.chdir(cwd);
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

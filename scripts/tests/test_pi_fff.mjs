import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const piRoot = process.env.PI_PACKAGE_ROOT;
const extensionRoot = process.env.PI_FFF_ROOT;

test("FFF follows stable updates with lifecycle scripts disabled", () => {
  const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
  assert.equal(fragment.packages.filter((entry) => entry === "npm:@ff-labs/pi-fff").length, 1);
  assert.deepEqual(fragment.npmCommand, ["npm", "--ignore-scripts"]);
});

test("FFF loads and searches fictional files through its native binding", {
  skip: (!piRoot || !extensionRoot) && "Set PI_PACKAGE_ROOT and PI_FFF_ROOT",
  timeout: 30000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-fff-test-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let networkAttempts = 0;
  let extension;
  let ctx;
  try {
    for (const key of Object.keys(process.env)) delete process.env[key];
    const agentDir = join(home, ".pi/agent");
    const workspace = join(home, "workspace");
    Object.assign(process.env, {
      HOME: home, PATH: environment.PATH, PI_CODING_AGENT_DIR: agentDir,
      PI_OFFLINE: "1", PI_TELEMETRY: "0",
    });
    mkdirSync(agentDir, { recursive: true });
    mkdirSync(workspace);
    writeFileSync(join(workspace, "fictional-widget.txt"), "fictional_fff_needle\n");
    process.chdir(workspace);
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [resolve(extensionRoot)] }));
    const { DefaultResourceLoader } = await import(pathToFileURL(join(piRoot, "dist/core/resource-loader.js")));
    const loader = new DefaultResourceLoader({
      cwd: workspace, agentDir, noSkills: true, noThemes: true, noPromptTemplates: true, noContextFiles: true,
    });
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.deepEqual(loaded.errors, []);
    assert.deepEqual(loaded.warnings ?? [], []);
    assert.equal(loaded.extensions.length, 1);
    extension = loaded.extensions[0];
    assert.deepEqual([...extension.tools.keys()].sort(), ["fffind", "ffgrep"]);
    for (const name of ["fff-health", "fff-mode", "fff-rescan"]) assert.ok(extension.commands.has(name));
    let activeTools = ["read", "bash", "edit", "write", "find", "grep"];
    loaded.runtime.getActiveTools = () => activeTools;
    loaded.runtime.setActiveTools = (tools) => { activeTools = tools; };
    const notices = [];
    let autocompleteFactory;
    ctx = {
      cwd: workspace, hasUI: false,
      sessionManager: { getEntries: () => [] },
      ui: {
        notify: (message, type) => { notices.push({ message, type }); },
        addAutocompleteProvider: (factory) => { autocompleteFactory = factory; },
      },
    };
    await extension.handlers.get("session_start")[0]({}, ctx);
    assert.deepEqual(notices, []);
    assert.ok(activeTools.includes("find") && activeTools.includes("grep"));
    assert.ok(activeTools.includes("fffind") && activeTools.includes("ffgrep"));
    const signal = new AbortController().signal;
    for (const [name, params] of [
      ["fffind", { pattern: "fictional-widget" }],
      ["ffgrep", { pattern: "fictional_fff_needle" }],
    ]) {
      const result = await extension.tools.get(name).definition.execute(`fixture-${name}`, params, signal);
      assert.equal(result.isError ?? false, false);
      assert.match(result.content.filter((item) => item.type === "text").map((item) => item.text).join("\n"), /fictional-widget\.txt/);
    }
    const provider = autocompleteFactory({ getSuggestions: () => null });
    const suggestions = await provider.getSuggestions(["@fictional-widget"], 0, 17, { signal });
    assert.ok(suggestions.items.some((item) => item.value === "@fictional-widget.txt"));
    await extension.commands.get("fff-health").handler("", ctx);
    assert.match(notices.at(-1).message, /Mode: tools-and-ui/);
    assert.match(notices.at(-1).message, /Picker: 1 files/);
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

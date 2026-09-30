import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const allowUI = process.argv.includes("--allow-ui");
const discoveryOnly = process.argv.includes("--discovery-only");
if ((!allowUI && !discoveryOnly) || (allowUI && discoveryOnly) || !process.env.PI_PACKAGE_ROOT) {
  console.error("Set PI_PACKAGE_ROOT and choose --discovery-only or --allow-ui. The live test clicks 100 + 105 in Calculator.");
  process.exit(1);
}

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const piRoot = resolve(process.env.PI_PACKAGE_ROOT);
const root = mkdtempSync(join(tmpdir(), "pi-cua-smoke-"));
const agentDir = join(root, "agent");
mkdirSync(agentDir);
const originalFetch = globalThis.fetch;
let session;
let stage = "SDK import";
let toolCalls = 0;
let imageCount = 0;
let imageBytes = 0;
let responseShape;
let outcome = { result: "failed", reason: "The adapter or native runtime could not complete the bounded test." };

try {
  globalThis.fetch = () => { throw new Error("Model and network calls are forbidden in this smoke test."); };
  const { createAgentSession, ModelRuntime, SessionManager, SettingsManager } = await import(pathToFileURL(join(piRoot, "dist/index.js")));
  const { loadExtensions } = await import(pathToFileURL(join(piRoot, "dist/core/extensions/loader.js")));
  const { validateToolArguments } = await import(pathToFileURL(join(piRoot, "node_modules/@earendil-works/pi-ai/dist/utils/validation.js")));
  const { desktopTools } = await import(pathToFileURL(join(repo, "stow/pi/.pi/agent/extensions/cmux-cua/client.mjs")));
  stage = "extension loading";
  const loaded = await loadExtensions([join(repo, "stow/pi/.pi/agent/extensions/cmux-cua/index.ts")], root);
  assert.equal(loaded.errors.length, 0);
  assert.equal(loaded.extensions[0].tools.size, 0);
  const resourceLoader = {
    getExtensions: () => loaded,
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => "Run only the approved Calculator smoke test.",
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  stage = "current-model lookup";
  const modelRuntime = await ModelRuntime.create({ authPath: join(agentDir, "auth.json"), modelsPath: join(agentDir, "models.json"), allowModelNetwork: false });
  assert.ok(process.env.PI_PROVIDER && process.env.PI_MODEL, "The current provider and model identity are required.");
  const model = modelRuntime.getModel(process.env.PI_PROVIDER, process.env.PI_MODEL) ?? {
    provider: process.env.PI_PROVIDER, id: process.env.PI_MODEL, name: process.env.PI_MODEL,
    api: "disabled-smoke-test", baseUrl: "http://127.0.0.1:1", reasoning: false,
    input: ["text", "image"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 16384, maxTokens: 1,
  };
  stage = "in-memory Pi session";
  ({ session } = await createAgentSession({
    cwd: root, agentDir, resourceLoader, modelRuntime, model,
    sessionManager: SessionManager.inMemory(root),
    settingsManager: SettingsManager.inMemory({ cacheWarming: "off", compaction: { enabled: false } }),
    tools: desktopTools.map((name) => "mcp__cmux_cua__" + name),
  }));
  stage = "session binding";
  await session.bindExtensions({ mode: "json" });
  stage = "native activation";
  await session.prompt("/cmux-cua on");
  assert.equal(session.agent.state.tools.length, desktopTools.length, "Activation must expose the desktop allowlist.");
  const invoke = async (name, args) => {
    assert.ok(allowUI, "Only discovery is authorized by this invocation.");
    const tool = session.agent.state.tools.find((entry) => entry.name === "mcp__cmux_cua__" + name);
    assert.ok(tool);
    const validated = validateToolArguments(tool, { name: tool.name, arguments: args });
    toolCalls++;
    stage = name;
    const result = await tool.execute(`smoke-${toolCalls}`, validated, AbortSignal.timeout(95000));
    for (const item of result.content) {
      if (item.type === "image") { imageCount++; imageBytes += Buffer.from(item.data, "base64").length; }
    }
    const objects = [];
    for (const item of result.content) {
      if (item.type === "text") {
        try { objects.push(JSON.parse(item.text)); } catch {}
      }
    }
    const known = new Set(["pid", "app", "apps", "success", "launched", "bundle_id", "windows", "window_id", "id", "elements", "tree_markdown", "content", "isError", "result"]);
    responseShape = { jsonObjects: objects.length, contentTypes: result.content.map((item) => item.type), fields: objects.map((data) => ({ type: Array.isArray(data) ? "array" : typeof data, knownFields: Object.keys(data ?? {}).filter((key) => known.has(key)), otherFieldCount: Object.keys(data ?? {}).filter((key) => !known.has(key)).length })) };
    stage = name + " result";
    return { result, objects };
  };
  if (discoveryOnly) {
    outcome = { result: "discovery passed", desktopToolCount: desktopTools.length };
  } else {
    const apps = await invoke("list_apps", {});
    const appLists = apps.objects.flatMap((data) => Array.isArray(data) ? [data] : Object.values(data ?? {}).filter(Array.isArray));
    const calculatorPids = [...new Set(appLists.flat().filter((app) => app.bundle_id === "com.apple.calculator").map((app) => app.pid))];
    assert.ok(calculatorPids.length <= 1, "An unambiguous Calculator process is required.");
    let pid = calculatorPids[0];
    if (pid === undefined) {
      const launched = await invoke("launch_app", { bundle_id: "com.apple.calculator" });
      pid = launched.objects.map((data) => data?.pid).find((value) => Number.isInteger(value) && value > 0);
    }
    assert.ok(Number.isInteger(pid) && pid > 0, "Calculator must return its exact process identity.");
    const windows = await invoke("list_windows", { pid, on_screen_only: true });
    const lists = windows.objects.flatMap((data) => Array.isArray(data) ? [data] : Object.values(data ?? {}).filter(Array.isArray));
    const candidates = lists.flat().filter((window) => (window.pid === undefined || window.pid === pid) && Number.isInteger(window.window_id ?? window.id));
    assert.equal(candidates.length, 1, "One unambiguous Calculator window is required.");
    const windowId = candidates[0].window_id ?? candidates[0].id;
    const snapshot = () => invoke("get_window_state", { pid, window_id: windowId, max_depth: 25, max_elements: 500 });
    let state = await snapshot();
    const elements = (snapshot) => snapshot.objects.flatMap((data) => Array.isArray(data?.elements) ? data.elements : []);
    const label = (element) => [element.label, element.title, element.description, element.value, element.name].filter((value) => typeof value === "string" || typeof value === "number").join(" ");
    const normalize = (value) => typeof value === "string" ? value.trim().toLowerCase() : "";
    const buttonAliases = { "1": ["1", "one"], "0": ["0", "zero"], "5": ["5", "five"], "+": ["+", "add", "addition", "plus"], "=": ["=", "equals", "equal", "is equal to"] };
    const button = (text) => {
      const names = (buttonAliases[text] ?? [text]).map(normalize);
      const matches = elements(state).filter((element) => /button/i.test(element.role ?? element.type ?? "") && [element.label, element.title, element.description, element.name].some((value) => names.includes(normalize(value))));
      assert.equal(matches.length, 1, "A unique grounded Calculator button is required.");
      const element = matches[0];
      assert.ok(typeof element.element_token === "string" && element.element_token.length > 0);
      return { pid, window_id: windowId, element_token: element.element_token, delivery_mode: "foreground" };
    };
    const clearLabels = ["All Clear", "Clear", "AC", "C"];
    const clear = clearLabels.find((text) => elements(state).some((element) => [element.label, element.title, element.description, element.name].some((value) => normalize(value) === normalize(text))));
    assert.ok(clear, "Calculator must expose a grounded clear control.");
    await invoke("click", button(clear));
    state = await snapshot();
    for (const text of ["1", "0", "0", "+", "1", "0", "5", "="]) {
      await invoke("click", button(text));
      state = await snapshot();
    }
    assert.ok(elements(state).some((element) => /(^|\s)205($|\s)/.test(label(element))), "Calculator must display the verified result 205.");
    assert.ok(imageCount >= 2 && imageBytes > 0, "Grounding and verification must include inline screenshots.");
    outcome = { result: "passed", application: "Calculator", expression: "100 + 105", verifiedResult: "205" };
  }
} catch (error) {
  const message = error?.message ?? "";
  if (/onboarding|permissions?|Accessibility|Screen Recording/i.test(message)) outcome = { result: "blocked", reason: "cmux reported a permission or onboarding constraint. Setup was not opened." };
  else if (message.startsWith("cmux Computer Use rejected the MCP request")) outcome = { result: "failed", reason: "The native MCP execution request was rejected." };
  else if (message.startsWith("The originating cmux or Pi authority changed")) outcome = { result: "failed", reason: "The live authority changed before desktop dispatch." };
  else if (message.startsWith("The cmux Computer Use connection closed")) outcome = { result: "failed", reason: "The proxy connection closed with an uncertain execution outcome." };
} finally {
  if (session) {
    try { await session.prompt("/cmux-cua off"); } catch {}
    session.dispose();
  }
  globalThis.fetch = originalFetch;
  rmSync(root, { recursive: true, force: true });
}
console.log(JSON.stringify({ ...outcome, stage, responseShape, toolCalls, inlineScreenshots: imageCount, screenshotBytes: imageBytes, modelCalls: 0, screenshotsPersisted: 0 }));
if (!["passed", "discovery passed"].includes(outcome.result)) process.exitCode = 1;

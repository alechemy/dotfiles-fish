import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
const piRoot = process.env.PI_PACKAGE_ROOT;
const candidateRoot = process.env.PI_SUBAGENTS_ROOT;

test("guarded candidate loads in Pi and enforces the tracked local-role policy", {
  skip: (!piRoot || !candidateRoot) && "Set PI_PACKAGE_ROOT and PI_SUBAGENTS_ROOT to reviewed packages",
  timeout: 30000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-local-delegation-test-"));
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
    mkdirSync(join(home, ".pi/agent"), { recursive: true });
    writeFileSync(join(home, ".pi/agent/settings.json"), JSON.stringify({ subagents: fragment.subagents }));
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    const { loadExtensions } = await import(pathToFileURL(resolve(piRoot, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([resolve(candidateRoot, "index.ts")], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    assert.ok(loaded.extensions[0].tools.has("subagent"));

    const require = createRequire(resolve(piRoot, "package.json"));
    const { createJiti } = require("jiti");
    const jiti = createJiti(import.meta.url, { fsCache: false });
    const policy = await jiti.import(resolve(candidateRoot, "src/runs/shared/model-scope.ts"));
    const { localDelegationTools } = await jiti.import(resolve(candidateRoot, "src/runs/shared/local-delegation.ts"));
    const config = policy.parseModelScopeConfig(fragment.subagents.modelScope, { filePath: "fixture.json" });
    assert.deepEqual(config.localDelegation.rootProviders, ["github-copilot", "openai-codex"]);
    for (const provider of config.localDelegation.rootProviders) {
      const parent = { provider, id: "fixture-parent" };
      for (const role of ["scout", "local-editor"]) {
        const agent = fragment.subagents.agentOverridesByProvider[provider][role];
        const scopes = policy.resolveModelScopesForAgent(config, role, parent);
        assert.equal(policy.localDelegationAgent(agent.model, scopes), role);
        assert.deepEqual(localDelegationTools(agent.model, scopes), agent.tools);
        assert.deepEqual(agent.fallbackModels, []);
        assert.deepEqual(agent.extensions, []);
        assert.equal(agent.defaultContext, "fresh");
        assert.equal(agent.thinking, false);
        const [modelProvider, id] = agent.model.split("/");
        policy.assertScopedModel({ provider: modelProvider, id, baseUrl: "http://127.0.0.1:8000/v1" }, scopes);
        policy.assertScopedModel({ provider: modelProvider, id, baseUrl: "http://[::1]:8000/v1" }, scopes);
        for (const baseUrl of ["https://example.invalid/v1", "http://localhost:8000/v1"]) {
          assert.throws(() => policy.assertScopedModel({ provider: modelProvider, id, baseUrl }, scopes), /loopback/);
        }
        assert.throws(() => policy.assertLocalDelegationContext(agent.model, scopes, "fork"), /fresh/);
        const nested = policy.resolveModelScopesForAgent(config, role, parent, scopes);
        assert.throws(() => policy.assertProviderModelScope(agent.model, nested), /outside/);
        assert.throws(() => policy.assertModelScopeRunner(scopes, "external-cli"), /external/);
      }
      const localModel = fragment.subagents.agentOverridesByProvider[provider].scout.model;
      const worker = policy.resolveModelScopesForAgent(config, "worker", parent);
      assert.throws(() => policy.assertProviderModelScope(localModel, worker), /outside/);
    }
    const otherRoot = policy.resolveModelScopesForAgent(config, "scout", { provider: "anthropic", id: "fixture-parent" });
    assert.throws(() => policy.assertProviderModelScope("omlx/Qwen3.8-27B-oQ4e-mtp", otherRoot), /outside/);
    const legacy = structuredClone(fragment.subagents.modelScope);
    legacy.localDelegation.rootProvider = "github-copilot";
    delete legacy.localDelegation.rootProviders;
    assert.throws(() => policy.parseModelScopeConfig(legacy, { filePath: "fixture.json" }), /localDelegation/);
    assert.equal(networkAttempts, 0);
  } finally {
    globalThis.fetch = originalFetch;
    process.chdir(cwd);
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const piRoot = process.env.PI_PACKAGE_ROOT;
const packagesRoot = process.env.PI_STABLE_PACKAGES_ROOT;

test("stable Context7 and Web Access load through their published manifests without credentials", {
  skip: (!piRoot || !packagesRoot) && "Set PI_PACKAGE_ROOT and PI_STABLE_PACKAGES_ROOT",
  timeout: 30000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-stable-packages-test-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let networkAttempts = 0;
  try {
    for (const key of Object.keys(process.env)) delete process.env[key];
    const agentDir = join(home, ".pi/agent");
    Object.assign(process.env, {
      HOME: home, PATH: environment.PATH, PI_CODING_AGENT_DIR: agentDir,
      PI_OFFLINE: "1", PI_TELEMETRY: "0",
    });
    process.chdir(home);
    mkdirSync(agentDir, { recursive: true });
    writeFileSync(join(agentDir, "settings.json"), JSON.stringify({
      packages: [resolve(packagesRoot, "@upstash/context7-pi"), resolve(packagesRoot, "pi-web-access")],
    }));
    globalThis.fetch = () => {
      networkAttempts++;
      throw new Error("Network access is forbidden");
    };
    const { DefaultResourceLoader } = await import(pathToFileURL(resolve(piRoot, "dist/core/resource-loader.js")));
    const loader = new DefaultResourceLoader({
      cwd: home, agentDir, noSkills: true, noThemes: true, noPromptTemplates: true, noContextFiles: true,
    });
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.deepEqual(loaded.errors, []);
    assert.deepEqual(loaded.warnings ?? [], []);
    assert.equal(loaded.extensions.length, 2);
    const tools = loaded.extensions.flatMap((extension) => [...extension.tools.keys()]);
    for (const name of ["resolve-library-id", "query-docs", "web_search", "source_check", "fetch_content", "get_search_content"]) {
      assert.ok(tools.includes(name), `Missing tool: ${name}`);
    }
    assert.equal(networkAttempts, 0);
  } finally {
    globalThis.fetch = originalFetch;
    process.chdir(cwd);
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

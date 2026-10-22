import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const piRoot = process.env.PI_PACKAGE_ROOT;
const candidateRoot = process.env.PI_SUBAGENTS_ROOT;
const fffRoot = process.env.PI_FFF_ROOT;

test("guarded foreground and runner children use native FFF with isolated indexes and restricted tools", {
  skip: (!piRoot || !candidateRoot || !fffRoot) && "Set PI_PACKAGE_ROOT, PI_SUBAGENTS_ROOT and PI_FFF_ROOT",
  timeout: 30000,
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-local-fff-test-"));
  const environment = { ...process.env };
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const children = [];
  let factory;
  let networkAttempts = 0;
  try {
    for (const key of Object.keys(process.env)) delete process.env[key];
    const agentDir = join(home, ".pi/agent");
    Object.assign(process.env, {
      HOME: home, PATH: environment.PATH, PI_CODING_AGENT_DIR: agentDir,
      PI_OFFLINE: "1", PI_TELEMETRY: "0", PI_FFF_MODE: "override", PI_FFF_MULTIGREP: "1",
    });
    mkdirSync(join(agentDir, "npm/node_modules/@ff-labs"), { recursive: true });
    symlinkSync(resolve(fffRoot), join(agentDir, "npm/node_modules/@ff-labs/pi-fff"));
    writeFileSync(join(agentDir, "settings.json"), "{}");
    writeFileSync(join(agentDir, "pi-fff.json"), JSON.stringify({ mode: "override" }));
    process.chdir(home);
    globalThis.fetch = () => { networkAttempts++; throw new Error("Network access is forbidden"); };
    const pi = await import(pathToFileURL(join(piRoot, "dist/index.js")));
    const require = createRequire(join(piRoot, "package.json"));
    const jiti = require("jiti").createJiti(import.meta.url, {
      fsCache: false,
      alias: {
        "@earendil-works/pi-coding-agent": join(piRoot, "dist/index.js"),
        "@earendil-works/pi-ai/compat": join(piRoot, "node_modules/@earendil-works/pi-ai/dist/compat.js"),
        "@earendil-works/pi-ai/oauth": join(piRoot, "node_modules/@earendil-works/pi-ai/dist/oauth.js"),
        "@earendil-works/pi-ai/providers/all": join(piRoot, "node_modules/@earendil-works/pi-ai/dist/providers/all.js"),
        "@earendil-works/pi-ai": join(piRoot, "node_modules/@earendil-works/pi-ai/dist/compat.js"),
        "@earendil-works/pi-agent-core": join(piRoot, "node_modules/@earendil-works/pi-agent-core/dist/index.js"),
        "@earendil-works/pi-tui": join(piRoot, "node_modules/@earendil-works/pi-tui/dist/index.js"),
        "typebox/compile": require.resolve("typebox/compile"),
        "typebox/value": require.resolve("typebox/value"),
        typebox: require.resolve("typebox"),
      },
    });
    const { buildInProcessChildLaunch } = await jiti.import(join(candidateRoot, "src/runs/shared/child-launch.ts"));
    const { createDefaultChildSessionFactory } = await jiti.import(join(candidateRoot, "src/runs/shared/child-session.ts"));
    const policy = await jiti.import(join(candidateRoot, "src/runs/shared/model-scope.ts"));
    const fragment = JSON.parse(readFileSync(join(repo, "stow/pi/.pi/agent/settings.fragment.json"), "utf8"));
    const config = policy.parseModelScopeConfig(fragment.subagents.modelScope, { filePath: "fixture.json" });
    let captured;
    factory = createDefaultChildSessionFactory({
      loadPiCodingAgent: async () => ({
        ...pi,
        resolveCliModel: ({ cliModel }) => {
          const [provider, id] = cliModel.split("/");
          return { model: { provider, id, name: id, api: "openai-completions", baseUrl: "http://127.0.0.1:8000/v1", reasoning: false, input: ["text"], contextWindow: 32000, maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } } };
        },
        createAgentSession: async (options) => {
          const created = await pi.createAgentSession(options);
          captured = created.session;
          return created;
        },
      }),
    });
    const sessions = [];
    const errors = [];
    for (const [index, host] of ["parent", "runner"].entries()) {
      const role = index === 0 ? "scout" : "local-editor";
      const provider = index === 0 ? "openai-codex" : "github-copilot";
      const agent = fragment.subagents.agentOverridesByProvider[provider][role];
      const workspace = join(home, `workspace-${index}`);
      mkdirSync(workspace);
      writeFileSync(join(workspace, `fictional-${index}.txt`), `fictional_needle_${index}\n`);
      const launch = buildInProcessChildLaunch({
        host, model: agent.model, modelScope: policy.resolveModelScopesForAgent(config, role, { provider, id: "fixture-parent" }),
        childAgentName: role, childIndex: index, cwd: workspace, sessionEnabled: false,
        inheritProjectContext: false, inheritGlobalContext: false, inheritSkills: false,
        tools: [...agent.tools, "bash", "subagent", "custom"], excludeTools: index === 0 ? ["ffgrep"] : [],
        extensions: ["/tmp/must-not-load.ts"], subagentOnlyExtensions: ["/tmp/must-not-load-either.ts"],
      });
      assert.equal(launch.session.ambientExtensions, false);
      assert.deepEqual(launch.session.extensionPaths, [join(agentDir, "npm/node_modules/@ff-labs/pi-fff")]);
      children.push(await factory.create({ ...launch.session, onExtensionError: (error) => { errors.push(error); } }));
      sessions.push(captured);
      assert.deepEqual(captured.getActiveToolNames().sort(), launch.config.localFffTools.toSorted());
      const blocked = await captured.extensionRunner.emitToolCall({ type: "tool_call", toolName: "bash", toolCallId: "forbidden", input: {} });
      assert.equal(blocked.block, true);
      assert.ok(!captured.getActiveToolNames().includes("fff-multi-grep"));
      if (index === 0) {
        const excluded = await captured.extensionRunner.emitToolCall({ type: "tool_call", toolName: "ffgrep", toolCallId: "excluded", input: {} });
        assert.equal(excluded.block, true);
      }
    }
    async function search(session, index, name, pattern) {
      const tool = session.agent.state.tools.find((item) => item.name === name);
      assert.ok(tool);
      const result = await tool.execute(`fixture-${index}-${name}`, { pattern }, new AbortController().signal);
      const text = result.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
      assert.match(text, new RegExp(`fictional-${index}\\.txt`));
      assert.doesNotMatch(text, new RegExp(`fictional-${1 - index}\\.txt`));
    }
    await Promise.all(sessions.map((session, index) => search(session, index, "fffind", `fictional-${index}`)));
    await search(sessions[1], 1, "ffgrep", "fictional_needle_1");
    await children[0].dispose();
    await search(sessions[1], 1, "ffgrep", "fictional_needle_1");
    assert.deepEqual(errors, []);
    assert.equal(networkAttempts, 0);
  } finally {
    try {
      await Promise.all(children.map((child) => child.dispose()));
      await factory?.dispose();
    } finally {
      globalThis.fetch = originalFetch;
      process.chdir(cwd);
      for (const key of Object.keys(process.env)) delete process.env[key];
      Object.assign(process.env, environment);
      rmSync(home, { recursive: true, force: true });
    }
  }
});

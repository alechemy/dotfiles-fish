import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { chmodSync, linkSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { PassThrough } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { proxyEnvironment, readToken, resolveAuthority } from "../../stow/pi/.pi/agent/extensions/cmux-cua/authority.mjs";
import { desktopTools, NativeProxy, validateArguments } from "../../stow/pi/.pi/agent/extensions/cmux-cua/client.mjs";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const entry = join(repo, "stow/pi/.pi/agent/extensions/cmux-cua/index.ts");
const credential = "SyntheticCredentialForTestsOnly0000000000000000000000000000000000";
const authority = { key: "synthetic-authority", token: credential, client: "/synthetic/cmux.app/Contents/Resources/bin/cmux-cua", socket: "/tmp/cmux-cua-501/com.cmuxterm.app/cmux-cua.sock", state: "/synthetic/state", surface: "22222222-2222-4222-8222-222222222222", workspace: "11111111-1111-4111-8111-111111111111", pid: 42, scope: "com.cmuxterm.app" };
const catalog = desktopTools.map((name) => ({ name, description: `Synthetic ${name}`, inputSchema: { type: "object", additionalProperties: false, properties: { value: { type: "string" } } } }));

function proxyFixture(options = {}) {
  const child = new EventEmitter();
  Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null, signalCode: null });
  const calls = [];
  const kills = [];
  const finish = (signal) => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    child.exitCode = signal ? null : 0;
    child.signalCode = signal ?? null;
    child.emit("exit", child.exitCode, child.signalCode);
  };
  child.kill = (signal) => { kills.push(signal); finish(signal); return true; };
  child.stdin.on("finish", () => { if (!options.ignoreEof) queueMicrotask(() => finish()); });
  let input = "";
  child.stdin.on("data", (data) => {
    input += data.toString();
    let end;
    while ((end = input.indexOf("\n")) !== -1) {
      const request = JSON.parse(input.slice(0, end));
      input = input.slice(end + 1);
      calls.push(request);
      if (request.id === undefined) continue;
      const response = options.respond?.(request) ?? (request.method === "initialize" ? { protocolVersion: "2025-06-18", serverInfo: { name: "cmux-cua", version: "synthetic" }, capabilities: { tools: {} } } : request.method === "tools/list" ? { tools: options.catalog ?? catalog } : { content: [{ type: "text", text: "Synthetic result" }] });
      if (response === "hang") continue;
      const envelope = { jsonrpc: "2.0", id: request.id, result: response };
      queueMicrotask(() => child.stdout.write(JSON.stringify(envelope) + "\n"));
    }
  });
  let spawnCount = 0;
  let launch;
  const spawn = (file, args, settings) => { spawnCount++; launch = { file, args, settings }; return child; };
  const client = new NativeProxy(authority, { spawn, timeout: options.timeout ?? 100 });
  return { client, child, calls, kills, get launch() { return launch; }, get spawnCount() { return spawnCount; } };
}

function authorityFixture() {
  const env = {
    CMUX_WORKSPACE_ID: authority.workspace, CMUX_SURFACE_ID: authority.surface,
    CMUX_BUNDLE_ID: "com.cmuxterm.app", CMUX_COMPUTER_USE_APP_ENABLED: "1",
    CMUX_BUNDLED_CLI_PATH: "/synthetic/cmux.app/Contents/Resources/bin/cmux",
    CMUX_SOCKET_PATH: "/synthetic/cmux.sock", CMUX_CUA_SOCKET_PATH: authority.socket,
    CMUX_CUA_CODEX_SOCKET_PATH: "/tmp/cmux-cua-501/com.cmuxterm.app/cmux-cua-codex.sock",
    CMUX_CUA_AUTH_TOKEN_FILE: "/tmp/cmux-cua-501/com.cmuxterm.app/auth-token",
    CMUX_CUA_RUNTIME_SCOPE: "com.cmuxterm.app",
    CMUX_CUA_STATE_DIR: "/synthetic/Library/Application Support/cmux/cmux-cua/runtime/com.cmuxterm.app/state",
    CMUX_CUA_CLIENT_PATH: "/synthetic/Library/Application Support/cmux/cmux-cua/helper/com.cmuxterm.app/cmux Computer Use.app/Contents/MacOS/cmux-cua",
  };
  const identity = {
    caller: { workspace_id: authority.workspace, surface_id: authority.surface, surface_type: "terminal" },
    focused: { workspace_id: "other", surface_id: "other" },
    bundle_identifier: "com.cmuxterm.app", app_bundle_path: "/synthetic/cmux.app",
    app_cli_path: env.CMUX_BUNDLED_CLI_PATH, app_executable_path: "/synthetic/cmux.app/Contents/MacOS/cmux",
    socket_path: env.CMUX_SOCKET_PATH,
  };
  let policy = { enabled: true, policyDisabled: false };
  const commands = [];
  const io = {
    realpath: async (path) => path,
    lstat: async (path) => {
      const isFile = path.endsWith("/cmux") || path.endsWith("/cmux-cua");
      const isSocket = path.endsWith(".sock");
      return { uid: 501, dev: 1, ino: 2, size: 3, mtimeMs: 4, mode: isFile ? 0o755 : isSocket ? 0o600 : 0o700, isFile: () => isFile, isSocket: () => isSocket, isDirectory: () => !isFile && !isSocket, isSymbolicLink: () => false };
    },
    open: async () => ({
      stat: async () => ({ uid: 501, mode: 0o600, nlink: 1, size: credential.length, isFile: () => true }),
      read: async (buffer) => { buffer.write(credential); return { bytesRead: credential.length }; },
      close: async () => {},
    }),
    run: async (file, args) => {
      commands.push({ file, args });
      if (file === "/bin/ps") return args[1] === "42" ? "84 node\n" : `1 ${identity.app_executable_path}\n`;
      if (file === "/usr/bin/python3") return JSON.stringify(policy);
      if (args.includes("identify")) return JSON.stringify(identity);
      if (args.includes("config")) return JSON.stringify({ primary: "~/cmux.json" });
      assert.fail("Unexpected authority command");
    },
  };
  return { env, io, identity, commands, setPolicy: (value) => { policy = value; }, options: { env, io, platform: "darwin", uid: 501, home: "/synthetic", temp: "/tmp", pid: 42 } };
}

test("authority requires the exact live caller rather than focus, bundled client, private native paths, and current policy", async () => {
  const fixture = authorityFixture();
  const result = await resolveAuthority(fixture.options);
  assert.equal(result.surface, authority.surface);
  assert.equal(result.pid, 42);
  assert.equal(result.token, credential);
  assert.equal(fixture.commands.some(({ args }) => args.includes("serve")), false);
  fixture.identity.caller.surface_id = "different";
  await assert.rejects(resolveAuthority(fixture.options));
  fixture.identity.caller.surface_id = authority.surface;
  fixture.setPolicy({ enabled: true, policyDisabled: true });
  await assert.rejects(resolveAuthority(fixture.options));
  fixture.setPolicy({ enabled: false, policyDisabled: false });
  await assert.rejects(resolveAuthority(fixture.options));
});

test("disabled, child, stale, foreign, ambient and compatibility-profile authority fails closed", async () => {
  for (const [key, value] of [
    ["CMUX_COMPUTER_USE_MCP_DISABLED", "1"], ["CMUX_COMPUTER_USE_APP_ENABLED", "0"], ["PI_SUBAGENT_CHILD", "1"],
    ["CMUX_SURFACE_ID", ""], ["CMUX_CUA_RUNTIME_SCOPE", "../other"], ["CMUX_BUNDLED_CLI_PATH", "/usr/local/bin/cmux"],
    ["CMUX_CUA_SOCKET_PATH", "/tmp/cmux-cua-501/com.cmuxterm.app/cmux-cua-codex.sock"], ["CMUX_CUA_AUTH_TOKEN_FILE", "/tmp/other-token"],
    ["CMUX_CUA_STATE_DIR", "/tmp/other-state"], ["CMUX_CUA_CLIENT_PATH", "/Applications/Standalone.app/cmux-cua"],
    ["CMUX_CUA_SOCKET_PATH", "/foreign/cmux-cua-501/com.cmuxterm.app/cmux-cua.sock"], ["CMUX_BUNDLE_ID", "other"],
  ]) {
    const fixture = authorityFixture();
    fixture.env[key] = value;
    await assert.rejects(resolveAuthority(fixture.options), key);
  }
  const fixture = authorityFixture();
  fixture.io.lstat = async () => ({ isSymbolicLink: () => true });
  await assert.rejects(resolveAuthority(fixture.options));
});

test("tokens are bounded no-follow single-link owner-only regular files without environment fallback", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-cua-token-"));
  try {
    const path = join(root, "token");
    writeFileSync(path, credential + "\n", { mode: 0o600 });
    assert.equal(await readToken(path, process.getuid()), credential);
    await assert.rejects(readToken(path, process.getuid() + 1));
    chmodSync(path, 0o644);
    await assert.rejects(readToken(path, process.getuid()));
    chmodSync(path, 0o600);
    symlinkSync(path, join(root, "symlink"));
    await assert.rejects(readToken(join(root, "symlink"), process.getuid()));
    linkSync(path, join(root, "hardlink"));
    await assert.rejects(readToken(path, process.getuid()));
    rmSync(join(root, "hardlink"));
    writeFileSync(path, "x".repeat(1025));
    await assert.rejects(readToken(path, process.getuid()));
    writeFileSync(path, "not a token");
    await assert.rejects(readToken(path, process.getuid()));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("child environment preserves cmux ownership and ignores hostile helper, logging and admission overrides", () => {
  const env = proxyEnvironment(authority, { HOME: "/synthetic", PATH: "/bin", CMUX_CUA_MCP_FORCE_PROXY: "0", CMUX_CUA_EXTERNAL_PERMISSION_FLOW: "0", CMUX_CUA_DAEMON_APP: "/other", CMUX_CUA_STATE_OWNER_PID: "99", CMUX_CUA_SOCKET_HOST_AUTH_TOKEN: "host-secret", CUA_LOG: "debug", NODE_OPTIONS: "--require=hostile", ANTHROPIC_API_KEY: "private" });
  assert.equal(env.CMUX_CUA_MCP_FORCE_PROXY, "1");
  assert.equal(env.CMUX_CUA_EXTERNAL_PERMISSION_FLOW, "1");
  assert.equal(env.CMUX_CUA_STATE_OWNER_PID, "42");
  assert.equal(env.CMUX_CUA_DEFAULT_SESSION, `cmux-${authority.surface}`);
  assert.equal(env.CMUX_CUA_STATE_DIR, authority.state);
  assert.equal(env.CUA_LOG, "off");
  for (const key of ["CMUX_CUA_DAEMON_APP", "CMUX_CUA_SOCKET_HOST_AUTH_TOKEN", "ANTHROPIC_API_KEY"]) assert.equal(env[key], undefined);
});

test("discovery remains tool-call-free, preserves native schemas, and closes only its own proxy through EOF", async () => {
  const f = proxyFixture();
  const tools = await f.client.discover();
  assert.deepEqual(tools, catalog);
  assert.deepEqual(f.calls.map((call) => call.method), ["initialize", "notifications/initialized", "tools/list"]);
  assert.deepEqual(f.launch.args, ["mcp", "--socket", authority.socket]);
  assert.equal(f.launch.settings.env.CMUX_CUA_SOCKET_AUTH_TOKEN, credential);
  assert.ok(!f.launch.args.some((arg) => arg.includes(credential)));
  await f.client.close();
  await f.client.close();
  assert.deepEqual(f.kills, []);
  assert.equal(f.spawnCount, 1);
  assert.ok(!f.calls.some((call) => call.params?.name === "end_session"));
});

test("allowlist excludes session, permission, recording, configuration and nested private controls", () => {
  for (const name of ["start_session", "end_session", "check_permissions", "recording_start", "set_config", "move_cursor"]) assert.throws(() => validateArguments(name, {}));
  for (const args of [{ session: "other" }, { _session_id: "other" }, { actions: [{ tool: "click", arguments: { _host_session: "other" } }] }]) assert.throws(() => validateArguments("click", args));
  for (const tool of ["start_session", "check_permissions", "perform_actions", "move_cursor"]) assert.throws(() => validateArguments("perform_actions", { actions: [{ tool, arguments: {} }] }));
  for (const key of ["screenshot_out_file", "debug_image_out", "cdp_debugging_port", "webkit_inspector_port", "additional_arguments"]) assert.throws(() => validateArguments("get_window_state", { [key]: "private" }));
  validateArguments("perform_actions", { actions: [{ tool: "click", arguments: { x: 1, y: 2 } }] });
});

test("calls serialize, recheck authority at dispatch, retain text and images, and redact the credential", async () => {
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? { content: [{ type: "text", text: `UI é ${credential}` }, { type: "image", mimeType: "image/png", data: "eA==" }] } : undefined });
  await f.client.discover();
  let checked = 0;
  const verify = async () => { checked++; };
  const results = await Promise.all([f.client.call("click", {}, undefined, verify), f.client.call("get_window_state", {}, undefined, verify)]);
  assert.equal(checked, 2);
  assert.deepEqual(f.calls.filter((call) => call.method === "tools/call").map((call) => call.params.name), ["click", "get_window_state"]);
  for (const result of results) {
    assert.equal(result.content[0].text, "UI é [redacted cmux credential]");
    assert.deepEqual(result.content[1], { type: "image", mimeType: "image/png", data: "eA==" });
    assert.equal(result.details, undefined);
  }
  await f.client.close();
});

test("authority loss prevents dispatch and automatic restart", async () => {
  const f = proxyFixture();
  await f.client.discover();
  await assert.rejects(f.client.call("click", {}, undefined, async () => { throw new Error(credential); }), (error) => !error.message.includes(credential));
  assert.equal(f.calls.some((call) => call.method === "tools/call"), false);
  await assert.rejects(f.client.call("click", {}, undefined, async () => {}));
  assert.equal(f.spawnCount, 1);
});

test("cancellation and timeouts close the proxy and report action uncertainty without retries", async () => {
  for (const cancel of [true, false]) {
    const f = proxyFixture({ timeout: 15, respond: (req) => req.method === "tools/call" ? "hang" : undefined });
    await f.client.discover();
    const controller = new AbortController();
    const result = f.client.call("click", {}, controller.signal, async () => {});
    if (cancel) setTimeout(() => controller.abort(), 5);
    await assert.rejects(result, /may have executed/);
    await f.client.close();
    assert.equal(f.calls.filter((call) => call.method === "tools/call").length, 1);
    assert.equal(f.spawnCount, 1);
  }
});

test("pre-dispatch cancellation sends nothing", async () => {
  const f = proxyFixture();
  await f.client.discover();
  await assert.rejects(f.client.call("click", {}, AbortSignal.abort(), async () => {}), /before dispatch/);
  assert.equal(f.calls.some((call) => call.method === "tools/call"), false);
  await f.client.close();
});

test("tool errors are failures, redact credentials, and require explicit reactivation", async () => {
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? { isError: true, content: [{ type: "text", text: `Setup required ${credential}` }] } : undefined });
  await f.client.discover();
  await assert.rejects(f.client.call("get_window_state", {}, undefined, async () => {}), (error) => error.message.includes("Setup required") && !error.message.includes(credential));
  assert.equal(f.client.closed, true);
});

test("wrong profile, malformed catalog and unsupported content fail closed", async () => {
  for (const tools of [catalog.filter((tool) => tool.name !== "get_window_state"), [...catalog, catalog[0]], [{ name: "click" }]]) {
    const f = proxyFixture({ catalog: tools });
    await assert.rejects(f.client.discover(), /discovery failed/);
    assert.equal(f.calls.some((call) => call.method === "tools/call"), false);
  }
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? { content: [{ type: "resource", resource: { uri: "file:///private" } }] } : undefined });
  await f.client.discover();
  await assert.rejects(f.client.call("click", {}, undefined, async () => {}), /may have executed/);
});

test("malformed, unmatched, oversized and secret-bearing protocol output is suppressed", async () => {
  for (const data of [credential + "\n", JSON.stringify({ jsonrpc: "2.0", id: 999, result: credential }) + "\n", "x".repeat(16 * 1024 * 1024 + 1)]) {
    const f = proxyFixture({ respond: () => "hang" });
    const result = f.client.discover();
    setImmediate(() => { f.child.stderr.write(credential); f.child.stdout.write(data); });
    await assert.rejects(result, (error) => !error.message.includes(credential));
    assert.equal(f.client.closed, true);
  }
});

test("Pi registration is quiet and model-independent until a user command", { skip: !process.env.PI_PACKAGE_ROOT && "Set PI_PACKAGE_ROOT to the installed reviewed Pi package" }, async () => {
  const home = mkdtempSync(join(tmpdir(), "pi-cua-loader-"));
  const environment = { ...process.env };
  const originalFetch = globalThis.fetch;
  try {
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, { HOME: home, PATH: environment.PATH, PI_CODING_AGENT_DIR: join(home, ".pi/agent"), PI_OFFLINE: "1", PI_TELEMETRY: "0" });
    globalThis.fetch = () => { assert.fail("Unexpected network call"); };
    const { loadExtensions } = await import(pathToFileURL(join(environment.PI_PACKAGE_ROOT, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([entry], home);
    assert.deepEqual(loaded.errors, []);
    const extension = loaded.extensions[0];
    assert.equal(extension.tools.size, 0);
    assert.deepEqual([...extension.commands.keys()], ["cmux-cua"]);
    loaded.runtime.getActiveTools = () => ["read"];
    loaded.runtime.setActiveTools = (tools) => { assert.deepEqual(tools, ["read"]); };
    for (const type of ["session_start", "session_shutdown"]) for (const handler of extension.handlers.get(type) ?? []) await handler({}, {});
    assert.equal(extension.tools.size, 0);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
    rmSync(home, { recursive: true, force: true });
  }
});

async function extensionFixture() {
  const { createJiti } = await import(pathToFileURL(join(process.env.PI_PACKAGE_ROOT, "node_modules/jiti/lib/jiti.mjs")));
  const jiti = createJiti(import.meta.url, { moduleCache: false, fsCache: false });
  const { registerComputerUse } = await jiti.import(entry);
  const tools = new Map();
  const commands = new Map();
  const handlers = new Map();
  const notifications = [];
  const clients = [];
  let active = ["read", "mcp__cmux_cua__unrelated"];
  let sessionId = "synthetic-one";
  let verify = async () => authority;
  const pi = {
    registerCommand: (name, command) => commands.set(name, command),
    registerTool: (tool) => tools.set(tool.name, tool),
    on: (name, handler) => handlers.set(name, handler),
    getActiveTools: () => active,
    getAllTools: () => [...tools.values()],
    setActiveTools: (names) => { active = names; },
  };
  registerComputerUse(pi, {
    resolveAuthority: () => verify(),
    NativeProxy: class {
      constructor() { const fixture = proxyFixture(); clients.push(fixture); return fixture.client; }
    },
  });
  const ctx = { model: { input: ["text", "image"] }, sessionManager: { getSessionId: () => sessionId }, ui: { notify: (message, type) => notifications.push({ message, type }) }, waitForIdle: async () => {} };
  return { tools, handlers, clients, ctx, notifications, command: (args) => commands.get("cmux-cua").handler(args, ctx), active: () => active, session: (id) => { sessionId = id; }, verifier: (fn) => { verify = fn; } };
}

const piOnly = { skip: !process.env.PI_PACKAGE_ROOT && "Set PI_PACKAGE_ROOT to reviewed Pi" };

test("user activation preserves schemas, unrelated active tools, sequential execution and session-bound cleanup", piOnly, async () => {
  const f = await extensionFixture();
  assert.equal(f.tools.size, 0);
  await f.handlers.get("session_start")({}, f.ctx);
  await f.command("status");
  assert.equal(f.clients.length, 0);
  await f.command("on");
  assert.equal(f.clients.length, 1);
  assert.equal(f.tools.size, desktopTools.length);
  assert.ok(f.active().includes("mcp__cmux_cua__unrelated"));
  for (const tool of catalog) {
    const definition = f.tools.get("mcp__cmux_cua__" + tool.name);
    assert.deepEqual(definition.parameters, tool.inputSchema);
    assert.equal(definition.executionMode, "sequential");
  }
  const tool = f.tools.get("mcp__cmux_cua__click");
  await tool.execute("synthetic", {}, undefined, undefined, f.ctx);
  f.session("synthetic-two");
  await assert.rejects(tool.execute("synthetic", {}, undefined, undefined, f.ctx), /current Pi session/);
  await f.handlers.get("session_shutdown")({}, f.ctx);
  assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
  assert.equal(f.clients[0].client.closed, true);
});

test("authority changes revoke tools before a GUI request", piOnly, async () => {
  const f = await extensionFixture();
  await f.command("on");
  f.verifier(async () => ({ ...authority, key: "replacement" }));
  await assert.rejects(f.tools.get("mcp__cmux_cua__click").execute("synthetic", {}, undefined, undefined, f.ctx), /No desktop operation/);
  assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
  assert.equal(f.clients[0].calls.some((call) => call.method === "tools/call"), false);
});

test("parallel activation and off during idle wait cannot start a proxy", piOnly, async () => {
  const f = await extensionFixture();
  let release;
  f.ctx.waitForIdle = () => new Promise((resolve) => { release = resolve; });
  const first = f.command("on");
  await f.command("on");
  await f.command("off");
  release();
  await first;
  assert.equal(f.clients.length, 0);
  assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
});

test("off or shutdown during old-proxy cleanup cannot be adopted by a pending activation", piOnly, async () => {
  for (const stop of ["off", "shutdown"]) {
    const f = await extensionFixture();
    await f.command("on");
    f.session("synthetic-two");
    const client = f.clients[0].client;
    const close = client.close.bind(client);
    let release;
    client.close = () => new Promise((resolve) => { release = async () => { await close(); resolve(); }; });
    const activation = f.command("on");
    await new Promise((resolve) => setImmediate(resolve));
    if (stop === "off") await f.command("off");
    else await f.handlers.get("session_shutdown")({}, f.ctx);
    await release();
    await activation;
    assert.equal(f.clients.length, 1);
    assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
  }
});

test("activation cannot overwrite another extension's native tool name", piOnly, async () => {
  const f = await extensionFixture();
  const other = { name: "mcp__cmux_cua__click", description: "Unrelated owner" };
  f.tools.set(other.name, other);
  await f.command("on");
  assert.equal(f.tools.get(other.name), other);
  assert.equal(f.clients[0].client.closed, true);
  assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
});

test("structured element tokens reach model content without duplicating image bytes or credentials", async () => {
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? {
    content: [{ type: "text", text: "Synthetic AX tree" }, { type: "image", mimeType: "image/png", data: "eA==" }],
    structuredContent: { elements: [{ element_token: "synthetic-element", value: credential }], screenshot: "eA==" },
  } : undefined });
  await f.client.discover();
  const result = await f.client.call("get_window_state", {}, undefined, async () => {});
  assert.deepEqual(JSON.parse(result.content.at(-1).text), { elements: [{ element_token: "synthetic-element", value: "[redacted cmux credential]" }], screenshot: "[image returned separately]" });
  await f.client.close();
});

test("spawn failure, stdout EOF and catalog change terminate transport without helper recovery", async () => {
  for (const event of ["spawn-error", "eof", "catalog-change"]) {
    const f = proxyFixture({ respond: () => "hang" });
    const result = f.client.discover();
    setImmediate(() => {
      if (event === "spawn-error") f.child.emit("error", new Error(credential));
      else if (event === "eof") f.child.stdout.end();
      else f.child.stdout.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/tools/list_changed" }) + "\n");
    });
    await assert.rejects(result, (error) => !error.message.includes(credential));
    await f.client.close();
    assert.equal(f.spawnCount, 1);
  }
});

test("cleanup escalates only for its unresponsive proxy", async () => {
  const f = proxyFixture({ ignoreEof: true });
  await f.client.discover();
  await f.client.close();
  assert.deepEqual(f.kills, ["SIGTERM"]);
});

test("native bootstrap schema fixture compiles and validates through installed Pi without rewriting", piOnly, async () => {
  const fixture = JSON.parse(readFileSync(join(repo, "scripts/tests/fixtures/cmux-cua-native-tools.json"), "utf8"));
  assert.deepEqual(fixture.tools.map((tool) => tool.name).sort(), [...desktopTools].sort());
  const { Compile } = await import(pathToFileURL(join(process.env.PI_PACKAGE_ROOT, "node_modules/typebox/build/compile/index.mjs")));
  const { validateToolArguments } = await import(pathToFileURL(join(process.env.PI_PACKAGE_ROOT, "node_modules/@earendil-works/pi-ai/dist/utils/validation.js")));
  const original = JSON.stringify(fixture.tools);
  for (const tool of fixture.tools) Compile(tool.inputSchema);
  const state = fixture.tools.find((tool) => tool.name === "get_window_state");
  const definition = { name: state.name, parameters: state.inputSchema };
  assert.deepEqual(validateToolArguments(definition, { name: state.name, arguments: { pid: 123, window_id: 456 } }), { pid: 123, window_id: 456 });
  assert.throws(() => validateToolArguments(definition, { name: state.name, arguments: { pid: 123 } }));
  assert.throws(() => validateToolArguments(definition, { name: state.name, arguments: { pid: 123, window_id: 456, unknown: true } }));
  assert.equal(JSON.stringify(fixture.tools), original);
  const f = proxyFixture({ catalog: fixture.tools });
  assert.deepEqual(await f.client.discover(), fixture.tools);
  await f.client.close();
});

test("pipe failures settle pending calls and never leak stderr or restart", async () => {
  for (const pipe of ["stdin", "stdout", "stderr"]) {
    const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? "hang" : undefined });
    await f.client.discover();
    const result = f.client.call("click", {}, undefined, async () => {});
    setImmediate(() => f.child[pipe].emit("error", new Error(credential)));
    await assert.rejects(result, (error) => !error.message.includes(credential));
    await f.client.close();
    assert.equal(f.spawnCount, 1);
  }
});

test("off while authority resolution is pending leaves no proxy or active desktop tools", piOnly, async () => {
  const f = await extensionFixture();
  let release;
  f.verifier(() => new Promise((resolve) => { release = () => resolve(authority); }));
  const activation = f.command("on");
  await new Promise((resolve) => setImmediate(resolve));
  await f.command("off");
  release();
  await activation;
  assert.equal(f.clients.length, 0);
  assert.deepEqual(f.active(), ["read", "mcp__cmux_cua__unrelated"]);
});

test("MCP rejection preserves bounded redacted diagnostics and closes the connection", async () => {
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? "hang" : undefined });
  await f.client.discover();
  const result = f.client.call("click", {}, undefined, async () => {});
  setImmediate(() => f.child.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: f.calls.at(-1).id, error: { code: -32603, message: `Synthetic permission gate ${credential}` } }) + "\n"));
  await assert.rejects(result, (error) => error.message.includes("-32603") && error.message.includes("permission gate") && error.message.includes("[redacted cmux credential]") && !error.message.includes(credential));
  await f.client.close();
});

test("text-only models cannot activate or continue desktop operations", piOnly, async () => {
  const f = await extensionFixture();
  f.ctx.model.input = ["text"];
  await f.command("on");
  assert.equal(f.clients.length, 0);
  f.ctx.model.input = ["text", "image"];
  await f.command("on");
  f.ctx.model.input = ["text"];
  await assert.rejects(f.tools.get("mcp__cmux_cua__click").execute("synthetic", {}, undefined, undefined, f.ctx), /No desktop operation/);
  assert.equal(f.clients[0].calls.some((call) => call.method === "tools/call"), false);
});

test("a CLI cannot claim an unrelated application executable as its parent authority", async () => {
  const f = authorityFixture();
  f.identity.app_executable_path = "/foreign/cmux.app/Contents/MacOS/cmux";
  await assert.rejects(resolveAuthority(f.options));
});

test("structured JSON keys are redacted and formatted text does not duplicate native structured data", async () => {
  const structured = { [credential]: "synthetic", element_token: "e:17" };
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? { content: [{ type: "text", text: JSON.stringify(structured, null, 2) }], structuredContent: structured } : undefined });
  await f.client.discover();
  const result = await f.client.call("get_window_state", {}, undefined, async () => {});
  assert.equal(result.content.length, 1);
  assert.equal(JSON.stringify(result).includes(credential), false);
  assert.equal(JSON.parse(result.content[0].text).element_token, "e:17");
  await f.client.close();
});

test("an image payload containing the credential is never returned", async () => {
  const f = proxyFixture({ respond: (req) => req.method === "tools/call" ? { content: [{ type: "image", data: credential, mimeType: "image/png" }] } : undefined });
  await f.client.discover();
  await assert.rejects(f.client.call("get_window_state", {}, undefined, async () => {}), (error) => !error.message.includes(credential));
  assert.equal(f.client.closed, true);
  await f.client.close();
});

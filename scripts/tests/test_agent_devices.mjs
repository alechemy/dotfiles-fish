import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { registerDevices } from "../../stow/pi/.pi/agent/extensions/agent-devices/lifecycle.mjs";

function fixture() {
  const handlers = new Map();
  const calls = [];
  const tools = new Map();
  let session = "session-a";
  let next = 0;
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    registerCommand() {},
    on(event, handler) { handlers.set(event, handler); },
  };
  const ctx = { sessionManager: { getSessionId: () => session } };
  const invoke = async (args) => {
    calls.push(args);
    return args[0] === "acquire" ? { lease: `lease-${++next}`, target: "synthetic-device" } : [];
  };
  registerDevices(pi, { parameters: {}, pid: 123, invoke });
  return {
    calls, ctx, handlers, tools,
    switchSession(value) { session = value; },
    execute(params, signal) { return tools.get("agent_device").execute("call", params, signal, undefined, ctx); },
  };
}

const acquire = { action: "acquire", platform: "ios", device: "synthetic-device" };

test("the installed Pi loader accepts the extension", {
  skip: !process.env.PI_PACKAGE_ROOT && "Set PI_PACKAGE_ROOT to the installed Pi package",
}, async () => {
  const home = mkdtempSync(join(tmpdir(), "agent-devices-loader-"));
  const previous = process.env.PI_CODING_AGENT_DIR;
  try {
    process.env.PI_CODING_AGENT_DIR = join(home, ".pi/agent");
    const { loadExtensions } = await import(pathToFileURL(join(process.env.PI_PACKAGE_ROOT, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([resolve("stow/pi/.pi/agent/extensions/agent-devices/index.ts")], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    assert.ok(loaded.extensions[0].tools.has("agent_device"));
    assert.ok(loaded.extensions[0].commands.has("agent-devices"));
  } finally {
    if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previous;
    rmSync(home, { recursive: true, force: true });
  }
});

test("registration starts no timers or helper processes", () => {
  const f = fixture();
  assert.deepEqual(f.calls, []);
  assert.equal(f.tools.get("agent_device").executionMode, "sequential");
});

test("acquisition binds ownership to the current session and process", async () => {
  const f = fixture();
  await f.execute(acquire);
  assert.deepEqual(f.calls[0], ["acquire", "ios", "synthetic-device", "--owner-pid", "123", "--session", "session-a"]);
});

test("session shutdown is scoped and idempotent", async () => {
  const f = fixture();
  await f.execute(acquire);
  f.switchSession("session-b");
  await f.execute(acquire);
  await f.handlers.get("session_shutdown")({}, f.ctx);
  await f.handlers.get("session_shutdown")({}, f.ctx);
  assert.equal(f.calls.filter((args) => args[0] === "release-session").length, 1);
  assert.deepEqual(f.calls.at(-1), ["release-session", "--session", "session-b", "--owner-pid", "123"]);
  f.switchSession("session-a");
  await f.handlers.get("session_shutdown")({}, f.ctx);
  assert.deepEqual(f.calls.at(-1), ["release-session", "--session", "session-a", "--owner-pid", "123"]);
});

test("session switching releases the old session", async () => {
  const f = fixture();
  await f.execute(acquire);
  await f.handlers.get("session_before_switch")({}, f.ctx);
  assert.equal(f.calls.at(-1)[0], "release-session");
});

test("one session cannot release or renew another session's lease", async () => {
  const f = fixture();
  await f.execute(acquire);
  f.switchSession("session-b");
  for (const action of ["release", "renew", "hold"]) {
    await assert.rejects(f.execute({ action, lease: "lease-1" }), /this session/);
  }
  assert.equal(f.calls.length, 1);
});

test("manual QA holds are explicit and bounded by the helper", async () => {
  const f = fixture();
  await f.execute(acquire);
  await f.execute({ action: "hold", lease: "lease-1", minutes: 45 });
  assert.deepEqual(f.calls.at(-1), ["hold", "lease-1", "--minutes", "45"]);
});

test("cancellation before acquisition has no side effects", async () => {
  const f = fixture();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(f.execute(acquire, controller.signal), /cancelled/);
  assert.deepEqual(f.calls, []);
});

test("cancellation during acquisition releases the completed lease", async () => {
  const f = fixture();
  const controller = new AbortController();
  const pending = f.execute(acquire, controller.signal);
  controller.abort();
  await assert.rejects(pending, /ownership released/);
  assert.deepEqual(f.calls.at(-1), ["release", "lease-1"]);
});

test("an acquisition completing after session shutdown is released", async () => {
  const f = fixture();
  const pending = f.execute(acquire);
  await f.handlers.get("session_shutdown")({}, f.ctx);
  await assert.rejects(pending, /session changed/);
  assert.deepEqual(f.calls.at(-1), ["release", "lease-1"]);
});

test("there is no cleanup at turn boundaries", () => {
  const f = fixture();
  for (const event of ["agent_end", "agent_settled", "turn_end"]) assert.equal(f.handlers.has(event), false);
});

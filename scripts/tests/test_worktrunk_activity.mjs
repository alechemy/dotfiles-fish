import assert from "node:assert/strict";
import { createConnection } from "node:net";
import test from "node:test";
import extension from "../../stow/worktrunk/.pi/agent/extensions/worktrunk.ts";

delete process.env.CMUX_WORKSPACE_ID;
delete process.env.CMUX_SURFACE_ID;

function fixture({ mode = "tui", fail = false } = {}) {
  const handlers = new Map();
  const reports = [];
  const warnings = [];
  const messages = [];
  let idle = true;
  extension({
    on: (name, handler) => handlers.set(name, handler),
    exec: async (command, args, options) => {
      reports.push({ command, args, options });
      return { code: fail ? 1 : 0 };
    },
    sendUserMessage: (message) => messages.push(message),
  });
  const context = {
    mode, cwd: "/synthetic/worktree", isIdle: () => idle,
    sessionManager: { getSessionId: () => "session-fixture" },
    ui: { notify: (text) => warnings.push(text) },
  };
  return {
    handlers, reports, warnings, messages,
    setIdle(value) { idle = value; },
    emit(name) { return handlers.get(name)?.({ title: "Private dialog content" }, context); },
  };
}

test("headless sessions neither report markers nor open timers", async () => {
  for (const mode of ["rpc", "json", "print"]) {
    const f = fixture({ mode });
    for (const event of ["session_start", "agent_start", "ui_prompt_start", "ui_prompt_end", "agent_settled", "session_shutdown"]) {
      await f.emit(event);
    }
    assert.deepEqual(f.reports, []);
  }
});

test("reports settled lifecycle and prompt spans without dialog content", async () => {
  const f = fixture();
  await f.emit("session_start");
  f.setIdle(false);
  await f.emit("agent_start");
  await f.emit("ui_prompt_start");
  await f.emit("ui_prompt_end");
  assert.equal(f.handlers.has("agent_end"), false);
  f.setIdle(true);
  await f.emit("agent_settled");
  await f.emit("session_shutdown");
  await f.emit("session_shutdown");
  await f.emit("ui_prompt_end");
  assert.deepEqual(f.reports.map((r) => r.args[3]), ["idle", "working", "blocked", "working", "idle", "clear"]);
  for (const report of f.reports) {
    assert.equal(report.command, "wt-pi");
    assert.match(report.args[1], /^[a-f0-9]{32}$/);
    assert.equal(report.args[2], String(process.pid));
    assert.deepEqual(report.options, { cwd: "/synthetic/worktree", timeout: 2000 });
  }
  assert.equal(new Set(f.reports.map((r) => r.args[1])).size, 1);
  assert.equal(JSON.stringify(f.reports).includes("Private"), false);
});

test("overlapping prompt events and shutdown remain ordered", async () => {
  const f = fixture();
  await f.emit("session_start");
  const start = f.emit("ui_prompt_start");
  const end = f.emit("ui_prompt_end");
  const stop = f.emit("session_shutdown");
  await Promise.all([start, end, stop]);
  assert.deepEqual(f.reports.map((r) => r.args[3]), ["idle", "blocked", "idle", "clear"]);
});

test("shutdown during startup does not leave a heartbeat timer", async (t) => {
  const timer = t.mock.method(globalThis, "setInterval", () => ({ unref() {} }));
  const f = fixture();
  await Promise.all([f.emit("session_start"), f.emit("session_shutdown")]);
  assert.equal(timer.mock.callCount(), 0);
  assert.deepEqual(f.reports.map((r) => r.args[3]), ["idle", "clear"]);
});

test("failures warn once without failing the session", async () => {
  const f = fixture({ fail: true });
  await f.emit("session_start");
  await f.emit("agent_start");
  await f.emit("session_shutdown");
  assert.equal(f.warnings.length, 1);
  assert.equal(f.warnings[0].includes("wt pi list"), true);
});

test("cmux identity wins over stale legacy environment variables", async () => {
  const previous = Object.fromEntries(
    ["HERDR_ENV", "HERDR_PANE_ID", "CMUX_WORKSPACE_ID", "CMUX_SURFACE_ID"].map((key) => [key, process.env[key]]),
  );
  Object.assign(process.env, {
    HERDR_ENV: "1",
    HERDR_PANE_ID: "pane-fixture",
    CMUX_WORKSPACE_ID: "workspace-fixture",
    CMUX_SURFACE_ID: "surface-fixture",
  });
  const f = fixture();
  try {
    await f.emit("session_start");
    const args = f.reports[0].args;
    assert.equal(args[args.indexOf("--backend") + 1], "cmux");
    assert.equal(args.includes("--feedback-socket"), true);
    await f.emit("session_shutdown");
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("cmux feedback receiver checks identity and acknowledges delivery", async () => {
  const previous = {
    workspace: process.env.CMUX_WORKSPACE_ID,
    surface: process.env.CMUX_SURFACE_ID,
    herdr: process.env.HERDR_ENV,
    pane: process.env.HERDR_PANE_ID,
  };
  delete process.env.HERDR_ENV;
  delete process.env.HERDR_PANE_ID;
  process.env.CMUX_WORKSPACE_ID = "workspace-fixture";
  process.env.CMUX_SURFACE_ID = "surface-fixture";
  const f = fixture();
  try {
    await f.emit("session_start");
    const report = f.reports[0].args;
    const socketPath = report[report.indexOf("--feedback-socket") + 1];
    const socketToken = report[report.indexOf("--feedback-token") + 1];
    const response = await new Promise((resolve, reject) => {
      const client = createConnection(socketPath);
      let output = "";
      client.on("connect", () => client.end(`${JSON.stringify({
        request_id: "request-1",
        token: socketToken,
        session_id: "session-fixture",
        workspace_id: "workspace-fixture",
        surface_id: "surface-fixture",
        text: "Synthetic review feedback",
      })}\n`));
      client.on("data", (chunk) => { output += chunk; });
      client.on("end", () => resolve(JSON.parse(output)));
      client.on("error", reject);
    });
    assert.deepEqual(response, { request_id: "request-1", status: "delivered" });
    assert.deepEqual(f.messages, ["Synthetic review feedback"]);
    await f.emit("session_shutdown");
  } finally {
    if (previous.workspace === undefined) delete process.env.CMUX_WORKSPACE_ID;
    else process.env.CMUX_WORKSPACE_ID = previous.workspace;
    if (previous.surface === undefined) delete process.env.CMUX_SURFACE_ID;
    else process.env.CMUX_SURFACE_ID = previous.surface;
    if (previous.herdr === undefined) delete process.env.HERDR_ENV;
    else process.env.HERDR_ENV = previous.herdr;
    if (previous.pane === undefined) delete process.env.HERDR_PANE_ID;
    else process.env.HERDR_PANE_ID = previous.pane;
  }
});

test("concurrent extension instances have different identities", async () => {
  const a = fixture();
  const b = fixture();
  await a.emit("session_start");
  await b.emit("session_start");
  await a.emit("session_shutdown");
  await b.emit("session_shutdown");
  assert.notEqual(a.reports[0].args[1], b.reports[0].args[1]);
});

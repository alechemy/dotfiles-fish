import assert from "node:assert/strict";
import test from "node:test";
import extension from "../../stow/worktrunk/.pi/agent/extensions/worktrunk.ts";

function fixture({ mode = "tui", fail = false } = {}) {
  const handlers = new Map();
  const reports = [];
  const warnings = [];
  let idle = true;
  extension({
    on: (name, handler) => handlers.set(name, handler),
    exec: async (command, args, options) => {
      reports.push({ command, args, options });
      return { code: fail ? 1 : 0 };
    },
  });
  const context = {
    mode, cwd: "/synthetic/worktree", isIdle: () => idle,
    ui: { notify: (text) => warnings.push(text) },
  };
  return {
    handlers, reports, warnings,
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
  assert.deepEqual(f.reports.map((r) => r.args.at(-1)), ["idle", "working", "blocked", "working", "idle", "clear"]);
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
  assert.deepEqual(f.reports.map((r) => r.args.at(-1)), ["idle", "blocked", "idle", "clear"]);
});

test("shutdown during startup does not leave a heartbeat timer", async (t) => {
  const timer = t.mock.method(globalThis, "setInterval", () => ({ unref() {} }));
  const f = fixture();
  await Promise.all([f.emit("session_start"), f.emit("session_shutdown")]);
  assert.equal(timer.mock.callCount(), 0);
  assert.deepEqual(f.reports.map((r) => r.args.at(-1)), ["idle", "clear"]);
});

test("failures warn once without failing the session", async () => {
  const f = fixture({ fail: true });
  await f.emit("session_start");
  await f.emit("agent_start");
  await f.emit("session_shutdown");
  assert.equal(f.warnings.length, 1);
  assert.equal(f.warnings[0].includes("wt pi list"), true);
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

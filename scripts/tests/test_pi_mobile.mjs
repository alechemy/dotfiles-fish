import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import mobileExtension from "../../stow/pi-mobile/.config/pi-mobile/mobile.ts";

function fixture(t, enabled = true) {
  const home = mkdtempSync(join(tmpdir(), "pi-mobile-extension-"));
  const original = { ...process.env };
  if (enabled) process.env.PI_MOBILE = "1";
  else delete process.env.PI_MOBILE;
  process.env.TMUX_PANE = "%7";
  t.after(() => {
    for (const key of Object.keys(process.env)) if (!(key in original)) delete process.env[key];
    Object.assign(process.env, original);
    rmSync(home, { recursive: true, force: true });
  });
  const handlers = new Map();
  const calls = [];
  const notices = [];
  let shutdowns = 0;
  const api = {
    on: (event, handler) => handlers.set(event, handler),
    exec: async (...args) => { calls.push(args); return { code: 0, stdout: "", stderr: "" }; },
  };
  const ctx = {
    cwd: home,
    sessionManager: {
      getHeader: () => ({ cwd: home }),
      getSessionName: () => "Fictional task",
      getSessionId: () => "synthetic-session",
    },
    ui: { notify: (...args) => notices.push(args) },
    shutdown: () => shutdowns++,
  };
  mobileExtension(api);
  return { home, handlers, calls, notices, ctx, shutdowns: () => shutdowns };
}

test("normal desktop loading registers no mobile handlers", t => {
  assert.equal(fixture(t, false).handlers.size, 0);
});

test("mobile startup uses runtime cwd and names only its own tmux window", async t => {
  const f = fixture(t);
  assert.notEqual(process.cwd(), f.ctx.cwd);
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.shutdowns(), 0);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[0][0], "tmux");
  assert.deepEqual(f.calls[0][1].slice(0, 3), ["rename-window", "-t", "%7"]);
  assert.match(f.calls[0][1][3], /Fictional task/);
  assert.equal(f.calls[1][0], "tmux");
  assert.deepEqual(f.calls[1][1], ["set-option", "-p", "-t", "%7", "@pi_mobile_cwd", f.ctx.cwd]);
  assert.equal(f.handlers.get("tool_call")({}, f.ctx), undefined);
});

test("symlinked working directories preserve the same identity", async t => {
  const f = fixture(t);
  const project = join(f.home, "project");
  const alias = join(f.home, "alias");
  mkdirSync(project);
  symlinkSync(project, alias);
  f.ctx.cwd = alias;
  f.ctx.sessionManager.getHeader = () => ({ cwd: project });
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.shutdowns(), 0);
});

test("a missing original directory refuses startup, input and tools", async t => {
  const f = fixture(t);
  f.ctx.sessionManager.getHeader = () => ({ cwd: join(f.home, "missing") });
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.shutdowns(), 1);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.handlers.get("input")({}, f.ctx), { action: "handled" });
  assert.equal(f.handlers.get("tool_call")({}, f.ctx).block, true);
});

test("a fallback to another existing directory is refused", async t => {
  const f = fixture(t);
  const project = join(f.home, "project");
  mkdirSync(project);
  f.ctx.sessionManager.getHeader = () => ({ cwd: project });
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.shutdowns(), 1);
  assert.equal(f.calls.length, 0);
});

test("missing session headers and invalid tmux pane IDs never target another window", async t => {
  const f = fixture(t);
  process.env.TMUX_PANE = "untrusted-pane";
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.calls.length, 0);
  f.ctx.sessionManager.getHeader = () => null;
  await f.handlers.get("session_start")({}, f.ctx);
  assert.equal(f.shutdowns(), 1);
});

test("Pi's installed extension loader accepts the mobile entry point", {
  skip: !process.env.PI_PACKAGE_ROOT && "Set PI_PACKAGE_ROOT for the loader check",
}, async t => {
  const f = fixture(t);
  process.env.PI_CODING_AGENT_DIR = join(f.home, "agent");
  const { loadExtensions } = await import(pathToFileURL(resolve(process.env.PI_PACKAGE_ROOT, "dist/core/extensions/loader.js")));
  const loaded = await loadExtensions([resolve("stow/pi-mobile/.config/pi-mobile/mobile.ts")], f.home);
  assert.deepEqual(loaded.errors, []);
  assert.equal(loaded.extensions.length, 1);
  assert.deepEqual([...loaded.extensions[0].handlers.keys()].sort(), ["input", "session_start", "tool_call"]);
});

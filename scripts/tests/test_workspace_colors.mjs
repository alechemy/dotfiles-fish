import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { automaticColor, normalizeColor, palette, readOverrides, saveOverride, workspaceIdentity } from "../../stow/pi/.pi/agent/extensions/workspace-colors/colors.mjs";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const entry = join(repo, "stow/pi/.pi/agent/extensions/workspace-colors/index.ts");

function temporary(t) {
  const path = mkdtempSync(join(tmpdir(), "workspace-colors-"));
  t.after(() => rmSync(path, { recursive: true, force: true }));
  return path;
}

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

test("automatic colors are stable and use the fixed palette", () => {
  assert.equal(automaticColor("git:/example/.git"), "#0E6B8C");
  assert.equal(automaticColor("git:/example/.git"), automaticColor("git:/example/.git"));
  const colors = new Set(Array.from({ length: 100 }, (_, i) => automaticColor(`git:/project-${i}/.git`)));
  assert.ok(colors.size > 10);
  assert.ok([...colors].every((color) => Object.values(palette).includes(color)));
});

test("color names and hex values normalize without accepting arbitrary input", () => {
  assert.equal(normalizeColor(" TeAl "), "#006B6B");
  assert.equal(normalizeColor("#abcdef"), "#ABCDEF");
  for (const value of ["auto", "__proto__", "#123", "#12345678", "blue; touch file", null]) {
    assert.throws(() => normalizeColor(value));
  }
});

test("main checkout, linked worktrees, subdirectories, and symlinks share one identity", async (t) => {
  const root = temporary(t);
  const main = join(root, "main");
  const worktree = join(root, "task");
  const alias = join(root, "alias");
  mkdirSync(main);
  git(main, "init", "-q");
  git(main, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "Initial");
  git(main, "worktree", "add", "-qb", "task", worktree);
  mkdirSync(join(worktree, "nested"));
  symlinkSync(worktree, alias, "dir");
  const identity = await workspaceIdentity(main);
  assert.match(identity, /^git:.*\/main\/\.git$/);
  for (const path of [worktree, join(worktree, "nested"), alias]) {
    assert.equal(await workspaceIdentity(path), identity);
  }
  const other = join(root, "other");
  mkdirSync(other);
  git(other, "init", "-q");
  assert.notEqual(await workspaceIdentity(other), identity);
});

test("bare repositories and separately located Git metadata are supported", async (t) => {
  const root = temporary(t);
  const bare = join(root, "bare.git");
  git(root, "init", "-q", "--bare", bare);
  assert.equal(await workspaceIdentity(bare), `git:${git(bare, "rev-parse", "--absolute-git-dir").trim()}`);
  const main = join(root, "main");
  const metadata = join(root, "metadata");
  mkdirSync(main);
  git(main, "init", "-q", `--separate-git-dir=${metadata}`);
  assert.match(await workspaceIdentity(main), /\/metadata$/);
});

test("non-repository directories use their canonical paths", async (t) => {
  const root = temporary(t);
  const alias = `${root}-alias`;
  symlinkSync(root, alias, "dir");
  t.after(() => rmSync(alias));
  assert.match(await workspaceIdentity(root), /^dir:/);
  assert.equal(await workspaceIdentity(root), await workspaceIdentity(alias));
});

test("concurrent saves preserve other overrides and auto removes only its own override", async (t) => {
  const path = join(temporary(t), "agent/workspace-colors.json");
  assert.deepEqual(await readOverrides(path), {});
  await Promise.all(Array.from({ length: 8 }, (_, i) => saveOverride(path, `git:/repo-${i}/.git`, "blue")));
  assert.equal(Object.keys(await readOverrides(path)).length, 8);
  assert.equal(statSync(path).mode & 0o777, 0o600);
  await saveOverride(path, "git:/repo-0/.git", null);
  const overrides = await readOverrides(path);
  assert.equal(Object.keys(overrides).length, 7);
  assert.equal(overrides["git:/repo-0/.git"], undefined);
  assert.equal(overrides["git:/repo-1/.git"], "#1565C0");
  assert.equal(existsSync(`${path}.lock`), false);
});

test("invalid and symlinked configuration is never replaced", async (t) => {
  const root = temporary(t);
  const path = join(root, "workspace-colors.json");
  for (const text of ["{", '{"version":2,"overrides":{}}', '{"version":1,"overrides":[]}', '{"version":1,"overrides":{"relative":"blue"}}', '{"version":1,"overrides":{"git:/repo/.git":"bad"}}']) {
    writeFileSync(path, text);
    await assert.rejects(saveOverride(path, "git:/repo/.git", "red"));
    assert.equal(readFileSync(path, "utf8"), text);
    assert.equal(existsSync(`${path}.lock`), false);
  }
  rmSync(path);
  const target = join(root, "target.json");
  writeFileSync(target, '{"version":1,"overrides":{}}');
  symlinkSync(target, path);
  await assert.rejects(saveOverride(path, "git:/repo/.git", "red"), /regular file/);
  assert.equal(readFileSync(target, "utf8"), '{"version":1,"overrides":{}}');
});

test("Pi loads the extension quietly and applies saved colors only to its explicit interactive workspace", {
  skip: !process.env.PI_PACKAGE_ROOT && "Set PI_PACKAGE_ROOT to the installed Pi package",
  timeout: 20000,
}, async (t) => {
  const home = temporary(t);
  const environment = { ...process.env };
  const agentDir = join(home, ".pi/agent");
  const bin = join(home, "bin");
  const log = join(home, "cmux.log");
  mkdirSync(agentDir, { recursive: true });
  mkdirSync(bin);
  writeFileSync(join(bin, "cmux"), '#!/usr/bin/env node\nrequire("node:fs").appendFileSync(process.env.CMUX_TEST_LOG, JSON.stringify(process.argv.slice(2)) + "\\n");\nprocess.exit(process.env.CMUX_TEST_FAIL ? 1 : 0);\n', { mode: 0o755 });
  try {
    Object.assign(process.env, {
      PI_CODING_AGENT_DIR: agentDir, PATH: `${bin}:${environment.PATH}`, CMUX_TEST_LOG: log,
      CMUX_WORKSPACE_ID: "11111111-1111-4111-8111-111111111111",
      CMUX_SURFACE_ID: "22222222-2222-4222-8222-222222222222",
    });
    const { loadExtensions } = await import(pathToFileURL(resolve(process.env.PI_PACKAGE_ROOT, "dist/core/extensions/loader.js")));
    const loaded = await loadExtensions([entry], home);
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    assert.equal(existsSync(log), false);
    const extension = loaded.extensions[0];
    const notifications = [];
    const ctx = { cwd: home, mode: "tui", hasUI: true, ui: { notify: (...args) => notifications.push(args) } };
    const start = extension.handlers.get("session_start")[0];
    const command = extension.commands.get("workspace-color").handler;
    const identity = await workspaceIdentity(home);
    const calls = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n").map(JSON.parse) : [];
    for (const mode of ["rpc", "json", "print"]) await start({}, { ...ctx, mode });
    delete process.env.CMUX_SURFACE_ID;
    await start({}, ctx);
    process.env.CMUX_SURFACE_ID = "22222222-2222-4222-8222-222222222222";
    process.env.CMUX_WORKSPACE_ID = "invalid";
    await start({}, ctx);
    assert.equal(calls().length, 0);
    process.env.CMUX_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
    await start({}, ctx);
    assert.deepEqual(calls()[0], ["workspace-action", "--workspace", process.env.CMUX_WORKSPACE_ID, "--action", "set-color", "--color", automaticColor(identity)]);
    assert.equal(notifications.length, 0);
    await command("teal", ctx);
    assert.equal((await readOverrides(join(agentDir, "workspace-colors.json")))[identity], "#006B6B");
    assert.equal(calls().at(-1).at(-1), "#006B6B");
    await start({}, ctx);
    assert.equal(calls().at(-1).at(-1), "#006B6B");
    const count = calls().length;
    await command("status", ctx);
    await command("bogus", ctx);
    assert.equal(calls().length, count);
    assert.equal(notifications.at(-1)[1], "error");
    await command("auto", ctx);
    assert.equal(calls().at(-1).at(-1), automaticColor(identity));
    assert.deepEqual(await readOverrides(join(agentDir, "workspace-colors.json")), {});
    process.env.CMUX_TEST_FAIL = "1";
    await command("purple", ctx);
    assert.match(notifications.at(-1)[0], /Saved.*could not apply/);
    assert.equal((await readOverrides(join(agentDir, "workspace-colors.json")))[identity], "#6A1B9A");
    await start({}, ctx);
    assert.equal(notifications.at(-1)[1], "warning");
  } finally {
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, environment);
  }
});

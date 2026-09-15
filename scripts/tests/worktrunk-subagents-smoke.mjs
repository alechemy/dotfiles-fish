#!/usr/bin/env node
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, copyFileSync, existsSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { createRequire } from "node:module";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const source = join(homedir(), ".pi/agent/local/copilot-delegation/node_modules/pi-subagents/src/runs/shared/worktree.ts");
const piPrefix = execFileSync("brew", ["--prefix", "pi-coding-agent"], { encoding: "utf8" }).trim();
const require = createRequire(join(piPrefix, "libexec/lib/node_modules/@earendil-works/pi-coding-agent/package.json"));
const { createJiti } = require("jiti");
const temporary = mkdtempSync("/tmp/ws-");
const originalEnv = { ...process.env };
try {
  for (const name of Object.keys(process.env)) {
    if (/^(GIT_|PI_|HERDR_|WORKTRUNK_)/.test(name)) delete process.env[name];
  }
  Object.assign(process.env, {
    HOME: temporary,
    XDG_CONFIG_HOME: join(temporary, ".config"),
    PI_CODING_AGENT_DIR: join(temporary, ".pi/agent"),
    GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1",
    WORKTRUNK_SYSTEM_CONFIG_PATH: "/dev/null", GIT_TERMINAL_PROMPT: "0",
  });
  const config = join(temporary, ".config/worktrunk/config.toml");
  mkdirSync(dirname(config), { recursive: true });
  copyFileSync(join(root, "stow/worktrunk/_seed/.config/worktrunk/config.toml"), config);
  const { createWorktrees, cleanupWorktrees } = await createJiti(import.meta.url).import(source);
  const repo = join(temporary, "repo");
  mkdirSync(repo);
  function git(...args) {
    return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  }
  git("init", "-qb", "main");
  git("config", "user.name", "Fixture");
  git("config", "user.email", "fixture@example.invalid");
  git("config", "commit.gpgsign", "false");
  mkdirSync(join(repo, ".config"));
  writeFileSync(join(repo, ".config/wt.toml"), '[pre-start]\nrefuse = "exit 1"\n');
  writeFileSync(join(repo, "fixture"), "original\n");
  git("add", ".");
  git("commit", "-qm", "fixture");
  const before = git("rev-parse", "HEAD");
  const allocation = createWorktrees(repo, "smoke", 1, { provider: "worktrunk", agents: ["worker"] });
  try {
    const task = allocation.worktrees[0];
    assert.equal(task.provider, "worktrunk");
    assert.match(task.branch, /^pi-subagents\//);
    assert.equal(git("rev-parse", "HEAD"), before);
    assert.equal(git("branch", "--show-current"), "main");
    assert.equal(existsSync(join(config, "../approvals.toml")), false);
    writeFileSync(join(task.path, "fixture"), "child work\n");
    const cleanup = cleanupWorktrees(allocation);
    assert.equal(cleanup.tasks[0].preserved, true);
    assert.equal(existsSync(task.path), true);
  } finally {
    assert.equal(cleanupWorktrees(allocation, { kind: "setup-rollback" }).state, "complete");
  }
  console.log("Subagents Worktrunk smoke passed: native allocator contract, reserved namespace, hooks suppressed and dirty work retained.");
} finally {
  for (const name of Object.keys(process.env)) delete process.env[name];
  Object.assign(process.env, originalEnv);
  rmSync(temporary, { recursive: true, force: true });
}

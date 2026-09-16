#!/usr/bin/env node
// Explicit-candidate, service-free preservation acceptance. Never load the active package.
// node scripts/tests/worktrunk-subagents-smoke.mjs --candidate-root /reviewed/source
// Optional: --pi-loader-root /existing/pi/package (Jiti only; no extension entrypoint).
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync,
  existsSync, realpathSync, rmSync, statSync } from "node:fs";
import { join, dirname, resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../../", import.meta.url));
const usage = "Usage: node worktrunk-subagents-smoke.mjs --candidate-root /reviewed/source [--pi-loader-root /existing/pi/package]";
function argumentsFor(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    if (!["--candidate-root", "--pi-loader-root"].includes(key) || !argv[i + 1] || argv[i + 1].startsWith("--") || options[key]) {
      throw new Error(usage);
    }
    options[key] = resolve(argv[i + 1]);
  }
  if (!options["--candidate-root"]) throw new Error(`${usage}\nA reviewed candidate is required; no installed-package fallback exists.`);
  const candidate = realpathSync(options["--candidate-root"]);
  const packageJson = JSON.parse(readFileSync(join(candidate, "package.json"), "utf8"));
  if (packageJson.name !== "pi-subagents") throw new Error("Candidate package must be pi-subagents.");
  for (const name of ["worktree.ts", "worktree-cleanup-plan.ts"]) {
    if (!statSync(join(candidate, "src/runs/shared", name)).isFile()) throw new Error(`Candidate missing ${name}`);
  }
  return { candidate, loader: options["--pi-loader-root"], version: packageJson.version };
}
let options;
try { options = argumentsFor(process.argv.slice(2)); }
catch (error) { console.error(`Subagents smoke: ${error.message}`); process.exit(2); }

// Allowlist process essentials before any candidate/dependency load. Do not inspect
// provider or credential values. This standalone process does not restore its caller env.
const essentials = {};
for (const key of ["PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "SystemRoot", "COMSPEC", "PATHEXT"]) {
  if (process.env[key] !== undefined) essentials[key] = process.env[key];
}
for (const key of Object.keys(process.env)) delete process.env[key];
Object.assign(process.env, essentials);
const temporary = realpathSync(mkdtempSync("/tmp/ws-candidate-"));
const home = join(temporary, "home");
mkdirSync(home);
Object.assign(process.env, {
  HOME: home, USERPROFILE: home, XDG_CONFIG_HOME: join(home, ".config"),
  XDG_CACHE_HOME: join(home, ".cache"), XDG_STATE_HOME: join(home, ".local/state"),
  PI_CODING_AGENT_DIR: join(home, ".pi/agent"),
  GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1", GIT_TERMINAL_PROMPT: "0",
  GIT_OPTIONAL_LOCKS: "0", GIT_NO_REPLACE_OBJECTS: "1",
  GIT_NO_LAZY_FETCH: "1", GIT_ALLOW_PROTOCOL: "",
  WORKTRUNK_SYSTEM_CONFIG_PATH: "/dev/null", LC_ALL: "C",
  TMPDIR: temporary, TMP: temporary, TEMP: temporary,
});
process.chdir(temporary);
const executable = name => execFileSync("/usr/bin/which", [name], { encoding: "utf8" }).trim();
const realGit = executable("git");
function runGit(cwd, args, expected = 0) {
  const result = spawnSync(realGit, ["-C", cwd, ...args], { encoding: "utf8", timeout: 15000 });
  assert.equal(result.status, expected, `fixture git ${args.join(" ")}: ${result.stderr}`);
  return result.stdout.trim();
}
const sourceFiles = ["src/runs/shared/worktree.ts", "src/runs/shared/worktree-cleanup-plan.ts"];
function sourceIdentity() {
  const candidateGit = args => execFileSync(realGit,
    ["-c", "core.fsmonitor=false", "-C", options.candidate, ...args],
    { stdio: ["ignore", "pipe", "pipe"], timeout: 15000 });
  const digest = bytes => createHash("sha256").update(bytes).digest("hex");
  const commit = candidateGit(["rev-parse", "HEAD"]).toString("utf8").trim();
  const files = Object.fromEntries(sourceFiles.map(name => [name, digest(readFileSync(join(options.candidate, name)))]));
  // Raw blobs avoid worktree diff clean filters, textconv, and fsmonitor hooks.
  const committedFiles = Object.fromEntries(sourceFiles.map(name => [name,
    digest(candidateGit(["cat-file", "blob", `${commit}:${name}`]))]));
  return { commit, version: options.version, files, committedFiles,
    dirtyFiles: sourceFiles.filter(name => files[name] !== committedFiles[name]),
    dirtyFingerprint: digest(JSON.stringify({ committedFiles, files })) };
}

try {
  // Resolve code paths only. Even `brew --prefix` can refresh network metadata.
  const pi = options.loader ? undefined : realpathSync(executable("pi"));
  const loaderCandidates = options.loader ? [options.loader] : [
    resolve(dirname(pi), ".."),
    join(dirname(pi), "../libexec/lib/node_modules/@earendil-works/pi-coding-agent"),
  ];
  const loader = loaderCandidates.find(path => {
    try { return JSON.parse(readFileSync(join(path, "package.json"), "utf8")).name === "@earendil-works/pi-coding-agent"; }
    catch { return false; }
  });
  if (!loader) throw new Error("Cannot identify the existing Pi loader package; pass --pi-loader-root explicitly.");
  const require = createRequire(join(loader, "package.json"));
  const { createJiti } = require("jiti");
  const identity = sourceIdentity();
  const jiti = createJiti(import.meta.url, { fsCache: false });
  const { createWorktrees, diffWorktrees, cleanupWorktrees } = await jiti.import(join(options.candidate, sourceFiles[0]));
  const { buildWorktreeCleanupPlan } = await jiti.import(join(options.candidate, sourceFiles[1]));
  for (const api of [createWorktrees, diffWorktrees, cleanupWorktrees, buildWorktreeCleanupPlan]) assert.equal(typeof api, "function");

  const config = join(home, ".config/worktrunk/config.toml");
  mkdirSync(dirname(config), { recursive: true });
  const seed = readFileSync(join(root, "stow/worktrunk/_seed/.config/worktrunk/config.toml"), "utf8");
  // A contained fixture layout lets both providers exercise the cleanup planner.
  writeFileSync(config, seed.replace(/^worktree-path = .*$/m,
    'worktree-path = "{{ repo_path }}/../managed/{{ repo }}/{{ branch | sanitize }}"'));
  assert.match(readFileSync(config, "utf8"), /managed/);

  const shimDir = join(temporary, "bin"); mkdirSync(shimDir);
  const faultFile = join(temporary, "git-fault.json");
  const shim = join(shimDir, "git.cjs");
  writeFileSync(shim, `const {spawnSync}=require('node:child_process');
const fs=require('node:fs'); const args=process.argv.slice(2);
const fault=fs.existsSync(${JSON.stringify(faultFile)}) ? JSON.parse(fs.readFileSync(${JSON.stringify(faultFile)},'utf8')) : {};
const selected=(fault.kind==='deny-owned-remove' && args.includes('worktree') && args.includes('remove') && args.includes(fault.path)) ||
 (fault.kind==='apply' && args.includes('apply')) ||
 (fault.kind==='status' && args.includes('status')) ||
 (fault.kind==='add' && args.includes('add')) ||
 (fault.kind==='machine-diff' && args.includes('diff') && args.includes('--cached') && !args.includes('--stat') && !args.includes('--numstat'));
if(selected) { fs.appendFileSync(fault.marker,'hit\\n'); process.exit(1); }
const result=spawnSync(${JSON.stringify(realGit)},args,{stdio:'inherit'}); process.exit(result.status??1);
`);
  writeFileSync(join(shimDir, "git"), `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(shim)} "$@"\n`, { mode: 0o755 });
  process.env.PATH = `${shimDir}:${essentials.PATH ?? "/usr/bin:/bin"}`;
  function fault(kind, callback) {
    const marker = join(temporary, "fault-hit"); rmSync(marker, { force: true });
    writeFileSync(faultFile, JSON.stringify({ kind, marker }));
    try { callback(); assert.ok(existsSync(marker), `fault ${kind} was not exercised`); }
    finally { rmSync(faultFile, { force: true }); }
  }

  let sequence = 0;
  function fixture(provider) {
    const dir = join(temporary, `case-${sequence++}`); mkdirSync(dir);
    const repo = join(dir, "repo"); mkdirSync(repo);
    const git = (...args) => runGit(repo, args);
    git("init", "-qb", "main", "--template=");
    git("config", "user.name", "Fixture"); git("config", "user.email", "fixture@example.invalid");
    git("config", "commit.gpgsign", "false"); git("config", "core.hooksPath", join(dir, "no-hooks"));
    mkdirSync(join(repo, "nested")); mkdirSync(join(repo, ".config"));
    writeFileSync(join(repo, ".config/wt.toml"), `[pre-start]\nrefuse = "touch ${join(dir, "unexpected-hook")}; exit 1"\n`);
    writeFileSync(join(repo, "nested/keep.txt"), "original\n");
    writeFileSync(join(repo, "nested/change.bin"), Buffer.from([0, 1, 2, 255]));
    writeFileSync(join(repo, "delete.bin"), Buffer.from([0, 3, 4, 254]));
    writeFileSync(join(repo, ".gitattributes"), "*.bin diff=fixture\n");
    git("add", "."); git("commit", "-qm", "fixture base");
    const base = git("rev-parse", "HEAD");
    const settings = { provider, agents: ["worker"], ...(provider === "native" ? { baseDir: join(dir, "managed") } : {}) };
    const artifacts = join(dir, "artifacts"); mkdirSync(artifacts);
    function allocate(extra = {}, count = 1) {
      const setup = createWorktrees(repo, "fixture", count, { ...settings, ...extra });
      assert.equal(setup.cwd, repo); assert.equal(setup.baseCommit, base);
      for (const task of setup.worktrees) {
        const rel = relative(temporary, realpathSync(task.path));
        assert.ok(rel && rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel), "candidate allocated outside fixture");
        assert.equal(task.provider, provider); assert.match(task.branch, /^pi-subagents\//);
      }
      assert.equal(git("rev-parse", "HEAD"), base); assert.equal(git("branch", "--show-current"), "main");
      assert.equal(existsSync(join(dir, "unexpected-hook")), false);
      assert.equal(existsSync(join(dirname(config), "approvals.toml")), false);
      return setup;
    }
    return { dir, repo, git, base, settings, artifacts, allocate, provider };
  }
  function change(setup) {
    const path = setup.worktrees[0].path;
    writeFileSync(join(path, "nested/change.bin"), Buffer.from([0, 9, 8, 128, 255]));
    writeFileSync(join(path, "added.bin"), Buffer.from([0, 7, 6, 253]));
    rmSync(join(path, "delete.bin"));
    writeFileSync(join(path, "nested/keep.txt"), "child text\n");
  }
  function capture(f, setup) {
    const diffs = diffWorktrees(setup, ["worker"], join(f.artifacts, "diffs"));
    assert.equal(diffs.length, 1); assert.equal(diffs[0].error, undefined);
    assert.ok(diffs[0].filesChanged >= 4);
    return diffs;
  }
  function handoff(f, setup, diffs) {
    const task = setup.worktrees[0];
    const outputPath = join(f.artifacts, "output.md"); writeFileSync(outputPath, "Fictional result.\n");
    const manifest = join(f.artifacts, "handoff.json");
    writeFileSync(manifest, JSON.stringify({ version: 1, runId: "fixture", source: "foreground",
      mode: "parallel", cwd: f.repo, createdAt: 1, updatedAt: 1,
      groups: [{ stepIndex: 0, repoRoot: f.repo, baseCommit: setup.baseCommit,
        children: [{ index: 0, taskIndex: 0, agent: "worker", status: "completed", summary: "done", outputPath,
          patch: { ...diffs[0], path: diffs[0].patchPath, changed: true } }],
        cleanup: { state: "partial", tasks: [{ index: 0, path: task.path, branch: task.branch,
          preserved: true, worktreeRemoved: false, branchRemoved: false, reason: "captured handoff" }] } }] }));
    return manifest;
  }
  function preserved(f, setup, report) {
    assert.equal(report.state, "partial");
    for (const task of setup.worktrees) {
      assert.equal(existsSync(task.path), true, "checkout was removed without proof");
      assert.ok(f.git("show-ref", "--verify", `refs/heads/${task.branch}`), "branch was removed without proof");
      const result = report.tasks.find(row => row.index === task.index);
      assert.equal(result.preserved, true); assert.equal(result.worktreeRemoved, false); assert.equal(result.branchRemoved, false);
    }
  }
  function removed(f, setup, report) {
    assert.equal(report.state, "complete");
    for (const task of setup.worktrees) {
      assert.equal(existsSync(task.path), false);
      runGit(f.repo, ["show-ref", "--verify", "--quiet", `refs/heads/${task.branch}`], 1);
    }
  }
  function preserveIntent(diffs, manifest) { return { kind: "preserve", capturedDiffs: diffs, handoffManifestPath: manifest }; }
  function plan(f, setup, manifest) {
    return buildWorktreeCleanupPlan({ repo: f.repo, handoffPath: manifest, worktreeBaseDir: join(f.dir, "managed"),
      foregroundRunOwnership: () => "terminal" }).entries.find(entry => entry.path === setup.worktrees[0].path);
  }

  await test("reviewed Subagents candidate preservation matrix", async t => {
    t.diagnostic(JSON.stringify(identity));
    for (const provider of ["native", "worktrunk"]) {
      await t.test(`${provider}: binary add/change/delete replay survives display and transform settings`, () => {
        const f = fixture(provider), setup = f.allocate(), task = setup.worktrees[0];
        const transform = join(f.dir, "unexpected-transform");
        const command = join(f.dir, "transform.sh");
        writeFileSync(command, `#!/bin/sh\ntouch ${JSON.stringify(transform)}\nprintf 'not a machine patch\\n'\n`, { mode: 0o755 });
        for (const [key, value] of Object.entries({ "color.ui": "always", "color.diff": "always",
          "diff.noprefix": "true", "diff.mnemonicPrefix": "true", "diff.srcPrefix": "OLD/",
          "diff.dstPrefix": "NEW/", "diff.relative": "true", "diff.external": command,
          "diff.fixture.textconv": command })) f.git("config", key, value);
        change(setup);
        // The integration checkout can advance without changing the recorded base.
        writeFileSync(join(f.repo, "integration-only.txt"), "not task work\n");
        f.git("add", "integration-only.txt"); f.git("commit", "-qm", "integration advanced");
        assert.notEqual(f.git("rev-parse", "HEAD"), setup.baseCommit);
        const diffs = capture(f, setup), manifest = handoff(f, setup, diffs);
        const patch = readFileSync(diffs[0].patchPath, "utf8");
        assert.match(patch, /GIT binary patch/); assert.ok(!patch.includes("\u001b"));
        assert.match(patch, /diff --git a\/nested\/change.bin b\/nested\/change.bin/);
        assert.equal(existsSync(transform), false, "external diff/textconv ran");
        const expectedTree = runGit(task.path, ["write-tree"]);
        removed(f, setup, cleanupWorktrees(setup, preserveIntent(diffs, manifest)));
        const replay = join(f.dir, "replay");
        f.git("worktree", "add", "--detach", replay, setup.baseCommit);
        runGit(replay, ["apply", "--check", "--binary", diffs[0].patchPath]);
        runGit(replay, ["apply", "--binary", diffs[0].patchPath]);
        assert.deepEqual(readFileSync(join(replay, "nested/change.bin")), Buffer.from([0, 9, 8, 128, 255]));
        assert.deepEqual(readFileSync(join(replay, "added.bin")), Buffer.from([0, 7, 6, 253]));
        assert.equal(existsSync(join(replay, "delete.bin")), false);
        assert.equal(existsSync(join(replay, "integration-only.txt")), false);
        runGit(replay, ["add", "-A"]); assert.equal(runGit(replay, ["write-tree"]), expectedTree);
        assert.equal(readFileSync(diffs[0].patchPath, "utf8"), patch);
        assert.equal(existsSync(transform), false);
      });
      await t.test(`${provider}: clean no-work cleanup remains permitted`, () => {
        const f = fixture(provider), setup = f.allocate(); removed(f, setup, cleanupWorktrees(setup));
      });
      await t.test(`${provider}: uncaptured work and missing handoff retain checkout and branch`, () => {
        const f = fixture(provider), setup = f.allocate(); change(setup);
        preserved(f, setup, cleanupWorktrees(setup));
        capture(f, setup); preserved(f, setup, cleanupWorktrees(setup));
      });
      for (const damage of ["corrupt", "missing", "empty-error", "valid-subset", "postcapture"]) {
        await t.test(`${provider}: ${damage} evidence cannot authorize cleanup`, () => {
          const f = fixture(provider), setup = f.allocate(); change(setup);
          const diffs = capture(f, setup), manifest = handoff(f, setup, diffs), task = setup.worktrees[0];
          if (damage === "corrupt") writeFileSync(diffs[0].patchPath, "nonempty but not a patch\n");
          if (damage === "missing") rmSync(diffs[0].patchPath);
          if (damage === "empty-error") diffs[0].error = "";
          if (damage === "valid-subset") writeFileSync(diffs[0].patchPath,
            runGit(task.path, ["diff", "--cached", "--binary", "--no-color", "--no-ext-diff", "--no-textconv", f.base, "--", "nested/keep.txt"]) + "\n");
          if (damage === "postcapture") {
            writeFileSync(join(task.path, "nested/change.bin"), Buffer.from([0, 22, 23]));
            writeFileSync(join(task.path, "later.bin"), Buffer.from([0, 24, 25]));
          }
          const index = runGit(task.path, ["write-tree"]);
          preserved(f, setup, cleanupWorktrees(setup, preserveIntent(diffs, manifest)));
          assert.equal(runGit(task.path, ["write-tree"]), index, "validation changed real index");
          if (damage === "postcapture") assert.deepEqual(readFileSync(join(task.path, "later.bin")), Buffer.from([0, 24, 25]));
        });
      }
      for (const kind of ["add", "machine-diff", "apply"]) {
        await t.test(`${provider}: ${kind} capture failure retains both resources`, () => {
          const f = fixture(provider), setup = f.allocate(); change(setup);
          fault(kind, () => {
            const diffs = diffWorktrees(setup, ["worker"], join(f.artifacts, "diffs"));
            assert.equal(diffs.length, 1); assert.notEqual(diffs[0].error, undefined);
            preserved(f, setup, cleanupWorktrees(setup, preserveIntent(diffs, handoff(f, setup, diffs))));
          });
        });
      }
      await t.test(`${provider}: patch write failure retains both resources`, () => {
        const f = fixture(provider), setup = f.allocate(); change(setup);
        mkdirSync(join(f.artifacts, "diffs/task-0-worker.patch"), { recursive: true });
        const diffs = diffWorktrees(setup, ["worker"], join(f.artifacts, "diffs"));
        assert.notEqual(diffs[0].error, undefined); preserved(f, setup, cleanupWorktrees(setup));
      });
      for (const kind of ["apply", "machine-diff", "add"]) {
        await t.test(`${provider}: ${kind} cleanup-validation failure preserves both resources`, () => {
          const f = fixture(provider), setup = f.allocate(); change(setup);
          const diffs = capture(f, setup), manifest = handoff(f, setup, diffs);
          fault(kind, () => preserved(f, setup, cleanupWorktrees(setup, preserveIntent(diffs, manifest))));
        });
      }
      await t.test(`${provider}: later cleanup planner rejects corrupt and stale committed evidence`, () => {
        const f = fixture(provider), setup = f.allocate(); change(setup);
        const diffs = capture(f, setup), manifest = handoff(f, setup, diffs), task = setup.worktrees[0];
        runGit(task.path, ["commit", "-qm", "fixture child change"]);
        const patch = readFileSync(diffs[0].patchPath);
        assert.equal(plan(f, setup, manifest).decision, "remove", "valid patch must reach planner preservation proof");
        for (const kind of ["apply", "machine-diff"]) {
          fault(kind, () => assert.notEqual(plan(f, setup, manifest).decision, "remove"));
        }
        writeFileSync(diffs[0].patchPath, "broken patch\n");
        assert.notEqual(plan(f, setup, manifest).decision, "remove");
        writeFileSync(diffs[0].patchPath, patch);
        writeFileSync(join(task.path, "later.bin"), Buffer.from([0, 33, 34]));
        runGit(task.path, ["add", "-A"]); runGit(task.path, ["commit", "-qm", "later change"]);
        assert.notEqual(plan(f, setup, manifest).decision, "remove");
        assert.equal(existsSync(task.path), true); assert.ok(f.git("show-ref", "--verify", `refs/heads/${task.branch}`));
      });
      await t.test(`${provider}: setup rollback is not implicit discard authority`, () => {
        const f = fixture(provider), setup = f.allocate(); change(setup);
        preserved(f, setup, cleanupWorktrees(setup, { kind: "setup-rollback" }));
      });
      await t.test(`${provider}: clean provisional setup rollback remains permitted`, () => {
        const f = fixture(provider), setup = f.allocate();
        removed(f, setup, cleanupWorktrees(setup, { kind: "setup-rollback" }));
      });
      await t.test(`${provider}: unknown setup state cannot authorize rollback`, () => {
        const f = fixture(provider), setup = f.allocate();
        fault("status", () => preserved(f, setup, cleanupWorktrees(setup, { kind: "setup-rollback" })));
      });
      await t.test(`${provider}: ignored setup-created work is retained`, () => {
        const f = fixture(provider);
        const exclude = join(f.dir, "excludes"); writeFileSync(exclude, "*.private\n");
        f.git("config", "core.excludesFile", exclude);
        const setup = f.allocate();
        writeFileSync(join(setup.worktrees[0].path, "fixture.private"), "fictional setup work\n");
        preserved(f, setup, cleanupWorktrees(setup, { kind: "setup-rollback" }));
        assert.equal(readFileSync(join(setup.worktrees[0].path, "fixture.private"), "utf8"), "fictional setup work\n");
      });
      await t.test(`${provider}: setup failure preserves first and failed-hook work`, () => {
        const f = fixture(provider); let planned;
        const hook = join(f.dir, "setup.cjs");
        writeFileSync(hook, `#!${process.execPath}\nconst fs=require('node:fs'); const x=JSON.parse(fs.readFileSync(0,'utf8'));
fs.writeFileSync(require('node:path').join(x.worktreePath,'setup-work.bin'),Buffer.from([0,40,x.index]));
if(x.index===1) process.exit(7); console.log('{}');\n`, { mode: 0o755 });
        assert.throws(() => f.allocate({ beforeCreate: value => { planned = value; }, setupHook: { hookPath: hook } }, 2), /setup hook failed/);
        assert.equal(planned.worktrees.length, 2);
        for (const task of planned.worktrees) {
          assert.equal(existsSync(task.path), true, "failed setup discarded fixture work");
          assert.ok(f.git("show-ref", "--verify", `refs/heads/${task.branch}`));
          assert.deepEqual(readFileSync(join(task.path, "setup-work.bin")), Buffer.from([0, 40, task.index]));
        }
      });
      if (provider === "worktrunk") {
        for (const invalid of ["base-mismatch", "foreign-path"]) {
          await t.test(`worktrunk: invalid ${invalid} provisioning preserves work and foreign paths`, () => {
            const f = fixture(provider);
            const realWt = executable("wt");
            const wrappedBin = join(f.dir, "provider-bin"); mkdirSync(wrappedBin);
            const allocationLog = join(f.dir, "allocation.json");
            let foreign;
            if (invalid === "foreign-path") {
              foreign = join(f.dir, "foreign");
              f.git("worktree", "add", "-b", "foreign-fixture", foreign, f.base);
              writeFileSync(join(foreign, "foreign.bin"), Buffer.from([0, 77, 78]));
            }
            const script = join(wrappedBin, "wt.cjs");
            writeFileSync(script, `const {spawnSync}=require('node:child_process'); const fs=require('node:fs'); const path=require('node:path');
const args=process.argv.slice(2); const r=spawnSync(${JSON.stringify(realWt)},args,{encoding:'utf8'});
if(r.status!==0 || !args.includes('--create')) {process.stdout.write(r.stdout||''); process.stderr.write(r.stderr||''); process.exit(r.status??1);}
const allocation=JSON.parse(r.stdout); fs.writeFileSync(${JSON.stringify(allocationLog)},JSON.stringify(allocation));
fs.writeFileSync(path.join(allocation.path,'provider-work.bin'),Buffer.from([0,70,71]));
const foreign=${JSON.stringify(foreign ?? null)};
if(foreign) {
 fs.writeFileSync(${JSON.stringify(faultFile)},JSON.stringify({kind:'deny-owned-remove',path:allocation.path,marker:${JSON.stringify(join(f.dir, "remove-attempt"))}}));
 allocation.path=foreign;
} else allocation.base_branch='inconsistent-base';
console.log(JSON.stringify(allocation));
`);
            writeFileSync(join(wrappedBin, "wt"), `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(script)} "$@"\n`, { mode: 0o755 });
            const previousPath = process.env.PATH;
            process.env.PATH = `${wrappedBin}:${previousPath}`;
            try {
              assert.throws(() => f.allocate(), /Worktrunk provisioning returned/);
              const owned = JSON.parse(readFileSync(allocationLog, "utf8"));
              // Test the foreign boundary first; old code can delete it as fallback
              // after a failed removal of the genuinely allocated worktree.
              if (foreign) {
                assert.equal(existsSync(foreign), true, "provider metadata caused foreign checkout deletion");
                assert.deepEqual(readFileSync(join(foreign, "foreign.bin")), Buffer.from([0, 77, 78]));
                assert.ok(f.git("show-ref", "--verify", "refs/heads/foreign-fixture"));
              }
              assert.equal(existsSync(owned.path), true, "invalid provisioning discarded new work");
              assert.deepEqual(readFileSync(join(owned.path, "provider-work.bin")), Buffer.from([0, 70, 71]));
              assert.ok(f.git("show-ref", "--verify", `refs/heads/${owned.branch}`));
              assert.equal(f.git("rev-parse", "HEAD"), f.base);
              assert.equal(f.git("branch", "--show-current"), "main");
            } finally {
              process.env.PATH = previousPath;
              rmSync(faultFile, { force: true });
            }
          });
        }
      }
      await t.test(`${provider}: discard requires the existing explicit authority`, () => {
        const f = fixture(provider), setup = f.allocate(); change(setup);
        preserved(f, setup, cleanupWorktrees(setup, { kind: "discard", authorization: { kind: "confirmed", policy: { discardWorktree: "forbid" } } }));
        preserved(f, setup, cleanupWorktrees(setup, { kind: "discard", authorization: { kind: "policy", policy: { discardWorktree: "confirm" } } }));
        removed(f, setup, cleanupWorktrees(setup, { kind: "discard", authorization: { kind: "confirmed", policy: { discardWorktree: "confirm" } } }));
        assert.equal(f.git("rev-parse", "HEAD"), f.base);
      });
    }
    assert.deepEqual(sourceIdentity(), identity, "candidate source changed during acceptance; rerun exact reviewed state");
  });
} finally {
  // Test teardown, NOT package cleanup: remove only this process's disposable root.
  // Preservation assertions above must run first, and failures remain test failures.
  process.chdir(root);
  rmSync(temporary, { recursive: true, force: true });
}

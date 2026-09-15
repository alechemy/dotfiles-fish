// Opt-in, service-free acceptance against the installer-pinned plugin source.
// HUNK_PLUGIN_ROOT=/reviewed/plugin node --test scripts/tests/test_hunk_runtime.mjs
// Requires that source checkout's existing TypeScript and runtime dependencies.
// Nothing is installed. Compile only into a disposable directory, never the source cache.
// Fake only Herdr/Hunk I/O. Run the real resolver, dispatcher, event handler, index,
// Pi submission adapter, and key installer. No live config, comments, or sessions.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const plugin = process.env.HUNK_PLUGIN_ROOT;
const REVISION = "b063856e85436668a165e511ed16a503ea729752";

test("pinned patched Hunk plugin acceptance", { skip: !plugin && "Set HUNK_PLUGIN_ROOT to reviewed source" }, async t => {
  const root = mkdtempSync(join(tmpdir(), "hunk-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(GIT_|HERDR_|PI_|NODE_)/.test(key)));
  Object.assign(env, {
    HOME: join(root, "home"), XDG_CONFIG_HOME: join(root, "home", ".config"),
    GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0",
    GIT_AUTHOR_NAME: "Fixture", GIT_AUTHOR_EMAIL: "fixture@example.invalid",
    GIT_COMMITTER_NAME: "Fixture", GIT_COMMITTER_EMAIL: "fixture@example.invalid",
  });
  mkdirSync(env.HOME);
  function command(cmd, args, cwd = root, expected = 0) {
    const result = spawnSync(cmd, args, { cwd, env, encoding: "utf8", timeout: 15000 });
    assert.equal(result.status, expected, `${cmd} ${args.join(" ")}: ${result.stderr}`);
    return result.stdout;
  }
  const sourceRoot = resolve(plugin);
  assert.equal(command("git", ["rev-parse", "HEAD"], sourceRoot).trim(), REVISION);
  const patch = readFileSync(join(ROOT, "scripts/patches/herdr-hunk-diff.patch"), "utf8");
  const normalize = text => text.split("\n").map(line => line.trimEnd()).join("\n").trim();
  assert.equal(normalize(command("git", ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", "src"], sourceRoot)),
    normalize(patch.split("diff --git a/tests/")[0]), "Source differs from the tracked patch");
  assert.equal(command("git", ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", "package*.json", "tsconfig.json"], sourceRoot), "");
  assert.equal(command("git", ["ls-files", "--others", "--", "src", "package*.json", "tsconfig.json"], sourceRoot), "");
  const pkg = JSON.parse(readFileSync(join(sourceRoot, "package.json"), "utf8"));
  assert.equal(pkg.version, "0.3.0");
  const compiled = join(root, "compiled");
  mkdirSync(compiled);
  writeFileSync(join(compiled, "package.json"), '{"type":"module"}\n');
  symlinkSync(join(sourceRoot, "node_modules"), join(compiled, "node_modules"), "dir");
  const compiler = join(sourceRoot, "node_modules/typescript/bin/tsc");
  command(process.execPath, [compiler, "-p", join(sourceRoot, "tsconfig.json"),
    "--outDir", compiled, "--noEmitOnError"], sourceRoot);
  const load = name => import(pathToFileURL(join(compiled, `${name}.js`)).href);
  const { loadConfig } = await load("config");
  const { resolveTarget } = await load("target");
  const { realTargetDeps } = await load("git");
  const { buildLaunchArgs } = await load("hunk");
  const { dispatch } = await load("runtime");
  const { handleEvent } = await load("events");
  const { ReviewIndex } = await load("index-store");
  const { HerdrAdapter } = await load("herdr");
  const { installKeys } = await load("keys-install");
  const { takenKeys } = await load("keys");
  t.diagnostic(JSON.stringify({ revision: REVISION, version: pkg.version,
    patchSha256: createHash("sha256").update(patch).digest("hex"),
    compiler: command(process.execPath, [compiler, "--version"], sourceRoot).trim(),
    execution: "temporary compile of identified source; fake Herdr/Hunk I/O" }));
  const cfg = loadConfig(join(ROOT, "stow/herdr/_seed/.config/herdr/plugins/config/jhochenbaum.hunkdiff"));
  assert.equal(cfg.review.auto_open, false);
  assert.deepEqual(cfg.review.on_states, []);
  cfg.review.base = "release";
  cfg.review.watch = false;
  const repo = join(root, "repo");
  mkdirSync(repo);
  const git = (...args) => command("git", args, repo).trim();
  git("init", "-q", "-b", "release", "--template=");
  const write = (name, text) => writeFileSync(join(repo, name), text);
  write("staged.txt", "base\n");
  write("unstaged.txt", "base\n");
  git("add", "."); git("commit", "-qm", "baseline");
  const mergeBase = git("rev-parse", "HEAD");
  git("checkout", "-qb", "task");
  for (const name of ["first.txt", "second.txt"]) {
    write(name, name + "\n"); git("add", name); git("commit", "-qm", name);
  }
  const head = git("rev-parse", "HEAD");
  git("checkout", "-q", "release");
  write("integration-only.txt", "integration\n"); git("add", "."); git("commit", "-qm", "diverged");
  const base = git("rev-parse", "HEAD");
  git("checkout", "-q", "task");
  write("staged.txt", "staged\n"); git("add", "staged.txt");
  write("unstaged.txt", "unstaged\n"); write("untracked.txt", "untracked\n");
  const deps = realTargetDeps(cwd => (cmd, args) => {
    assert.equal(cmd, "git", "Resolver must not launch an external service");
    const r = spawnSync(cmd, args, { cwd, env, encoding: "utf8", timeout: 10000 });
    return { status: r.status ?? 1, stdout: r.stdout ?? "" };
  });
  const resolveFor = (config, mode, ref) => resolveTarget({ worktree: repo }, config, deps, mode, ref);
  const names = (...args) => command("git", ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", ...args, "--"], repo)
    .split("\0").filter(Boolean).sort();

  await t.test("real resolver and dispatcher select four distinct scopes over two commits and dirty files", async () => {
    const rt = runtime("scopes");
    const expected = [
      ["review", "working", ["diff"], ["staged.txt", "unstaged.txt"]],
      ["review:staged", "staged", ["diff", "--staged"], ["staged.txt"]],
      ["review:commit", "commit", ["show"], ["second.txt"]],
      ["review:branch", "branch", ["diff", "release...HEAD"], ["first.txt", "second.txt"]],
    ];
    for (const [action, mode, argv, paths] of expected) {
      assert.equal(await dispatch(action, rt), 0);
      const target = rt.targetFor(mode);
      assert.deepEqual(buildLaunchArgs(target, rt.cfg), argv);
      assert.equal(rt.index.get(repo).requestedMode, mode);
      const actual = mode === "working" ? names("HEAD") : mode === "staged" ? names("--cached")
        : mode === "commit" ? names("HEAD^", "HEAD") : names(target.ref);
      assert.deepEqual(actual, paths);
    }
    assert.equal(rt.index.get(repo).displayedTarget, "release...HEAD");
    assert.equal(git("ls-files", "--others", "--exclude-standard"), "untracked.txt");
    assert.equal(cfg.review.exclude_untracked, false);
    assert.equal(git("merge-base", base, head), mergeBase);
    assert.notEqual(base, mergeBase);
    assert.deepEqual(names(base, head), ["first.txt", "integration-only.txt", "second.txt"]);
    assert.equal(rt.reloads.at(-1).ref, "release...HEAD");
  });

  await t.test("auto distinguishes dirty, untracked-only, excluded-untracked and clean branch review", async () => {
    assert.equal(resolveFor(cfg).mode, "working");
    git("restore", "--staged", "--worktree", "--", "staged.txt", "unstaged.txt");
    assert.equal(resolveFor(cfg).mode, "working", "untracked alone remains working scope");
    const trackedOnly = structuredClone(cfg); trackedOnly.review.exclude_untracked = true;
    assert.equal(resolveFor(trackedOnly).mode, "branch");
    assert.deepEqual(buildLaunchArgs(resolveFor(trackedOnly), trackedOnly),
      ["diff", "release...HEAD", "--exclude-untracked"]);
    rmSync(join(repo, "untracked.txt"));
    assert.equal(resolveFor(cfg).mode, "branch");
    assert.equal(resolveFor(cfg).ref, "release...HEAD");
    const rt = runtime("clean-auto");
    assert.equal(await dispatch("review", rt), 0);
    assert.equal(rt.index.get(repo).requestedMode, "branch");
    assert.equal(rt.index.get(repo).displayedTarget, "release...HEAD");
    write("staged.txt", "staged\n"); git("add", "staged.txt");
    write("unstaged.txt", "unstaged\n"); write("untracked.txt", "untracked\n");
  });

  await t.test("missing base falls back with warning; invalid configured base can use detected upstream", async () => {
    const absent = structuredClone(cfg);
    absent.review.base = "missing-base";
    let target = resolveFor(absent, "branch");
    assert.equal(target.mode, "working");
    assert.match(target.warning, /Configured base .* does not exist/);
    assert.match(target.warning, /No base branch resolved/);
    assert.deepEqual(buildLaunchArgs(target, absent), ["diff"]);
    const rt = runtime("fallback", absent);
    assert.equal(await dispatch("review:branch", rt), 0);
    assert.equal(rt.index.get(repo).displayedTarget, "working tree");
    assert.ok(rt.notices.some(text => text.includes("No base branch resolved")));
    git("branch", "--set-upstream-to=release", "task");
    target = resolveFor(absent, "branch");
    assert.equal(target.mode, "branch");
    assert.equal(target.ref, "release...HEAD");
    assert.match(target.warning, /comparing against release instead/);
    git("branch", "--unset-upstream", "task");
    absent.review.base = "";
    assert.equal(resolveFor(absent, "branch").mode, "working");
    assert.deepEqual(resolveFor(absent, "branch", "release...HEAD"), {
      worktree: repo, mode: "branch", ref: "release...HEAD",
    });
  });

  await t.test("automatic base order and current-branch remote avoidance use real Git refs", () => {
    const auto = structuredClone(cfg); auto.review.base = "";
    git("branch", "main", mergeBase); git("branch", "master", mergeBase); git("branch", "trunk", mergeBase);
    assert.equal(resolveFor(auto, "branch").ref, "main...HEAD");
    git("update-ref", "refs/remotes/origin/release", base);
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release");
    assert.equal(resolveFor(auto, "branch").ref, "origin/release...HEAD");
    git("config", "branch.task.remote", ".");
    git("config", "branch.task.merge", "refs/heads/task");
    assert.equal(resolveFor(auto, "branch").ref, "origin/release...HEAD", "skip a self-comparing upstream");
    git("branch", "--unset-upstream", "task");
    git("branch", "--set-upstream-to=release", "task");
    assert.equal(resolveFor(auto, "branch").ref, "release...HEAD");
    git("branch", "--unset-upstream", "task");
    git("update-ref", "refs/remotes/origin/task", head);
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/task");
    assert.equal(resolveFor(auto, "branch").ref, "main...HEAD");
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD");
    git("branch", "-D", "main"); assert.equal(resolveFor(auto, "branch").ref, "master...HEAD");
    git("branch", "-D", "master"); assert.equal(resolveFor(auto, "branch").ref, "trunk...HEAD");
    git("branch", "-D", "trunk");
  });

  await t.test("unrelated history is not validated by resolver; Git rejects its three-dot range", () => {
    const unrelated = git("commit-tree", "HEAD^{tree}", "-m", "unrelated root");
    const config = structuredClone(cfg); config.review.base = unrelated;
    const target = resolveFor(config, "branch");
    assert.equal(target.mode, "branch");
    assert.equal(target.warning, undefined);
    command("git", ["merge-base", unrelated, head], repo, 1);
    command("git", ["diff", target.ref, "--"], repo, 128);
  });

  function runtime(name, config = structuredClone(cfg)) {
    const stateDir = join(root, name);
    const index = new ReviewIndex(stateDir);
    const notices = [], reloads = [], commands = [], removed = [];
    const agents = ["w1:p1", "w1:p2"].map(pane_id => ({ pane_id, agent: "pi", agent_status: "idle" }));
    const rt = {
      cfg: config, ctx: { worktree: repo, agentName: "pi", paneId: "w1:p1" },
      stateDir, pluginRoot: sourceRoot, index, notices, reloads, commands, removed, agents,
      comments: [], failure: undefined, removalFails: false,
      targetFor(mode, ref) { return resolveTarget(this.ctx, this.cfg, deps, mode, ref); },
      get target() { return this.targetFor(); },
      commitExists: deps.commitExists,
    };
    const adapter = new HerdrAdapter("never-executed-herdr", args => {
      commands.push(args);
      if (args[0] === "agent" && args[1] === "list") return { status: 0, stdout: JSON.stringify({ result: { agents } }) };
      if (args[0] === "agent" && args[1] === rt.failure) return { status: 1, stdout: "" };
      return { status: 0, stdout: "{}" };
    });
    rt.herdr = {
      promptAgent: adapter.promptAgent.bind(adapter), notify: message => notices.push(message),
      openPane: () => "w1:p7", closePane: () => true,
      reportMetadata: (pane, fields) => { rt.metadata = { pane, ...fields }; },
    };
    rt.hunk = {
      listComments: async (worktree, type) => {
        assert.equal(worktree, repo); assert.equal(type, "user");
        return rt.comments.filter(comment => comment.source === "user");
      },
      removeComment: async (worktree, id) => {
        assert.equal(worktree, repo);
        removed.push(id);
        if (rt.removalFails) throw new Error("fixture removal failed");
        rt.comments = rt.comments.filter(comment => comment.noteId !== id);
      },
      reload: async (worktree, target) => { assert.equal(worktree, repo); reloads.push(target); },
    };
    return rt;
  }
  const comment = id => ({ noteId: id, source: "user", filePath: "first.txt", newRange: [1, 1], body: "Fix fixture" });
  const prompts = rt => rt.commands.filter(args => args[0] === "agent" && args[1] === "prompt");
  const fromShell = rt => { rt.ctx = { worktree: repo, paneId: "w1:p9" }; };

  await t.test("two Pi panes retain recipient on unrelated status and reassign on explicit reuse", async () => {
    const rt = runtime("recipient");
    assert.equal(await dispatch("review:branch", rt), 0);
    assert.equal(rt.index.get(repo).agentPaneId, "w1:p1");
    const events = {
      cfg: rt.cfg, index: rt.index, herdr: rt.herdr,
      worktreeForPane: () => { throw new Error("disabled status hook must not resolve a pane"); },
      resolveTarget: () => rt.target, reloadReview: () => { throw new Error("unexpected status reload"); },
      reportReviewMetadata: async () => {},
    };
    for (const agent_status of ["idle", "working", "blocked", "unknown"]) {
      assert.equal(await handleEvent({ type: "pane_agent_status_changed",
        data: { pane_id: "w1:p2", agent: "pi", agent_status } }, events), 0);
      assert.equal(rt.index.get(repo).agentPaneId, "w1:p1");
    }
    rt.ctx.paneId = "w1:p2";
    assert.equal(await dispatch("review:branch", rt), 0);
    assert.equal(rt.index.get(repo).agentPaneId, "w1:p2");
    assert.equal(rt.index.get(repo).paneId, "w1:p7");
    assert.equal(rt.reloads.length, 1);
    assert.match(rt.metadata.title, /release\.\.\.HEAD/);
    fromShell(rt); rt.comments = [comment("c1")];
    assert.equal(await dispatch("send-review", rt), 0);
    assert.equal(prompts(rt)[0][2], "w1:p2");
    assert.deepEqual(rt.commands.filter(args => args[1] === "send-keys")[0], ["agent", "send-keys", "w1:p2", "ctrl+s"]);
    assert.deepEqual(rt.index.sentIds(repo), ["c1"]);
    assert.deepEqual(rt.comments, []);
    assert.equal(await dispatch("send-review", rt), 0);
    assert.equal(prompts(rt).length, 1);
  });

  await t.test("blocked/missing Pi and failed or repeated sends preserve comments and delivery state", async () => {
    const rt = runtime("failures");
    await dispatch("review", rt); fromShell(rt);
    rt.comments = [comment("c1")];
    for (const status of ["blocked", "working", "unknown"]) {
      rt.agents[0].agent_status = status;
      assert.equal(await dispatch("send-review", rt), 1);
      assert.deepEqual(rt.index.sentIds(repo), []);
      assert.equal(rt.comments.length, 1);
    }
    const recipient = rt.agents.shift();
    assert.equal(await dispatch("send-review", rt), 1);
    assert.equal(prompts(rt).length, 0);
    rt.agents.unshift(recipient); recipient.agent_status = "idle";
    for (const failure of ["prompt", "send-keys"]) {
      rt.failure = failure;
      for (let attempt = 0; attempt < 2; attempt++) {
        assert.equal(await dispatch("send-review", rt), 1);
        assert.deepEqual(rt.index.sentIds(repo), []);
        assert.equal(rt.comments.length, 1);
        assert.deepEqual(rt.removed, []);
      }
    }
    rt.failure = undefined;
    assert.equal(await dispatch("send-review", rt), 0);
    assert.deepEqual(rt.index.sentIds(repo), ["c1"]);
    assert.deepEqual(rt.comments, []);
  });

  await t.test("cleanup failure and explicit reopening preserve sent IDs and prevent duplicate delivery", async () => {
    const rt = runtime("dedup");
    await dispatch("review", rt); fromShell(rt);
    rt.comments = [comment("c1"), { ...comment("agent-note"), source: "agent" }];
    rt.removalFails = true;
    assert.equal(await dispatch("send-review", rt), 0);
    assert.deepEqual(rt.index.sentIds(repo), ["c1"]);
    assert.equal(rt.comments.length, 2);
    assert.equal(await dispatch("send-review", rt), 0);
    rt.ctx = { worktree: repo, agentName: "pi", paneId: "w1:p2" };
    assert.equal(await dispatch("review:branch", rt), 0);
    assert.deepEqual(rt.index.sentIds(repo), ["c1"]);
    fromShell(rt);
    assert.equal(await dispatch("send-review", rt), 0);
    assert.equal(prompts(rt).length, 1);
    assert.ok(rt.notices.some(text => text.includes("could not remove")));
  });

  await t.test("explicit send from a different Pi pane overrides stored recipient for that send", async () => {
    const rt = runtime("send-context");
    await dispatch("review", rt);
    rt.comments = [comment("c1")]; rt.ctx.paneId = "w1:p2";
    assert.equal(await dispatch("send-review", rt), 0);
    assert.equal(prompts(rt)[0][2], "w1:p2");
    assert.equal(rt.index.get(repo).agentPaneId, "w1:p1");
  });

  await t.test("real key installer preserves array conflicts and configurator reports skipped branch binding", () => {
    const path = join(root, "keys.toml");
    const user = '[keys]\nzoom = ["prefix+shift+b", "cmd+enter"]\n';
    writeFileSync(path, user);
    const bindings = [{ key: "prefix+shift+b", action: "jhochenbaum.hunkdiff.review:branch", description: "branch" }];
    const result = installKeys(path, bindings);
    assert.equal(result.ok, false);
    assert.deepEqual(result.skipped, bindings);
    assert.equal(readFileSync(path, "utf8"), user);
    assert.ok(takenKeys(user).has("prefix+shift+b"));
    // Exercise the tracked configurator with the freshly compiled plugin helper.
    const fixturePlugin = join(root, "fixture-plugin"); mkdirSync(fixturePlugin);
    symlinkSync(compiled, join(fixturePlugin, "dist"), "dir");
    const preferences = join(root, "preferences");
    const result2 = spawnSync(process.execPath, [join(ROOT, "scripts/configure-herdr-hunk.mjs"), fixturePlugin, preferences],
      { cwd: root, env: { ...env, HERDR_CONFIG_PATH: path }, encoding: "utf8", timeout: 10000 });
    assert.equal(result2.status, 1);
    assert.match(result2.stderr, /prefix\+shift\+b/);
    assert.ok(readFileSync(path, "utf8").startsWith(user));
    assert.ok(!readFileSync(path, "utf8").includes('action = "jhochenbaum.hunkdiff.review:branch"'));
    assert.ok(readFileSync(path, "utf8").includes("jhochenbaum.hunkdiff.review:staged"));
  });
});

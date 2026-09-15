// Offline coverage of exact code in the tracked patch, not a full plugin runtime.
// No installed plugin, live Herdr server, Hunk daemon, or model is loaded.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const patch = readFileSync(new URL("../patches/herdr-hunk-diff.patch", import.meta.url), "utf8");

function patchedHunk(file) {
  const section = patch.split(`diff --git a/${file} b/${file}\n`)[1]?.split("diff --git ")[0];
  assert.ok(section, `Missing tracked patch for ${file}`);
  return section.split("\n")
    .filter(line => (line.startsWith("+") && !line.startsWith("+++")) || line.startsWith(" "))
    .map(line => line.slice(1)).join("\n");
}

function between(source, start, end) {
  const offset = source.indexOf(start);
  assert.notEqual(offset, -1, `Missing start: ${start}`);
  const limit = source.indexOf(end, offset + start.length);
  assert.notEqual(limit, -1, `Missing end: ${end}`);
  return source.slice(offset + start.length, limit);
}

// Compile only the JS-compatible method body. Its behavior stays owned by the patch.
const promptAgent = new Function("target", "text", between(
  patchedHunk("src/herdr.ts"),
  "promptAgent(target: string, text: string): boolean {", "\n  }",
));

function adapter(agents, failure) {
  const calls = [];
  return {
    calls,
    agentList: () => agents,
    run(args) {
      calls.push(args);
      return { status: args[1] === failure ? 1 : 0 };
    },
    promptAgent,
  };
}

const pi = (pane_id, agent_status = "idle") => ({ agent: "pi", pane_id, agent_status });

test("Pi submission targets the selected pane and sends Ctrl+S only after paste", () => {
  for (const state of ["idle", "done"]) {
    const herdr = adapter([pi("w1:p1", "working"), pi("w1:p2", state)]);
    assert.equal(herdr.promptAgent("w1:p2", "fix fixture"), true);
    assert.deepEqual(herdr.calls, [
      ["agent", "prompt", "w1:p2", "fix fixture"],
      ["agent", "send-keys", "w1:p2", "ctrl+s"],
    ]);
  }
});

test("blocked, busy, missing and unknown Pi recipients do not receive a paste", () => {
  for (const state of ["blocked", "working", "unknown", "", undefined]) {
    const herdr = adapter([{ ...pi("w1:p1"), agent_status: state }, pi("w1:p2")]);
    assert.equal(herdr.promptAgent("w1:p1", "fix fixture"), false);
    assert.deepEqual(herdr.calls, []);
  }
  const herdr = adapter([pi("w1:p2"), { pane_id: "w1:p3" }]);
  assert.equal(herdr.promptAgent("w1:p1", "fix fixture"), false);
  assert.equal(herdr.promptAgent("w1:p3", "fix fixture"), false);
  assert.deepEqual(herdr.calls, []);
});

test("failed paste or submission returns false on every retry", () => {
  for (const failure of ["prompt", "send-keys"]) {
    const herdr = adapter([pi("w1:p1"), pi("w1:p2")], failure);
    const attempt = [["agent", "prompt", "w1:p2", "fix fixture"]];
    if (failure === "send-keys") attempt.push(["agent", "send-keys", "w1:p2", "ctrl+s"]);
    assert.equal(herdr.promptAgent("w1:p2", "fix fixture"), false);
    assert.equal(herdr.promptAgent("w1:p2", "fix fixture"), false);
    assert.deepEqual(herdr.calls, [...attempt, ...attempt]);
  }
});

test("non-Pi agents keep the existing Herdr submission path", () => {
  const herdr = adapter([{ agent: "claude", pane_id: "w1:p1", agent_status: "working" }]);
  assert.equal(herdr.promptAgent("w1:p1", "fix fixture"), true);
  assert.deepEqual(herdr.calls, [["agent", "prompt", "w1:p1", "fix fixture"]]);
});

const addKey = new Function("value", "keys", between(
  patchedHunk("src/keys.ts"), "const add = (value: unknown) => {", "\n  };",
));

test("conflict collection preserves scalar and array-valued user keys", () => {
  const keys = new Set();
  for (const value of ["prefix+f", ["prefix+shift+b", "cmd+enter", "", null, 17], undefined, {}]) {
    addKey(value, keys);
  }
  assert.deepEqual(keys, new Set(["prefix+f", "prefix+shift+b", "cmd+enter"]));
});

// The patch contains this upsert, not the surrounding dispatcher or index implementation.
// An empty sent update does not clear history: the real ReviewIndex unions sent IDs.
// test_hunk_runtime.mjs covers that lifecycle when a plugin source root is supplied.
const reuse = new Function("rt", "target", "suppliedRef", "agentPaneFromContext", "displayedReviewRecord",
  `rt.index.upsert({${between(patchedHunk("src/runtime.ts"), "rt.index.upsert({", "\n      });")}\n});`);

test("explicit reuse supplies each invoking Pi pane and an empty sent update", () => {
  let record = { worktree: "/fixture", paneId: "w1:p7", agentPaneId: "w1:p2", sent: ["old-comment"] };
  const rt = {
    ctx: { agentName: "pi", paneId: "w1:p1" },
    index: { upsert: update => { record = { ...record, ...update }; } },
  };
  for (const paneId of ["w1:p1", "w1:p2", "w1:p2"]) {
    rt.ctx.paneId = paneId;
    reuse(rt, { worktree: "/fixture" }, "release", rt => rt.ctx.paneId,
      () => ({ comparison: "release...HEAD" }));
    assert.equal(record.agentPaneId, paneId);
    assert.equal(record.agentName, "pi");
    assert.equal(record.paneId, "w1:p7");
    assert.equal(record.comparison, "release...HEAD");
    assert.deepEqual(record.sent, []);
  }
});

test("tracked preferences disable status-driven automatic opening", () => {
  const seed = readFileSync(new URL(
    "../../stow/herdr/_seed/.config/herdr/plugins/config/jhochenbaum.hunkdiff/config.toml", import.meta.url,
  ), "utf8");
  assert.match(seed, /^auto_open = false$/m);
  assert.match(seed, /^on_states = \[\]$/m);
});

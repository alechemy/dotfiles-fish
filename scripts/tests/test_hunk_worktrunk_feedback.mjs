import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import extension from "../cmux/worktrunk-feedback.ts";

async function fixture(receiptIds = '[item["id"] for item in payload["comments"]]', sessionCount = 1) {
  const root = await mkdtemp(join(tmpdir(), "hunk-feedback-"));
  const log = join(root, "calls.jsonl");
  const wtPi = join(root, "wt-pi");
  const hunk = join(root, "hunk");
  await writeFile(wtPi, `#!/usr/bin/python3
import json, sys
body = sys.stdin.read()
with open(${JSON.stringify(log)}, "a") as stream:
    stream.write(json.dumps({"command": "wt-pi", "args": sys.argv[1:], "body": body}) + "\\n")
if sys.argv[1] == "feedback":
    payload = json.loads(body)
    print(json.dumps({"status": "delivered", "delivered_ids": ${receiptIds}}))
`);
  await writeFile(hunk, `#!/bin/sh
printf '{"command":"hunk","args":"%s"}\\n' "$*" >> '${log}'
if [ "$1 $2" = "session list" ]; then
  printf '%s\\n' '${JSON.stringify({ sessions: Array.from({ length: sessionCount }, (_, index) => ({ pid: process.pid, cwd: root, sessionId: `exact-session-${index}` })) })}'
else
  printf '{"ok":true}\\n'
fi
`);
  await chmod(wtPi, 0o755);
  await chmod(hunk, 0o755);
  const handlers = new Map();
  const commands = new Map();
  extension({
    on: (name, handler) => handlers.set(name, handler),
    registerCommand: (command, handler) => commands.set(command.id, handler),
  });
  return { root, log, handlers, commands, cleanup: () => rm(root, { recursive: true, force: true }) };
}

function snapshot(revision = 1) {
  return {
    generation: "generation-1",
    stateRevision: revision,
    files: [{ fileKey: "file-1", path: "src/example.ts" }],
    notes: [
      { id: "human-1", source: "user", fileKey: "file-1", anchor: { preferred: { side: "new", line: 4 } },
        summary: "Handle this case", rationale: "It can fail", resolution: "active" },
      { id: "agent-1", source: "agent", fileKey: "file-1", anchor: { preferred: { side: "new", line: 8 } },
        summary: "Do not send", resolution: "active" },
    ],
  };
}

async function calls(path) {
  return (await readFile(path, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
}

test("sends only human comments and removes them after a verified receipt", async () => {
  const f = await fixture();
  const oldPath = process.env.PATH;
  process.env.PATH = `${f.root}:${oldPath}`;
  const notices = [];
  try {
    const current = snapshot();
    await f.handlers.get("note_changed")({ kind: "created", note: current.notes[0] },
                                         { cwd: f.root, notify: (message) => notices.push(message) });
    await f.handlers.get("note_changed")({ kind: "created", note: current.notes[1] },
                                         { cwd: f.root, notify: (message) => notices.push(message) });
    await f.commands.get("worktrunk-send-feedback")({
      cwd: f.root,
      review: { snapshot: () => current },
      notify: (message) => notices.push(message),
    });
    const recorded = await calls(f.log);
    const feedback = recorded.find((item) => item.command === "wt-pi" && item.args[0] === "feedback");
    assert.deepEqual(JSON.parse(feedback.body).comments.map((item) => item.id), ["human-1"]);
    const removals = recorded.filter((item) => item.command === "hunk" && item.args.startsWith("session comment rm"));
    assert.equal(removals.length, 1);
    assert.equal(removals[0].args, "session comment rm exact-session-0 human-1 --json");
    assert.equal(recorded.some((item) => item.body?.includes("agent-1")), false);
    assert.equal(recorded.filter((item) => item.args?.includes("--tracking-token")).length, 2);
    assert.match(notices.at(-1), /Delivered 1 human comment/);
  } finally {
    process.env.PATH = oldPath;
    await f.cleanup();
  }
});

test("rejects duplicate, missing, and extra receipt identities", async (t) => {
  for (const [name, receiptIds] of [
    ["duplicate", '["human-1", "human-1"]'],
    ["missing", '["human-1"]'],
    ["extra", '["human-1", "other"]'],
  ]) {
    await t.test(name, async () => {
      const f = await fixture(receiptIds);
      const oldPath = process.env.PATH;
      process.env.PATH = `${f.root}:${oldPath}`;
      const notices = [];
      try {
        const current = snapshot();
        current.notes.push({
          id: "human-2", source: "user", fileKey: "file-1",
          anchor: { preferred: { side: "new", line: 9 } },
          summary: "Keep this too", resolution: "active",
        });
        await f.commands.get("worktrunk-send-feedback")({
          cwd: f.root,
          review: { snapshot: () => current },
          notify: (message) => notices.push(message),
        });
        const recorded = await calls(f.log);
        assert.equal(recorded.some((item) => item.command === "hunk"), false);
        assert.match(notices.at(-1), /invalid receipt/);
      } finally {
        process.env.PATH = oldPath;
        await f.cleanup();
      }
    });
  }
});

test("retains delivered notes when its exact Hunk session is missing or ambiguous", async () => {
  for (const count of [0, 2]) {
    const f = await fixture(undefined, count);
    const oldPath = process.env.PATH;
    process.env.PATH = `${f.root}:${oldPath}`;
    const notices = [];
    try {
      await f.commands.get("worktrunk-send-feedback")({
        cwd: f.root,
        review: { snapshot: () => snapshot() },
        notify: (message) => notices.push(message),
      });
      const recorded = await calls(f.log);
      assert.equal(recorded.some((item) => typeof item.args === "string" && item.args.startsWith("session comment rm")), false);
      assert.match(notices.at(-1), /could not be identified/);
    } finally {
      process.env.PATH = oldPath;
      await f.cleanup();
    }
  }
});

test("resolved path changes produce a new delivery fingerprint", async () => {
  const f = await fixture();
  const oldPath = process.env.PATH;
  process.env.PATH = `${f.root}:${oldPath}`;
  try {
    const first = snapshot();
    const second = { ...snapshot(), files: [{ fileKey: "file-1", path: "src/renamed.ts" }] };
    for (const current of [first, second]) {
      await f.commands.get("worktrunk-send-feedback")({
        cwd: f.root,
        review: { snapshot: () => current },
        notify: () => undefined,
      });
    }
    const recorded = await calls(f.log);
    const payloads = recorded
      .filter((item) => item.command === "wt-pi" && item.args[0] === "feedback")
      .map((item) => JSON.parse(item.body).comments[0]);
    assert.equal(payloads[0].path, "src/example.ts");
    assert.equal(payloads[1].path, "src/renamed.ts");
    assert.notEqual(payloads[0].fingerprint, payloads[1].fingerprint);
  } finally {
    process.env.PATH = oldPath;
    await f.cleanup();
  }
});

test("retains comments when review state changes after delivery", async () => {
  const f = await fixture();
  const oldPath = process.env.PATH;
  process.env.PATH = `${f.root}:${oldPath}`;
  const notices = [];
  let reads = 0;
  try {
    await f.commands.get("worktrunk-send-feedback")({
      cwd: f.root,
      review: { snapshot: () => snapshot(++reads > 2 ? 2 : 1) },
      notify: (message) => notices.push(message),
    });
    const recorded = await calls(f.log);
    assert.equal(recorded.some((item) => item.command === "hunk"), false);
    assert.match(notices.at(-1), /comments were retained/);
  } finally {
    process.env.PATH = oldPath;
    await f.cleanup();
  }
});

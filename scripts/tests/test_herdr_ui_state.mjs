import assert from "node:assert/strict";
import test from "node:test";
import extension from "../../stow/herdr/.pi/agent/extensions/herdr-ui-state.ts";

function fixture(env = { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p1" }) {
  const original = { HERDR_ENV: process.env.HERDR_ENV, HERDR_PANE_ID: process.env.HERDR_PANE_ID };
  for (const key of Object.keys(original)) {
    if (env[key] === undefined) delete process.env[key];
    else process.env[key] = env[key];
  }
  const handlers = new Map();
  const reports = [];
  try {
    extension({
      on: (name, handler) => handlers.set(name, handler),
      events: { emit: (name, data) => reports.push({ name, ...data }) },
    });
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
  return {
    handlers, reports,
    emit(name, mode = "tui") {
      handlers.get(name)?.({ title: "Private prompt title" }, { mode });
    },
  };
}

test("stays inactive outside Herdr or without a pane", () => {
  assert.equal(fixture({}).handlers.size, 0);
  assert.equal(fixture({ HERDR_ENV: "1" }).handlers.size, 0);
});

test("balances the coalesced waiting span without exposing prompt titles", () => {
  const f = fixture();
  f.emit("ui_prompt_end");
  f.emit("ui_prompt_start");
  f.emit("ui_prompt_start");
  f.emit("ui_prompt_end");
  f.emit("ui_prompt_end");
  assert.deepEqual(f.reports, [
    { name: "herdr:blocked", active: true, label: "Waiting for input" },
    { name: "herdr:blocked", active: false },
  ]);
});

test("headless prompts never change the containing pane", () => {
  const f = fixture();
  for (const mode of ["print", "json", "rpc"]) {
    f.emit("ui_prompt_start", mode);
    f.emit("ui_prompt_end", mode);
  }
  assert.deepEqual(f.reports, []);
  f.emit("ui_prompt_start");
  f.emit("ui_prompt_end", "rpc");
  assert.equal(f.reports.length, 1);
});

test("shutdown clears only an outstanding span", () => {
  const f = fixture();
  f.emit("ui_prompt_start");
  f.emit("session_shutdown");
  f.emit("session_shutdown");
  f.emit("ui_prompt_end");
  assert.equal(f.reports.length, 2);
  assert.equal(f.reports[1].active, false);
});

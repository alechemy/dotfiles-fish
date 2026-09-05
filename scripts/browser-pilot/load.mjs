import assert from "node:assert/strict";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

const { loadExtensions } = await import(pathToFileURL(`${process.env.PILOT_PI_ROOT}/dist/core/extensions/loader.js`).href);
const loaded = await loadExtensions([`${process.cwd()}/check.ts`], process.cwd());
if (loaded.errors.length) {
  writeFileSync("loader-errors.json", JSON.stringify(loaded.errors));
  throw new Error("Pi loader failed. Inspect only the private synthetic loader-errors.json.");
}
assert.equal(loaded.extensions.length, 1);
const extension = loaded.extensions[0];
const ctx = { cwd: process.cwd(), hasUI: false, mode: "print", modelRegistry: undefined, model: undefined, signal: undefined };
const execute = (name, params) => extension.tools.get(name).definition.execute("fictional", params, undefined, undefined, ctx);
if (process.env.PILOT_MODE === "registration") {
  loaded.runtime.getAllTools = () => [...extension.tools.values()].map(tool => tool.definition);
  loaded.runtime.getActiveTools = () => [...extension.tools.keys()];
  loaded.runtime.setActiveTools = () => {};
  const fire = async type => { for (const handler of extension.handlers.get(type) ?? []) await handler({ type, reason: "startup" }, ctx); };
  assert.equal(extension.tools.has("mcpScript"), false);
  assert.equal(existsSync("journal.jsonl"), false);
  try {
    await fire("session_start");
    await execute("mcp", {});
    assert.equal(existsSync("journal.jsonl"), false);
    const connected = await execute("mcp", { connect: "pilot" });
    assert.equal(connected.details.error, undefined);
    for (const name of ["mcp", "mcp__pilot"]) {
      assert.equal(extension.tools.has(name), true);
      const result = await execute(name, { tool: "allowed_echo", args: {}, ...(name === "mcp" ? { server: "pilot" } : {}) });
      assert.equal(result.details.error, "approval_required");
      const blocked = await execute(name, { tool: "blocked_echo", args: {}, ...(name === "mcp" ? { server: "pilot" } : {}) });
      assert.equal(blocked.details.error, "tool_not_found");
    }
  } finally { await fire("session_shutdown"); }
  const rows = readFileSync("journal.jsonl", "utf8").trim().split("\n").map(line => JSON.parse(line));
  assert.equal(rows.filter(row => row.event === "call").length, 0);
  assert.equal(rows.filter(row => row.event === "exit").length, 1);
  writeFileSync("summary.json", JSON.stringify({ defaultRegistration: true, namespaceRefusal: true, toolCalls: 0 }));
} else {
  assert.deepEqual([...extension.tools.keys()], ["pilot_check"]);
  await execute("pilot_check", {});
}
loaded.runtime.invalidate();

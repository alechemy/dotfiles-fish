import assert from "node:assert/strict";
import { createHash } from "node:crypto";

export const browserTools = ["list_pages", "navigate_page", "take_snapshot", "click", "fill", "take_screenshot", "list_console_messages", "get_console_message", "list_network_requests", "get_network_request", "performance_start_trace", "performance_stop_trace"];
export const rawTools = [...browserTools, "close_page", "drag", "evaluate_script", "fill_form", "handle_dialog", "hover", "lighthouse_audit", "new_page", "performance_analyze_insight", "press_key", "select_page", "take_heapsnapshot", "type_text", "upload_file", "wait_for"].sort();
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
export const digest = value => createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");

export function sourceCatalogDigests(tools) {
  assert.deepEqual(tools.map(tool => tool.name).sort(), rawTools);
  const normalized = structuredClone(tools);
  for (const tool of normalized) delete tool.annotations.category;
  return {
    sourceWire: Object.fromEntries(tools.map(tool => [tool.name, digest(tool)])),
    sdkNormalized: Object.fromEntries(normalized.map(tool => [tool.name, digest(tool)])),
  };
}

export function assertBrowserCatalog(connection, expected) {
  assert.equal(connection.status, "connected");
  assert.deepEqual(connection.client.getServerCapabilities(), { logging: {}, tools: { listChanged: true } });
  assert.equal(connection.client.getServerVersion().name, "chrome_devtools");
  assert.equal(connection.client.getServerVersion().version, "1.8.0");
  assert.equal(connection.instructions, undefined);
  assert.deepEqual(connection.resources, []);
  assert.deepEqual(connection.prompts, []);
  assert.deepEqual(connection.tools.map(tool => tool.name).sort(), rawTools);
  for (const tool of connection.tools) {
    assert.equal(tool._meta, undefined);
    assert.equal(tool.inputSchema.type, "object");
    assert.equal(digest(tool), expected[tool.name], `Reviewed catalog mismatch: ${tool.name}`);
  }
}

export function assertBrowserArguments(name, args, fixture, output) {
  const fields = {
    list_pages: [], navigate_page: ["pageId", "type", "url", "timeout"], take_snapshot: ["pageId"],
    fill: ["pageId", "uid", "value"], click: ["pageId", "uid"],
    take_screenshot: ["pageId", "format", "filePath"], list_console_messages: ["pageId", "pageSize", "includePreservedMessages"],
    get_console_message: ["pageId", "msgid"], list_network_requests: ["pageId", "pageSize", "includePreservedRequests"],
    get_network_request: ["pageId", "reqid"], performance_start_trace: ["pageId", "reload", "autoStop", "filePath"],
    performance_stop_trace: ["pageId", "filePath"],
  };
  assert.ok(browserTools.includes(name));
  const required = name === "performance_start_trace" && args.autoStop === false
    ? fields[name].filter(key => key !== "filePath") : fields[name];
  assert.deepEqual(Object.keys(args).sort(), [...required].sort());
  if (name !== "list_pages") assert.ok(Number.isInteger(args.pageId) && args.pageId > 0);
  if (name === "navigate_page") {
    assert.equal(args.type, "url");
    assert.ok([fixture.url, fixture.blocked].includes(args.url));
    assert.equal(args.timeout, 5000);
  }
  if (["fill", "click"].includes(name)) assert.match(args.uid, /^\d+_\d+$/);
  if (name === "fill") assert.equal(args.value, "Fictional Ada");
  if (name === "take_screenshot") { assert.equal(args.format, "png"); assert.equal(args.filePath, `${output}/fixture.png`); }
  for (const key of ["msgid", "reqid"]) if (key in args) assert.ok(Number.isInteger(args[key]) && args[key] > 0);
  if (name.startsWith("list_") && name !== "list_pages") {
    assert.equal(args.pageSize, 20);
    assert.equal(args[name === "list_console_messages" ? "includePreservedMessages" : "includePreservedRequests"], false);
  }
  if (name === "performance_start_trace") {
    assert.equal(typeof args.autoStop, "boolean");
    assert.equal(args.reload, args.autoStop);
    assert.equal(args.filePath, args.autoStop ? `${output}/automatic.json.gz` : undefined);
  }
  if (name === "performance_stop_trace") assert.equal(args.filePath, `${output}/explicit.json.gz`);
}

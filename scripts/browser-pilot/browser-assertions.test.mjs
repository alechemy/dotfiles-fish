import assert from "node:assert/strict";
import { test } from "node:test";
import { assertBrowserArguments, assertBrowserCatalog, rawTools, sourceCatalogDigests } from "./browser-assertions.mjs";

const fixture = { url: "http://127.0.0.1:1234/", blocked: "http://127.0.0.1:1235" };
const source = rawTools.map(name => ({ name, description: "Fictional tool", inputSchema: { type: "object", properties: {} }, annotations: { category: "fictional", readOnlyHint: false }, execution: { taskSupport: "forbidden" } }));
const expected = sourceCatalogDigests(source);
const catalog = () => ({
  status: "connected", tools: source.map(tool => ({ ...structuredClone(tool), annotations: { readOnlyHint: false } })),
  resources: [], prompts: [], client: {
    getServerCapabilities: () => ({ logging: {}, tools: { listChanged: true } }),
    getServerVersion: () => ({ name: "chrome_devtools", version: "1.8.0" }),
  },
});

test("source projection removes only category and retains execution", () => {
  assert.ok(source.every(tool => tool.annotations.category === "fictional"));
  assert.notDeepEqual(expected.sourceWire, expected.sdkNormalized);
  assertBrowserCatalog(catalog(), expected.sdkNormalized);
  const changed = catalog();
  delete changed.tools[0].execution;
  assert.throws(() => assertBrowserCatalog(changed, expected.sdkNormalized));
});

test("SDK-visible catalog mutations refuse dispatch", () => {
  for (const mutate of [
    c => { c.resources.push({}); }, c => { c.prompts.push({}); },
    c => { c.tools.pop(); }, c => { c.tools.push({ name: "unexpected" }); },
    c => { c.tools[0]._meta = { ui: {} }; },
    c => { c.tools[0].annotations.destructiveHint = true; },
    c => { c.tools[0].inputSchema.properties.initScript = { type: "string" }; },
    c => { c.tools[0].execution.taskSupport = "required"; },
    c => { c.tools[0].unreviewed = true; }, c => { c.instructions = "unreviewed"; },
    c => { c.client.getServerCapabilities = () => ({ tools: {}, tasks: {} }); },
    c => { c.client.getServerVersion = () => ({ name: "chrome_devtools", version: "2.0.0" }); },
  ]) {
    const changed = catalog();
    mutate(changed);
    assert.throws(() => assertBrowserCatalog(changed, expected.sdkNormalized));
  }
});

test("fixed navigation refuses injection and other destinations", () => {
  const args = { pageId: 1, type: "url", url: fixture.url, timeout: 5000 };
  assertBrowserArguments("navigate_page", args, fixture, "/fictional/tmp");
  for (const patch of [{ initScript: "fictional" }, { url: "https://example.com" }, { timeout: 0 }, { pageId: -1 }, { type: "reload" }]) {
    assert.throws(() => assertBrowserArguments("navigate_page", { ...args, ...patch }, fixture, "/fictional/tmp"));
  }
  assert.throws(() => assertBrowserArguments("evaluate_script", {}, fixture, "/fictional/tmp"));
});

test("finite arguments require identifiers, fictional values and owned output names", () => {
  assertBrowserArguments("fill", { pageId: 1, uid: "1_2", value: "Fictional Ada" }, fixture, "/fictional/tmp");
  assertBrowserArguments("performance_start_trace", { pageId: 1, reload: false, autoStop: false }, fixture, "/fictional/tmp");
  for (const [name, args] of [
    ["fill", { pageId: 1, uid: "other", value: "Fictional Ada" }],
    ["fill", { pageId: 1, uid: "1_2", value: "other" }],
    ["get_network_request", { pageId: 1 }],
    ["take_screenshot", { pageId: 1, format: "png", filePath: "/other.png" }],
    ["list_pages", { arbitrary: true }],
    ["list_network_requests", { pageId: 1, pageSize: 1000, includePreservedRequests: false }],
    ["performance_start_trace", { pageId: 1, reload: true, autoStop: false }],
  ]) assert.throws(() => assertBrowserArguments(name, args, fixture, "/fictional/tmp"));
});

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { Type } from "typebox";
import { initializeMcp } from "__ADAPTER__/init.ts";
import { executeCall, executeConnect, executeList, executeDescribe, executeSearch } from "__ADAPTER__/proxy-modes.ts";
import { createMcpRuntimeOwner } from "__ADAPTER__/runtime-owner.ts";
import { cleanupMaterializedBinaryResources } from "__ADAPTER__/tool-registrar.ts";
import { createMcpAdapter } from "__ADAPTER__/index.ts";
import { createDirectToolExecutor } from "__ADAPTER__/direct-tools.ts";

const config = JSON.parse(readFileSync("config.json", "utf8"));
const journal = join(process.cwd(), "journal.jsonl");
const cache = join(process.env.PI_CODING_AGENT_DIR!, "mcp-cache.json");
const events = () => existsSync(journal) ? readFileSync(journal, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line)) : [];
const count = (event: string) => events().filter(row => row.event === event).length;
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const seed = () => writeFileSync(cache, JSON.stringify({ version: 1, servers: {} }));

export function assertCatalog(connection: any, expected: string[]) {
  assert.equal(connection.status, "connected");
  const capabilities = connection.client.getServerCapabilities();
  assert.ok(Object.keys(capabilities).every(key => ["tools", "logging"].includes(key)));
  assert.ok(capabilities.tools);
  assert.deepEqual(connection.resources, []);
  assert.deepEqual(connection.prompts, []);
  assert.deepEqual(connection.tools.map((tool: any) => tool.name).sort(), [...expected].sort());
  for (const tool of connection.tools) {
    assert.equal(tool._meta, undefined);
    assert.equal(tool.inputSchema.type, "object");
  }
}

export default function (pi: any) {
  if (process.env.PILOT_MODE === "registration") return createMcpAdapter({ config })(pi);
  pi.registerTool({
    name: "pilot_check", label: "Pilot check", description: "Finite fictional adapter checks.", parameters: Type.Object({}),
    async execute(_id: string, _params: any, _signal: any, _update: any, ctx: any) {
      const summary: Record<string, any> = {};
      const expected = ["allowed_echo", "blocked_echo", "unexpected_tool", "echo-name", "echo_name"];
      async function phase(approve: boolean, cold: boolean) {
        if (cold) { if (existsSync(cache)) unlinkSync(cache); } else seed();
        const before = count("start");
        const owner = createMcpRuntimeOwner();
        owner.addCleanup(() => cleanupMaterializedBinaryResources(owner.signal));
        const selected = structuredClone(config);
        selected.mcpServers.pilot.approveTools = approve;
        const state = await initializeMcp(pi, ctx, owner, { config: selected });
        try {
          assert.deepEqual(Object.keys(state.config.mcpServers).sort(), ["disabled", "pilot"]);
          assert.equal(count("start") - before, cold ? 1 : 0);
          if (cold) { summary.coldCacheStarts = 1; return; }
          assert.equal((await executeConnect(state, "disabled")).details.error, "server_disabled");
          assert.equal(count("start"), before);
          for (let cycle = 0; cycle < 2; cycle++) {
            const connected = await executeConnect(state, "pilot");
            assert.equal(connected.details.error, undefined);
            const connection = state.manager.getConnection("pilot")!;
            assertCatalog(connection, expected);
            for (const patch of [
              { resources: [{}] }, { prompts: [{}] },
              { tools: [...connection.tools, { name: "new_tool" }] },
              { tools: connection.tools.map((tool: any) => ({ ...tool, _meta: { ui: {} } })) },
              { client: { getServerCapabilities: () => ({ tools: {}, resources: {} }) } },
            ]) assert.throws(() => assertCatalog({ ...connection, ...patch }, expected));
            assert.deepEqual(state.toolMetadata.get("pilot")!.map(tool => tool.originalName), ["allowed_echo"]);
            assert.equal(executeList(state, "pilot").details.count, 1);
            assert.ok(executeDescribe(state, "blocked_echo").details.error);
            assert.equal(JSON.stringify(executeSearch(state, "blocked_echo")).includes('"originalName":"blocked_echo"'), false);
            const beforeCalls = count("call");
            for (const name of ["blocked_echo", "pilot_blocked_echo", "unexpected_tool", "echo-name", "echo_name"]) {
              assert.equal((await executeCall(state, name, {}, "pilot")).details.error, "tool_not_found");
            }
            assert.equal(count("call"), beforeCalls);
            for (const name of ["allowed_echo", "pilot_allowed_echo"]) {
              const result = await executeCall(state, name, {}, "pilot");
              assert.equal(result.details.error, approve ? "approval_required" : undefined);
            }
            if (approve) {
              const direct = createDirectToolExecutor(() => state, () => null, {
                serverName: "pilot", originalName: "allowed_echo", prefixedName: "pilot_allowed_echo", description: "Fictional echo",
              } as any);
              assert.equal((await direct("fictional", {}, undefined, undefined, ctx)).details.error, "approval_required");
            }
            assert.equal(count("call") - beforeCalls, approve ? 0 : 2);
            await sleep(50);
            if (!approve && cycle === 0) {
              const result = await executeCall(state, "allowed_echo", { large: true }, "pilot");
              assert.equal(result.details.error, undefined);
            }
            await state.manager.close("pilot");
          }
          summary[approve ? "headlessRefusedCalls" : "permittedCalls"] = approve ? 6 : 5;
        } finally { await owner.stop(); }
      }
      await phase(true, true);
      await phase(true, false);
      await phase(false, false);
      await sleep(100);
      const rows = events();
      assert.equal(count("start"), count("exit"));
      assert.equal(count("call"), 5);
      for (const row of rows.filter(row => row.event === "initialize")) {
        assert.equal(row.capabilities.includes("sampling"), false);
        assert.equal(row.capabilities.includes("elicitation"), false);
      }
      assert.ok(rows.filter(row => row.event === "probe-response").length >= 8);
      assert.ok(rows.filter(row => row.event === "probe-response").every(row => row.code === -32601));
      const spills = readdirSync(process.env.TMPDIR!).filter(name => name.startsWith("pi-mcp-output-"));
      assert.ok(spills.length > 0);
      let files = 0;
      for (const dir of spills) for (const name of readdirSync(join(process.env.TMPDIR!, dir))) {
        assert.equal(statSync(join(process.env.TMPDIR!, dir, name)).mode & 0o777, 0o600);
        files++;
      }
      summary.spillFilesSurviveShutdown = files;
      summary.fixtureStarts = count("start");
      summary.fixtureExits = count("exit");
      summary.unsolicitedRequestsRefused = count("probe-response");
      writeFileSync("summary.json", JSON.stringify(summary));
      return { content: [{ type: "text", text: "Fictional checks passed." }], details: summary };
    },
  });
}

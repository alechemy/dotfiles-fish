import assert from "node:assert/strict";
import { readFileSync, writeFileSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { Type } from "typebox";
import { initializeMcp } from "__ADAPTER__/init.ts";
import { executeCall, executeConnect } from "__ADAPTER__/proxy-modes.ts";
import { createMcpRuntimeOwner } from "__ADAPTER__/runtime-owner.ts";
import { cleanupMaterializedBinaryResources } from "__ADAPTER__/tool-registrar.ts";
import { assertBrowserCatalog, assertBrowserArguments, browserTools } from "./browser-assertions.mjs";

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
export default function (pi: any) {
  pi.registerTool({
    name: "pilot_check", label: "Pilot check", description: "Finite fictional browser checks.", parameters: Type.Object({}),
    async execute(_id: string, _params: any, _signal: any, _update: any, ctx: any) {
      const config = JSON.parse(readFileSync("config.json", "utf8"));
      const fixture = JSON.parse(readFileSync("fixture.json", "utf8"));
      const expected = JSON.parse(readFileSync("browser-catalog.json", "utf8"));
      const output = process.env.TMPDIR!;
      const owner = createMcpRuntimeOwner();
      owner.addCleanup(() => cleanupMaterializedBinaryResources(owner.signal));
      let calls = 0;
      const pids: number[] = [];
      const profiles: string[] = [];
      const summary: Record<string, any> = {};
      const state = await initializeMcp(pi, ctx, owner, { config });
      async function call(name: string, args: any) {
        const connection = state.manager.getConnection("pilot");
        assertBrowserCatalog(connection, expected.sdkNormalized);
        assertBrowserArguments(name, args, fixture, output);
        const result = await executeCall(state, name, args, "pilot");
        assert.equal(state.manager.getConnection("pilot"), connection);
        calls++;
        writeFileSync("last-result.json", JSON.stringify({ name, result }));
        assert.equal(result.details.error, undefined);
        assert.notEqual(result.isError, true);
        assert.notEqual(result.details.mcpResult?.isError, true);
        assert.ok(result.content.every((item: any) => item.type === "text"));
        const text = result.content.map((item: any) => item.text).join("\n");
        assert.ok(text.length < 100_000);
        return text;
      }
      async function exited(pid: number) {
        for (let i = 0; i < 100; i++) {
          try { process.kill(pid, 0); } catch (error: any) { if (error.code === "ESRCH") return; throw error; }
          await sleep(100);
        }
        throw new Error("Owned process survived shutdown");
      }
      function browserIdentity(serverPid: number) {
        const children = execFileSync("/usr/bin/pgrep", ["-P", String(serverPid)], { encoding: "utf8", timeout: 5000 }).trim().split("\n").map(Number);
        const matches = children.filter(pid => execFileSync("/bin/ps", ["-p", String(pid), "-o", "comm="], { encoding: "utf8", timeout: 5000 }).trim() === fixture.executable);
        assert.equal(matches.length, 1);
        const pid = matches[0];
        pids.push(pid);
        writeFileSync("owned-browser-pids.json", JSON.stringify(pids));
        const args = execFileSync("/bin/ps", ["-ww", "-p", String(pid), "-o", "args="], { encoding: "utf8", timeout: 5000 });
        assert.ok(args.startsWith(fixture.executable + " "));
        assert.ok(args.includes(" --remote-debugging-pipe "));
        assert.equal(args.includes("--remote-debugging-port"), false);
        assert.equal(args.includes("--no-sandbox"), false);
        const profile = args.match(/--user-data-dir=(\S+)/)?.[1];
        assert.ok(profile?.startsWith(output + "/puppeteer_dev_chrome_profile-"));
        assert.ok(existsSync(profile!));
        profiles.push(profile!);
        return pid;
      }
      try {
        assert.equal(state.manager.getConnection("pilot"), undefined);
        for (let cycle = 0; cycle < 2; cycle++) {
          assert.equal((await executeConnect(state, "pilot")).details.error, undefined);
          const connection = state.manager.getConnection("pilot")!;
          writeFileSync(`catalog-${cycle}.json`, JSON.stringify(connection.tools));
          assertBrowserCatalog(connection, expected.sdkNormalized);
          assert.deepEqual(state.toolMetadata.get("pilot")!.map(tool => tool.originalName).sort(), [...browserTools].sort());
          const serverPid = connection.transport.pid;
          assert.ok(Number.isInteger(serverPid));
          pids.push(serverPid);
          writeFileSync("owned-browser-pids.json", JSON.stringify(pids));
          const blank = await call("list_pages", {});
          const pages = [...blank.matchAll(/^(\d+): about:blank/gm)];
          assert.equal(pages.length, 1);
          const pageId = Number(pages[0][1]);
          const browserPid = browserIdentity(serverPid);
          const navigated = await call("navigate_page", { pageId, type: "url", url: fixture.url, timeout: 5000 });
          assert.ok(navigated.includes("Successfully navigated"));
          let snapshot = await call("take_snapshot", { pageId });
          assert.ok(snapshot.includes("Fictional pilot"));
          if (cycle === 0) {
            const inputUid = snapshot.match(/uid=(\d+_\d+) textbox "Name/)[1];
            await call("fill", { pageId, uid: inputUid, value: "Fictional Ada" });
            snapshot = await call("take_snapshot", { pageId });
            const buttonUid = snapshot.match(/uid=(\d+_\d+) button "Greet"/)[1];
            await call("click", { pageId, uid: buttonUid });
            assert.ok((await call("take_snapshot", { pageId })).includes("Hello Fictional Ada"));
            summary.domEffect = true;
            await call("take_screenshot", { pageId, format: "png", filePath: join(output, "fixture.png") });
            const image = readFileSync(join(output, "fixture.png"));
            assert.equal(image.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
            assert.equal(image.readUInt32BE(16), 1280);
            assert.equal(image.readUInt32BE(20), 720);
            summary.screenshot = true;
            const consoleText = await call("list_console_messages", { pageId, pageSize: 20, includePreservedMessages: false });
            const msgid = Number(consoleText.match(/msgid=(\d+)[^\n]*fictional-console-ready/)[1]);
            assert.ok((await call("get_console_message", { pageId, msgid })).includes("fictional-console-ready"));
            assert.ok(consoleText.includes("fictional-subresource-blocked"));
            assert.ok(consoleText.includes("fictional-redirect-blocked"));
            summary.consoleAndSubresourceRestriction = true;
            const network = await call("list_network_requests", { pageId, pageSize: 20, includePreservedRequests: false });
            const reqid = Number(network.match(/reqid=(\d+) GET http:\/\/127\.0\.0\.1:\d+\/data \[200\]/)[1]);
            const request = await call("get_network_request", { pageId, reqid });
            assert.ok(request.includes("fictional-response-body"));
            assert.equal(request.includes("fictional-header-value"), false);
            assert.equal(request.includes("fictional-cookie-value"), false);
            assert.match(request, /authorization:.*redacted/i);
            assert.match(request, /set-cookie:.*redacted/i);
            summary.networkHeaderRedaction = true;
            const auto = await call("performance_start_trace", { pageId, reload: true, autoStop: true, filePath: join(output, "automatic.json.gz") });
            assert.ok(auto.includes("performance trace has been stopped"));
            const manual = await call("performance_start_trace", { pageId, reload: false, autoStop: false });
            assert.ok(manual.includes("performance trace is being recorded"));
            await sleep(500);
            const stopped = await call("performance_stop_trace", { pageId, filePath: join(output, "explicit.json.gz") });
            assert.ok(stopped.includes("performance trace has been stopped"));
            summary.automaticAndExplicitTraces = true;
            for (const name of ["fixture.png", "automatic.json.gz", "explicit.json.gz"]) {
              const stat = statSync(join(output, name));
              assert.equal(stat.mode & 0o777, 0o600);
              assert.ok(stat.size > 100 && stat.size < 20_000_000);
            }
            const denied = await call("navigate_page", { pageId, type: "url", url: fixture.blocked, timeout: 5000 });
            assert.ok(denied.includes("Unable to navigate"));
            summary.navigationRestriction = true;
          }
          if (cycle === 1) {
            process.kill(serverPid, "SIGTERM");
            await exited(serverPid);
          }
          await state.manager.close("pilot");
          await exited(serverPid);
          await exited(browserPid);
          assert.equal(existsSync(profiles.at(-1)!), false);
        }
      } finally { await owner.stop(); }
      writeFileSync("summary.json", JSON.stringify({ ...summary, calls, catalogConnections: 2, eofAndSigtermShutdown: true, serverAndBrowserExits: pids.length, profilesRemoved: profiles.length }));
      return { content: [{ type: "text", text: "Fictional browser checks passed." }], details: {} };
    },
  });
}

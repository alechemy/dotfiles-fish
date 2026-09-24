import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { randomBytes, randomUUID } from "node:crypto";
import { chmodSync, mkdirSync, unlinkSync } from "node:fs";
import { createServer, type Server, type Socket } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default function (pi: ExtensionAPI) {
  const token = randomUUID().replaceAll("-", "");
  let context: ExtensionContext | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let waiting = false;
  let stopped = false;
  let warned = false;
  let queue = Promise.resolve();
  let sessionId: string | undefined;
  let feedbackToken: string | undefined;
  let feedbackSocket: string | undefined;
  let server: Server | undefined;
  const sockets = new Set<Socket>();

  function backend(): "cmux" | "standalone" {
    return process.env.CMUX_WORKSPACE_ID && process.env.CMUX_SURFACE_ID ? "cmux" : "standalone";
  }

  function report(clear = false) {
    const ctx = context;
    if (!ctx || (stopped && !clear)) return queue;
    const status = clear ? "clear" : waiting ? "blocked" : ctx.isIdle() ? "idle" : "working";
    const currentBackend = backend();
    const args = ["_activity", token, String(process.pid), status, "--backend", currentBackend];
    if (sessionId) args.push("--session-id", sessionId);
    if (currentBackend === "cmux" && process.env.CMUX_WORKSPACE_ID) args.push("--workspace-id", process.env.CMUX_WORKSPACE_ID);
    if (currentBackend === "cmux" && process.env.CMUX_SURFACE_ID) args.push("--surface-id", process.env.CMUX_SURFACE_ID);
    if (feedbackSocket) args.push("--feedback-socket", feedbackSocket);
    if (feedbackToken) args.push("--feedback-token", feedbackToken);
    queue = queue.then(async () => {
      let failure = "could not execute wt-pi";
      try {
        const result = await pi.exec("wt-pi", args, { cwd: ctx.cwd, timeout: 2000 });
        if (result.code === 0 && !result.killed) {
          warned = false;
          return;
        }
        failure = result.killed
          ? "the activity update timed out after 2 seconds"
          : `wt-pi exited with code ${result.code}`;
      } catch {}
      if (!warned && !clear) {
        ctx.ui.notify(`Worktrunk activity is unavailable because ${failure}. Run wt pi list in a shell to diagnose it.`, "warning");
        warned = true;
      }
    });
    return queue;
  }

  function stopReceiver() {
    for (const client of sockets) client.destroy();
    sockets.clear();
    server?.close();
    server = undefined;
    if (feedbackSocket) {
      try {
        unlinkSync(feedbackSocket);
      } catch {}
    }
    feedbackSocket = undefined;
    feedbackToken = undefined;
  }

  function reply(client: Socket, value: Record<string, unknown>) {
    client.end(`${JSON.stringify(value)}\n`);
  }

  async function startReceiver(ctx: ExtensionContext) {
    stopReceiver();
    if (backend() !== "cmux" || !sessionId) return;
    const uid = typeof process.getuid === "function" ? process.getuid() : 0;
    const directory = join(tmpdir(), `wt-pi-${uid}`);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    chmodSync(directory, 0o700);
    feedbackToken = randomBytes(32).toString("hex");
    feedbackSocket = join(directory, `${token}.sock`);
    const expectedSession = sessionId;
    const expectedWorkspace = process.env.CMUX_WORKSPACE_ID;
    const expectedSurface = process.env.CMUX_SURFACE_ID;
    server = createServer((client) => {
      sockets.add(client);
      client.setTimeout(5000);
      let input = "";
      client.on("data", (chunk) => {
        input += chunk;
        if (input.length > 65536) {
          client.destroy();
          return;
        }
        if (!input.includes("\n")) return;
        const line = input.slice(0, input.indexOf("\n"));
        let request: Record<string, unknown>;
        try {
          request = JSON.parse(line) as Record<string, unknown>;
        } catch {
          reply(client, { status: "refused", reason: "invalid-request" });
          return;
        }
        const requestId = typeof request.request_id === "string" ? request.request_id : undefined;
        if (
          request.token !== feedbackToken ||
          request.session_id !== expectedSession ||
          request.workspace_id !== expectedWorkspace ||
          request.surface_id !== expectedSurface ||
          typeof request.text !== "string" ||
          request.text.length === 0 ||
          sessionId !== expectedSession ||
          !ctx.isIdle()
        ) {
          reply(client, { request_id: requestId, status: "refused", reason: "recipient-not-idle" });
          return;
        }
        try {
          pi.sendUserMessage(request.text);
          reply(client, { request_id: requestId, status: "delivered" });
        } catch {
          reply(client, { request_id: requestId, status: "refused", reason: "delivery-failed" });
        }
      });
      client.on("timeout", () => client.destroy());
      client.on("close", () => sockets.delete(client));
    });
    await new Promise<void>((resolve, reject) => {
      server?.once("error", reject);
      server?.listen(feedbackSocket, () => {
        chmodSync(feedbackSocket as string, 0o600);
        resolve();
      });
    });
  }

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    context = ctx;
    stopped = false;
    waiting = false;
    sessionId = ctx.sessionManager.getSessionId();
    if (timer) clearInterval(timer);
    if (backend() === "cmux") await startReceiver(ctx);
    await report();
    if (stopped) return;
    timer = setInterval(() => { void report(); }, 30000);
    timer.unref();
  });

  pi.on("agent_start", () => report());
  pi.on("agent_settled", () => report());
  pi.on("ui_prompt_start", (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    waiting = true;
    return report();
  });
  pi.on("ui_prompt_end", (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    waiting = false;
    return report();
  });
  pi.on("session_shutdown", async () => {
    if (stopped) return;
    stopped = true;
    if (timer) clearInterval(timer);
    timer = undefined;
    await report(true);
    stopReceiver();
    context = undefined;
    sessionId = undefined;
  });
}

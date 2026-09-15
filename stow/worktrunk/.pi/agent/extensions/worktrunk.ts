import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { randomUUID } from "node:crypto";

export default function (pi: ExtensionAPI) {
  const token = randomUUID().replaceAll("-", "");
  let context: ExtensionContext | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let waiting = false;
  let stopped = false;
  let warned = false;
  let queue = Promise.resolve();

  function report(clear = false) {
    const ctx = context;
    if (!ctx || (stopped && !clear)) return queue;
    const status = clear ? "clear" : waiting ? "blocked" : ctx.isIdle() ? "idle" : "working";
    queue = queue.then(async () => {
      try {
        const result = await pi.exec("wt-pi", ["_activity", token, String(process.pid), status], {
          cwd: ctx.cwd,
          timeout: 2000,
        });
        if (result.code !== 0) throw new Error("Activity update failed");
        warned = false;
      } catch {
        if (!warned && !clear) {
          ctx.ui.notify("Worktrunk activity is unavailable. Run wt pi list in a shell to diagnose it.", "warning");
          warned = true;
        }
      }
    });
    return queue;
  }

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    context = ctx;
    stopped = false;
    waiting = false;
    if (timer) clearInterval(timer);
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
    context = undefined;
  });
}

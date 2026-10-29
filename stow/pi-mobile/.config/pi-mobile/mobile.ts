import { basename } from "node:path";
import { realpathSync, statSync } from "node:fs";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

function directoryError(ctx: ExtensionContext): string | undefined {
  const stored = ctx.sessionManager.getHeader()?.cwd;
  try {
    if (!stored || !statSync(stored).isDirectory() || realpathSync(stored) !== realpathSync(ctx.cwd)) {
      return "Mobile resume requires the conversation's original directory. Restore that directory before continuing.";
    }
  } catch {
    return "The conversation's original directory is unavailable. Restore it before continuing.";
  }
}

export default function (pi: ExtensionAPI) {
  if (process.env.PI_MOBILE !== "1") return;

  pi.on("session_start", async (_event, ctx) => {
    const error = directoryError(ctx);
    if (error) {
      ctx.ui.notify(error, "error");
      ctx.shutdown();
      return;
    }
    const pane = process.env.TMUX_PANE;
    if (pane && /^%\d+$/.test(pane)) {
      const name = ctx.sessionManager.getSessionName() || ctx.sessionManager.getSessionId().slice(0, 8);
      const title = `pi: ${basename(ctx.cwd)} / ${name}`.replace(/[\x00-\x1f\x7f-\x9f]/g, " ").slice(0, 100);
      await pi.exec("tmux", ["rename-window", "-t", pane, title], { timeout: 2000 });
      await pi.exec("tmux", ["set-option", "-p", "-t", pane, "@pi_mobile_cwd", ctx.cwd], { timeout: 2000 });
    }
  });

  pi.on("input", (_event, ctx) => {
    const error = directoryError(ctx);
    if (error) {
      ctx.ui.notify(error, "error");
      ctx.shutdown();
      return { action: "handled" };
    }
  });

  pi.on("tool_call", (_event, ctx) => {
    const error = directoryError(ctx);
    if (error) return { block: true, reason: error };
  });
}

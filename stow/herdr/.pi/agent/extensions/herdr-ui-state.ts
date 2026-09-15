import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  if (process.env.HERDR_ENV !== "1" || !process.env.HERDR_PANE_ID) return;

  let waiting = false;

  function clearWaiting() {
    if (!waiting) return;
    waiting = false;
    pi.events.emit("herdr:blocked", { active: false });
  }

  pi.on("ui_prompt_start", (_event, ctx) => {
    if (ctx.mode !== "tui" || waiting) return;
    waiting = true;
    pi.events.emit("herdr:blocked", { active: true, label: "Waiting for input" });
  });

  pi.on("ui_prompt_end", (_event, ctx) => {
    if (ctx.mode === "tui") clearWaiting();
  });

  pi.on("session_shutdown", clearWaiting);
}

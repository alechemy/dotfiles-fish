import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { BudgetLedger } from "./ledger.mjs";
import { capModel, CREDIT_SCALE, validateConfig } from "./policy.mjs";
import { wrapProvider } from "./provider.mjs";

export default function (pi: ExtensionAPI) {
  const inheritedOwner = process.env.PI_COPILOT_BUDGET_OWNER;
  let owner: string;
  let config = { contextCeiling: 272000, dailyCredits: 1550, sessionCredits: 1550, estimateMargin: 1.1 };
  let configError: Error | undefined;
  try {
    config = validateConfig(JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8")));
  } catch {
    configError = new Error("Copilot guard: invalid configuration; requests blocked.");
  }
  const ledger = new BudgetLedger(join(getAgentDir(), "copilot-budget"), config);
  if (configError) ledger.reserve = async () => { throw configError; };

  async function enforceCeiling(ctx: ExtensionContext) {
    if (ctx.model?.provider === "github-copilot" && ctx.model.contextWindow > config.contextCeiling) {
      const thinking = pi.getThinkingLevel();
      await pi.setModel(capModel(ctx.model, config));
      pi.setThinkingLevel(thinking);
    }
  }

  async function showStatus(ctx: ExtensionContext) {
    if (!ctx.hasUI || ctx.model?.provider !== "github-copilot") return;
    try {
      const status = await ledger.status(owner);
      ctx.ui.setStatus("copilot-budget", `Copilot${status.enforcementEnabled ? "" : " caps OFF"} day ${(status.daily / CREDIT_SCALE).toFixed(0)}/${config.dailyCredits} credits`);
    } catch {
      ctx.ui.setStatus("copilot-budget", "Copilot budget storage unavailable");
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    owner = ctx.mode === "tui" || ctx.mode === "rpc" || !inheritedOwner ? ctx.sessionManager.getSessionId() : inheritedOwner;
    process.env.PI_COPILOT_BUDGET_OWNER = owner;
    const provider = ctx.modelRegistry.getRegisteredNativeProvider("github-copilot") ?? ctx.modelRegistry.getProvider("github-copilot");
    if (!provider) return;
    pi.registerProvider(wrapProvider(provider, ledger, config, createAssistantMessageEventStream,
      () => owner));
    await enforceCeiling(ctx);
    await showStatus(ctx);
  });

  pi.on("model_select", async (_event, ctx) => {
    if (ctx.model?.provider !== "github-copilot") {
      if (ctx.hasUI) ctx.ui.setStatus("copilot-budget", undefined);
    } else {
      await enforceCeiling(ctx);
      await showStatus(ctx);
    }
  });
  pi.on("before_agent_start", async (_event, ctx) => { await enforceCeiling(ctx); });
  pi.on("message_end", async (_event, ctx) => { await showStatus(ctx); });

  pi.registerCommand("copilot-budget", {
    description: "Show Copilot estimates, or turn shared budget enforcement on/off",
    handler: async (args, ctx) => {
      const action = args.trim().toLowerCase();
      if (action && action !== "on" && action !== "off") {
        ctx.ui.notify("Usage: /copilot-budget [on|off]. This controls all guarded Pi sessions and subagents.", "warning");
        return;
      }
      try {
        if (action) await ledger.setEnforcement(action === "on");
        const status = await ledger.status(owner);
        await showStatus(ctx);
        ctx.ui.notify(`Copilot budget enforcement is ${status.enforcementEnabled ? "ON" : "OFF"} for all guarded Pi sessions and subagents. ` +
          `The setting persists until changed. Usage tracking and the context ceiling remain active. ` +
          `Copilot estimates for ${status.day} UTC: daily ${(status.daily / CREDIT_SCALE).toFixed(1)}/${config.dailyCredits} credits; ` +
          `session ${(status.session / CREDIT_SCALE).toFixed(1)}/${config.sessionCredits}. ` +
          `${status.reservations} request reservations. Prices include a ${Math.round((config.estimateMargin - 1) * 100)}% margin. ` +
          `${status.enforcementEnabled ? "The guard can stop early when the remaining budget cannot cover another request. " : "Requests can exceed both credit caps. "}Other Copilot apps are not counted.`,
          status.enforcementEnabled ? "info" : "warning");
      } catch {
        ctx.ui.notify("Copilot guard: budget storage unavailable; could not confirm enforcement. Requests remain subject to the stored policy.", "error");
      }
    },
  });
}

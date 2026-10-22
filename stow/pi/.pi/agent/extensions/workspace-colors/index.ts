import { getAgentDir, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { join } from "node:path";
import { automaticColor, normalizeColor, readOverrides, saveOverride, workspaceIdentity } from "./colors.mjs";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function (pi: ExtensionAPI) {
  const config = join(getAgentDir(), "workspace-colors.json");

  function workspace() {
    const id = process.env.CMUX_WORKSPACE_ID;
    return id && uuid.test(id) && uuid.test(process.env.CMUX_SURFACE_ID ?? "") ? id : undefined;
  }

  async function applyColor(id: string, color: string) {
    const result = await pi.exec("cmux", ["workspace-action", "--workspace", id, "--action", "set-color", "--color", color], { timeout: 2000 });
    if (result.code !== 0 || result.killed) throw new Error("Could not apply the workspace color through cmux.");
  }

  pi.on("session_start", async (_event, ctx) => {
    const id = workspace();
    if (ctx.mode !== "tui" || !id) return;
    try {
      const identity = await workspaceIdentity(ctx.cwd);
      const overrides = await readOverrides(config);
      await applyColor(id, overrides[identity] ?? automaticColor(identity));
    } catch (error) {
      ctx.ui.notify(`Workspace color: ${error instanceof Error ? error.message : "Configuration failed."}`, "warning");
    }
  });

  pi.registerCommand("workspace-color", {
    description: "Show the repository color, save a color name or #RRGGBB, or restore automatic selection with auto.",
    handler: async (args, ctx) => {
      const id = workspace();
      if (ctx.mode !== "tui" || !id) {
        if (ctx.hasUI) ctx.ui.notify("Workspace colors require an interactive Pi session in cmux.", "warning");
        return;
      }
      try {
        const identity = await workspaceIdentity(ctx.cwd);
        const value = args.trim();
        if (!value || value === "status") {
          const overrides = await readOverrides(config);
          ctx.ui.notify(`${identity}\nColor: ${overrides[identity] ?? automaticColor(identity)} (${overrides[identity] ? "saved override" : "automatic"})\nConfiguration: ${config}`, "info");
          return;
        }
        const override = value.toLowerCase() === "auto" ? null : normalizeColor(value);
        await saveOverride(config, identity, override);
        const color = override ?? automaticColor(identity);
        try {
          await applyColor(id, color);
        } catch {
          ctx.ui.notify(`Saved ${color} for ${identity}, but cmux could not apply it. Reload Pi to retry.`, "warning");
          return;
        }
        ctx.ui.notify(`${override ? "Saved" : "Restored automatic"} color ${color} for ${identity}.`, "info");
      } catch (error) {
        ctx.ui.notify(`Workspace color: ${error instanceof Error ? error.message : "Configuration failed."}`, "error");
      }
    },
  });
}

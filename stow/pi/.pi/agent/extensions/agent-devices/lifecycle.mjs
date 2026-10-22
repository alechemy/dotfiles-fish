export function registerDevices(pi, { parameters, pid, invoke }) {
  const sessions = new Set();
  const leases = new Map();
  const generations = new Map();

  const releaseSession = async (_event, ctx) => {
    const session = ctx.sessionManager.getSessionId();
    generations.set(session, (generations.get(session) ?? 0) + 1);
    if (!sessions.has(session)) return;
    await invoke(["release-session", "--session", session, "--owner-pid", String(pid)]);
    sessions.delete(session);
    for (const [lease, owner] of leases) if (owner === session) leases.delete(lease);
  };

  pi.registerTool({
    name: "agent_device",
    label: "Agent device",
    description: "Acquire and boot an owned iOS simulator or Android emulator, renew its 30-minute lease, release it after verification, or retain it for user-requested manual QA. Last-owner release shuts it down. Unmanaged running devices are preserved. Holds last at most 120 minutes and survive session exit.",
    parameters,
    executionMode: "sequential",
    promptGuidelines: [
      "Acquire before any workflow that can boot a simulator or emulator, including native run scripts. Use the returned target explicitly; do not let frameworks select another device.",
      "Release after verification, including failure or cancellation. Renew before the 30-minute expiry when using the device across tools or turns.",
      "For a long shell job, use ~/.local/bin/agent-device run <ios|android> <UDID|AVD> -- <command>. It protects the device while the job runs and releases its own ownership afterward. AGENT_DEVICE_TARGET contains its target.",
      "Use a hold only when the user explicitly requests manual QA, with an agreed duration. Do not release another session's lease or shut down unmanaged devices.",
    ],
    async execute(_id, params, signal, _update, ctx) {
      if (signal?.aborted) throw new Error("Device operation cancelled");
      const session = ctx.sessionManager.getSessionId();
      const generation = generations.get(session) ?? 0;
      let args;
      if (params.action === "acquire") {
        if (!params.platform || !params.device) throw new Error("acquire requires platform and device");
        args = ["acquire", params.platform, params.device, "--owner-pid", String(pid), "--session", session];
      } else if (params.action === "status") {
        args = ["status"];
      } else {
        if (!params.lease || leases.get(params.lease) !== session) throw new Error("Use a lease acquired by this session");
        args = [params.action, params.lease];
        if (params.action === "hold") args.push("--minutes", String(params.minutes ?? 60));
      }
      const result = await invoke(args);
      if (params.action === "acquire") {
        sessions.add(session);
        leases.set(result.lease, session);
        if (signal?.aborted || generation !== (generations.get(session) ?? 0) || session !== ctx.sessionManager.getSessionId()) {
          await invoke(["release", result.lease]);
          leases.delete(result.lease);
          throw new Error("Device operation cancelled or session changed; ownership released");
        }
      } else if (params.action === "release") {
        leases.delete(params.lease);
      }
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  });

  pi.registerCommand("agent-devices", {
    description: "Show managed simulators, emulators, and ownership expiry",
    handler: async (_args, ctx) => {
      const result = await invoke(["status"]);
      ctx.ui.notify(JSON.stringify(result), "info");
    },
  });

  pi.on("session_before_switch", releaseSession);
  pi.on("session_shutdown", releaseSession);
}

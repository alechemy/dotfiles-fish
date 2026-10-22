import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerDevices } from "./lifecycle.mjs";

const execute = promisify(execFile);

export default function (pi: ExtensionAPI) {
  registerDevices(pi, {
    parameters: Type.Object({
      action: Type.Union(["acquire", "renew", "release", "hold", "status"].map((value) => Type.Literal(value))),
      platform: Type.Optional(Type.Union([Type.Literal("ios"), Type.Literal("android")])),
      device: Type.Optional(Type.String({ description: "iOS simulator UDID or Android AVD name" })),
      lease: Type.Optional(Type.String()),
      minutes: Type.Optional(Type.Integer({ minimum: 1, maximum: 120 })),
    }),
    pid: process.pid,
    async invoke(args: string[]) {
      const { stdout } = await execute("/usr/bin/python3", [join(homedir(), ".local/bin/agent-device"), ...args], {
        timeout: 330_000,
        maxBuffer: 256 * 1024,
      });
      return JSON.parse(stdout);
    },
  });
}

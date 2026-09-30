import { getAgentDir, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export default async function (pi: ExtensionAPI) {
  process.env.PI_CMUX_NOTIFY_LEVEL = "disabled";
  process.env.PI_CMUX_SIDEBAR = "0";
  process.env.PI_CMUX_AUTOTITLE_DISABLED = "1";
  const entry = join(getAgentDir(), "npm", "node_modules", "pi-cmux", "extensions", "index.ts");
  const extension = await import(pathToFileURL(entry).href);
  extension.default(pi);
}

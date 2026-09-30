import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { proxyEnvironment, requireAuthority } from "./authority.mjs";

export const desktopTools = Object.freeze([
  "list_apps", "launch_app", "list_windows", "get_window_state",
  "click", "double_click", "right_click", "type_text", "press_key",
  "hotkey", "scroll", "drag", "set_value", "perform_actions",
]);
const actionTools = new Set(desktopTools.filter((name) => !["list_apps", "launch_app", "list_windows", "get_window_state", "perform_actions"].includes(name)));
const maxFrameBytes = 16 * 1024 * 1024;
const unavailable = "The cmux Computer Use connection closed. A dispatched action may have executed. Inspect before retrying; activate the connection explicitly again.";

export function validateArguments(name, args) {
  requireAuthority(desktopTools.includes(name) && args && typeof args === "object" && !Array.isArray(args), "This operation is outside the desktop-tool allowlist.");
  const inspect = (value, depth = 0) => {
    requireAuthority(depth <= 32, "Computer Use arguments exceed the nesting limit.");
    if (!value || typeof value !== "object") return;
    for (const [key, nested] of Object.entries(value)) {
      requireAuthority(key !== "session" && !key.startsWith("_") && !["__proto__", "constructor", "prototype"].includes(key), "Computer Use identity and private controls cannot be supplied by an agent.");
      requireAuthority(!["screenshot_out_file", "debug_image_out", "cdp_debugging_port", "webkit_inspector_port", "additional_arguments"].includes(key), "Computer Use screenshots stay inline; launch arguments, debugging servers, and file output are not exposed.");
      inspect(nested, depth + 1);
    }
  };
  inspect(args);
  if (name === "perform_actions") {
    requireAuthority(Array.isArray(args.actions) && args.actions.length > 0 && args.actions.length <= 20 && args.actions.every((action) => action && actionTools.has(action.tool) && action.arguments && typeof action.arguments === "object" && !Array.isArray(action.arguments)), "The action group contains an unsupported operation.");
  }
}

export class NativeProxy {
  #child;
  #pending;
  #closed = false;
  #exited = false;
  #closePromise;
  #sequence = 0;
  #queue = Promise.resolve();
  #token;
  #spawn;
  #timeout;
  #buffer = "";
  #decoder = new StringDecoder("utf8");

  constructor(authority, options = {}) {
    this.authorityKey = authority.key;
    this.#token = authority.token;
    this.#spawn = options.spawn ?? spawn;
    this.#timeout = options.timeout ?? 90000;
    this.#child = this.#spawn(authority.client, ["mcp", "--socket", authority.socket], {
      env: proxyEnvironment(authority), stdio: ["pipe", "pipe", "pipe"],
    });
    this.#child.stderr.on("data", () => {});
    this.#child.on("error", () => { this.#exited = !this.#child.pid; void this.close(); });
    this.#child.on("exit", () => { this.#exited = true; void this.close(); });
    this.#child.stdin.on("error", () => { void this.close(); });
    this.#child.stdout.on("data", (chunk) => this.#receive(chunk));
    this.#child.stdout.on("error", () => { void this.close(); });
    this.#child.stdout.on("end", () => { void this.close(); });
    this.#child.stderr.on("error", () => { void this.close(); });
  }

  get closed() { return this.#closed; }

  #receive(chunk) {
    this.#buffer += this.#decoder.write(chunk);
    if (Buffer.byteLength(this.#buffer) > maxFrameBytes) {
      void this.close();
      return;
    }
    let end;
    while ((end = this.#buffer.indexOf("\n")) >= 0 && !this.#closed) {
      const line = this.#buffer.slice(0, end);
      this.#buffer = this.#buffer.slice(end + 1);
      if (!line.trim()) continue;
      try {
        const reply = JSON.parse(line);
        requireAuthority(reply.jsonrpc === "2.0", "Invalid MCP envelope.");
        if (reply.id === undefined && reply.method === "notifications/tools/list_changed") {
          void this.close();
          return;
        }
        requireAuthority(this.#pending && reply.id === this.#pending.id && (Object.hasOwn(reply, "result") !== Object.hasOwn(reply, "error")), "Unexpected MCP response.");
        const pending = this.#pending;
        this.#pending = undefined;
        pending.cleanup();
        if (reply.error) {
          const code = Number.isInteger(reply.error.code) ? ` (${reply.error.code})` : "";
          const diagnostic = typeof reply.error.message === "string" ? reply.error.message.replaceAll(this.#token, "[redacted cmux credential]").slice(0, 1024) : "No diagnostic was provided.";
          pending.reject(new Error(`cmux Computer Use rejected the MCP request${code}. ${diagnostic}`));
          void this.close();
        } else {
          pending.resolve(reply.result);
        }
      } catch {
        void this.close();
      }
    }
  }

  #request(method, params, signal, timeout = this.#timeout) {
    if (this.#closed || signal?.aborted) return Promise.reject(new Error(unavailable));
    const id = ++this.#sequence;
    return new Promise((resolve, reject) => {
      const abort = () => { void this.close(); };
      const timer = setTimeout(abort, timeout);
      this.#pending = {
        id, resolve, reject,
        cleanup: () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); },
      };
      signal?.addEventListener("abort", abort, { once: true });
      this.#child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n", (error) => {
        if (error) void this.close();
      });
    });
  }

  #enqueue(operation) {
    const result = this.#queue.then(() => {
      requireAuthority(!this.#closed, unavailable);
      return operation();
    });
    this.#queue = result.catch(() => {});
    return result;
  }

  async discover() {
    return this.#enqueue(async () => {
      try {
        const init = await this.#request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "pi-cmux-cua", version: "0.1.0" } }, undefined, 10000);
        requireAuthority(["2025-06-18", "2024-11-05"].includes(init?.protocolVersion) && init?.serverInfo?.name === "cmux-cua" && typeof init.serverInfo.version === "string" && init?.capabilities?.tools && typeof init.capabilities.tools === "object" && !Array.isArray(init.capabilities.tools), "The bundled proxy returned an unsupported MCP initialization.");
        this.#child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
        const result = await this.#request("tools/list", {}, undefined, 10000);
        requireAuthority(Array.isArray(result?.tools) && result.tools.length <= 100 && !result.nextCursor, "The native tool catalog is invalid or incomplete.");
        const names = new Set();
        for (const tool of result.tools) {
          requireAuthority(typeof tool?.name === "string" && !names.has(tool.name) && typeof tool.description === "string" && tool.inputSchema?.type === "object", "The native tool catalog contains invalid schemas.");
          names.add(tool.name);
        }
        requireAuthority(names.has("get_window_state") && names.has("perform_actions") && desktopTools.every((name) => names.has(name)), "The bundled proxy does not advertise the expected native desktop profile.");
        return result.tools.filter((tool) => desktopTools.includes(tool.name));
      } catch {
        await this.close();
        throw new Error("Permission-free native MCP discovery failed. No desktop operation was sent.");
      }
    });
  }

  async call(name, args, signal, verifyAuthority) {
    validateArguments(name, args);
    const frozenArgs = JSON.parse(JSON.stringify(args));
    return this.#enqueue(async () => {
      requireAuthority(!signal?.aborted, "Computer Use was cancelled before dispatch.");
      try {
        await verifyAuthority();
        requireAuthority(!this.#closed && !signal?.aborted, "Computer Use was cancelled before dispatch.");
      } catch {
        await this.close();
        throw new Error("The originating cmux or Pi authority changed. No desktop operation was dispatched.");
      }
      const result = await this.#request("tools/call", { name, arguments: frozenArgs }, signal);
      try {
        requireAuthority(Array.isArray(result?.content) && (!Object.hasOwn(result, "isError") || typeof result.isError === "boolean"), "Invalid Computer Use result.");
        const content = result.content.map((item) => {
          if (item?.type === "text" && typeof item.text === "string") return { type: "text", text: item.text.replaceAll(this.#token, "[redacted cmux credential]") };
          requireAuthority(item?.type === "image" && typeof item.data === "string" && item.data.length > 0 && !item.data.includes(this.#token) && /^image\/(png|jpeg|webp|gif)$/.test(item.mimeType) && /^[A-Za-z0-9+/]*={0,2}$/.test(item.data) && item.data.length % 4 === 0, "Unsupported Computer Use content.");
          return { type: "image", data: item.data, mimeType: item.mimeType };
        });
        if (result.isError) throw new Error("Computer Use rejected the operation. " + content.filter((item) => item.type === "text").map((item) => item.text).join("\n"));
        if (result.structuredContent !== undefined) {
          const images = new Set(content.filter((item) => item.type === "image").map((item) => item.data));
          const text = JSON.stringify(result.structuredContent, (_key, value) => typeof value === "string" ? images.has(value) ? "[image returned separately]" : value : value).replaceAll(this.#token, "[redacted cmux credential]");
          if (!content.some((item) => {
            if (item.type !== "text") return false;
            try { return JSON.stringify(JSON.parse(item.text)) === text; } catch { return false; }
          })) content.push({ type: "text", text });
        }
        return { content, details: undefined };
      } catch (error) {
        await this.close();
        if (result?.isError && error instanceof Error && error.message.startsWith("Computer Use rejected the operation.")) throw error;
        throw new Error(unavailable);
      }
    });
  }

  close() {
    if (this.#closePromise) return this.#closePromise;
    this.#closed = true;
    const pending = this.#pending;
    this.#pending = undefined;
    pending?.cleanup();
    pending?.reject(new Error(unavailable));
    this.#buffer = "";
    this.#closePromise = new Promise((resolve) => {
      const child = this.#child;
      if (this.#exited || child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
      let killTimer;
      const termTimer = setTimeout(() => {
        child.kill("SIGTERM");
        killTimer = setTimeout(() => { child.kill("SIGKILL"); }, 1000);
        killTimer.unref?.();
      }, 1000);
      termTimer.unref?.();
      const finish = () => {
        clearTimeout(termTimer);
        clearTimeout(killTimer);
        child.removeListener("exit", finish);
        child.removeListener("close", finish);
        child.removeListener("error", finish);
        resolve();
      };
      child.once("exit", finish);
      child.once("close", finish);
      child.once("error", finish);
      child.stdin.end();
    });
    return this.#closePromise;
  }
}

import { execFile } from "node:child_process";
import { constants } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";

const policyScript = fileURLToPath(new URL("./policy.py", import.meta.url));
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function requireAuthority(condition, message) {
  if (!condition) throw new Error(message);
}

export function runCommand(file, args, env = process.env) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { env, timeout: 3000, maxBuffer: 1024 * 1024, encoding: "utf8" }, (error, stdout) => {
      if (error) reject(new Error("cmux authority verification failed."));
      else resolve(stdout);
    });
  });
}

export async function readToken(path, uid, io = { open }) {
  let file;
  try {
    file = await io.open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const metadata = await file.stat();
    requireAuthority(metadata.isFile() && metadata.uid === uid && metadata.nlink === 1 && (metadata.mode & 0o777) === 0o600 && metadata.size > 0 && metadata.size <= 1024, "The cmux credential file is not private and owner-controlled.");
    const bytes = Buffer.alloc(metadata.size + 1);
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
    requireAuthority(bytesRead === metadata.size, "The cmux credential changed while being read.");
    const token = bytes.subarray(0, bytesRead).toString("utf8").replace(/\n$/, "");
    bytes.fill(0);
    requireAuthority(/^[A-Za-z0-9]{32,256}$/.test(token), "The cmux credential file is invalid.");
    return token;
  } catch {
    throw new Error("The cmux credential file is unavailable or unsafe.");
  } finally {
    await file?.close();
  }
}

export async function resolveAuthority(options = {}) {
  const env = options.env ?? process.env;
  const uid = options.uid ?? process.getuid?.();
  const home = options.home ?? homedir();
  const temp = options.temp ?? tmpdir();
  const pid = options.pid ?? process.pid;
  const io = options.io ?? { lstat, realpath, open, run: runCommand };
  requireAuthority((options.platform ?? process.platform) === "darwin", "cmux Computer Use requires macOS.");
  requireAuthority(env.PI_SUBAGENT_CHILD !== "1", "Computer Use cannot be activated by a managed child.");
  requireAuthority(env.CMUX_COMPUTER_USE_MCP_DISABLED !== "1" && env.CMUX_COMPUTER_USE_APP_ENABLED === "1", "Computer Use is disabled for this terminal.");
  requireAuthority(uuid.test(env.CMUX_WORKSPACE_ID ?? "") && uuid.test(env.CMUX_SURFACE_ID ?? ""), "A native cmux terminal identity is required.");
  const scope = env.CMUX_CUA_RUNTIME_SCOPE;
  requireAuthority(typeof scope === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(scope), "The cmux runtime scope is invalid.");
  const cli = env.CMUX_BUNDLED_CLI_PATH;
  requireAuthority(typeof cli === "string" && isAbsolute(cli) && cli.endsWith("/Contents/Resources/bin/cmux"), "The app-bundled cmux CLI is required.");
  const bundle = dirname(dirname(dirname(dirname(cli))));
  requireAuthority(bundle.endsWith(".app"), "The cmux application owner is invalid.");
  const client = join(dirname(cli), "cmux-cua");
  const socket = env.CMUX_CUA_SOCKET_PATH;
  const cmuxSocket = env.CMUX_SOCKET_PATH;
  const tokenFile = env.CMUX_CUA_AUTH_TOKEN_FILE;
  const state = join(home, "Library/Application Support/cmux/cmux-cua/runtime", scope, "state");
  const helper = join(home, "Library/Application Support/cmux/cmux-cua/helper", scope, "cmux Computer Use.app/Contents/MacOS/cmux-cua");
  requireAuthority(typeof socket === "string" && isAbsolute(socket) && basename(socket) === "cmux-cua.sock" && basename(dirname(socket)) === scope && basename(dirname(dirname(socket))) === `cmux-cua-${uid}`, "The exact native cmux endpoint is required.");
  const runtimeRoot = await io.realpath(dirname(dirname(dirname(socket))));
  const allowedRoots = await Promise.all([temp, "/tmp"].map((path) => io.realpath(path)));
  requireAuthority(allowedRoots.includes(runtimeRoot), "The cmux endpoint is outside its private runtime.");
  requireAuthority(socket !== env.CMUX_CUA_CODEX_SOCKET_PATH && tokenFile === join(dirname(socket), "auth-token") && env.CMUX_CUA_STATE_DIR === state && env.CMUX_CUA_CLIENT_PATH === helper, "The cmux runtime paths do not share one native authority.");
  requireAuthority(typeof cmuxSocket === "string" && isAbsolute(cmuxSocket), "The live cmux control socket is required.");
  const stamps = [];
  for (const [path, kind, privateMode] of [[client, "file"], [cli, "file"], [dirname(socket), "directory", 0o700], [socket, "socket", 0o600], [cmuxSocket, "socket", 0o600], [state, "directory", 0o700]]) {
    const s = await io.lstat(path);
    requireAuthority(!s.isSymbolicLink() && (kind === "file" ? s.isFile() && (s.mode & 0o6022) === 0 && (s.mode & 0o111) !== 0 && [0, uid].includes(s.uid) : s.uid === uid && (s.mode & 0o777) === privateMode && (kind === "socket" ? s.isSocket() : s.isDirectory())), "A cmux executable or private runtime has unsafe ownership.");
    if (kind === "file") stamps.push([s.dev, s.ino, s.size, s.mtimeMs]);
  }
  const identity = JSON.parse(await io.run(cli, ["--socket", cmuxSocket, "--json", "--id-format", "uuids", "identify", "--workspace", env.CMUX_WORKSPACE_ID, "--surface", env.CMUX_SURFACE_ID], { ...env, CMUXTERM_CLI_RESPONSE_TIMEOUT_SEC: "0.75" }));
  requireAuthority(identity.caller?.surface_type === "terminal" && identity.caller.workspace_id?.toLowerCase() === env.CMUX_WORKSPACE_ID.toLowerCase() && identity.caller.surface_id?.toLowerCase() === env.CMUX_SURFACE_ID.toLowerCase(), "The originating cmux terminal is no longer live.");
  requireAuthority(identity.bundle_identifier === env.CMUX_BUNDLE_ID && identity.app_executable_path === join(identity.app_bundle_path, "Contents/MacOS/cmux") && await io.realpath(identity.app_bundle_path) === await io.realpath(bundle) && await io.realpath(identity.app_cli_path) === await io.realpath(cli) && await io.realpath(identity.socket_path) === await io.realpath(cmuxSocket), "The live cmux application does not own this connection.");
  let ancestor = pid;
  let owned = false;
  for (let depth = 0; depth < 16 && ancestor > 1; depth++) {
    const row = (await io.run("/bin/ps", ["-p", String(ancestor), "-o", "ppid=", "-o", "comm="])).trim().match(/^(\d+)\s+(.+)$/);
    requireAuthority(row, "The terminal process owner cannot be verified.");
    if (row[2] === identity.app_executable_path) {
      owned = true;
      break;
    }
    ancestor = Number(row[1]);
  }
  requireAuthority(owned, "The Pi process is not descended from the verified cmux application.");
  const config = JSON.parse(await io.run(cli, ["--socket", cmuxSocket, "--json", "config", "path"]));
  const configPaths = [config.primary, config.fallback, config.legacy].filter((path) => path !== undefined).map((path) => {
    requireAuthority(typeof path === "string" && !path.includes("\n"), "The cmux configuration owner cannot be verified.");
    const expanded = path.startsWith("~/") ? join(home, path.slice(2)) : path;
    requireAuthority(isAbsolute(expanded), "The cmux configuration owner cannot be verified.");
    return expanded;
  });
  requireAuthority(configPaths.length > 0, "The cmux configuration owner cannot be verified.");
  const policy = JSON.parse(await io.run("/usr/bin/python3", [policyScript, identity.bundle_identifier, ...configPaths]));
  requireAuthority(policy.enabled === true && policy.policyDisabled === false, "Computer Use is disabled by the live cmux setting or managed policy.");
  const token = await readToken(tokenFile, uid, io);
  return {
    client, socket, state, token, pid,
    workspace: env.CMUX_WORKSPACE_ID,
    surface: env.CMUX_SURFACE_ID,
    scope,
    key: JSON.stringify([cli, client, cmuxSocket, socket, state, token, pid, env.CMUX_WORKSPACE_ID, env.CMUX_SURFACE_ID, stamps, identity.bundle_identifier, identity.app_executable_path]),
  };
}

export function proxyEnvironment(authority, env = process.env) {
  const result = {};
  for (const key of ["HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"]) {
    if (env[key] !== undefined) result[key] = env[key];
  }
  return Object.assign(result, {
    CMUX_CUA_MCP_FORCE_PROXY: "1",
    CMUX_CUA_EXTERNAL_PERMISSION_FLOW: "1",
    CMUX_CUA_SOCKET_AUTH_TOKEN: authority.token,
    CMUX_CUA_DEFAULT_SESSION: `cmux-${authority.surface}`,
    CMUX_CUA_STATE_OWNER_PID: String(authority.pid),
    CMUX_CUA_RUNTIME_SCOPE: authority.scope,
    CMUX_CUA_STATE_DIR: authority.state,
    CMUX_WORKSPACE_ID: authority.workspace,
    CMUX_SURFACE_ID: authority.surface,
    CMUX_CUA_TELEMETRY_ENABLED: "false",
    CMUX_CUA_UPDATE_CHECK: "false",
    CMUX_CUA_CURSOR_GRADIENT: "#12c7f5,#2d8cff,#6c5cff",
    CMUX_CUA_CURSOR_BLOOM: "#2d8cff",
    CMUX_CUA_CURSOR_LABEL: "cmux",
    NODE_OPTIONS: "",
    BUN_OPTIONS: "",
    CUA_LOG: "off",
  });
}

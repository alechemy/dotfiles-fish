import { execFile } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, readFile, realpath, rename, rm, writeFile } from "node:fs/promises";
import { dirname, isAbsolute } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";

const exec = promisify(execFile);

export const palette = Object.freeze({
  red: "#C0392B", crimson: "#922B21", orange: "#A04000", amber: "#7D6608",
  olive: "#4A5C18", green: "#196F3D", teal: "#006B6B", aqua: "#0E6B8C",
  blue: "#1565C0", navy: "#1A5276", indigo: "#283593", purple: "#6A1B9A",
  magenta: "#AD1457", rose: "#880E4F", brown: "#7B3F00", charcoal: "#3E4B5E",
});

export function normalizeColor(value) {
  if (typeof value !== "string") throw new Error("Use a cmux color name or #RRGGBB.");
  const color = value.trim().toLowerCase();
  if (Object.hasOwn(palette, color)) return palette[color];
  if (/^#[0-9a-f]{6}$/.test(color)) return color.toUpperCase();
  throw new Error("Use a cmux color name or #RRGGBB.");
}

export function automaticColor(identity) {
  const colors = Object.values(palette);
  const index = createHash("sha256").update(identity).digest().readUInt32BE(0) % colors.length;
  return colors[index];
}

export async function workspaceIdentity(cwd) {
  const path = await realpath(cwd);
  const env = { ...process.env, LC_ALL: "C" };
  for (const key of ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"]) delete env[key];
  let common;
  try {
    const result = await exec("git", ["-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], {
      env, timeout: 2000, maxBuffer: 65536,
    });
    common = result.stdout.trimEnd();
  } catch (error) {
    if (error.code === 128 && error.stderr?.includes("not a git repository")) return `dir:${path}`;
    throw new Error("Could not resolve the repository for workspace colors.");
  }
  if (!isAbsolute(common)) throw new Error("Git returned an invalid shared directory.");
  return `git:${await realpath(common)}`;
}

export async function readOverrides(path) {
  let text;
  try {
    if (!(await lstat(path)).isFile()) throw new Error("Workspace color configuration must be a regular file.");
    text = await readFile(path, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw error;
  }
  const config = JSON.parse(text);
  if (config?.version !== 1 || !config.overrides || typeof config.overrides !== "object" || Array.isArray(config.overrides)) {
    throw new Error("Workspace color configuration requires version 1 and an overrides object.");
  }
  const overrides = {};
  for (const [key, value] of Object.entries(config.overrides)) {
    if (!/^(git|dir):\//.test(key)) throw new Error("Workspace color keys must be canonical git: or dir: paths.");
    overrides[key] = normalizeColor(value);
  }
  return overrides;
}

export async function saveOverride(path, identity, color) {
  const normalized = color === null ? null : normalizeColor(color);
  const lock = `${path}.lock`;
  const temporary = `${path}.${randomUUID()}.tmp`;
  await mkdir(dirname(path), { recursive: true });
  const deadline = Date.now() + 2000;
  for (;;) {
    try {
      await mkdir(lock, { mode: 0o700 });
      break;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      if (Date.now() >= deadline) throw new Error(`Workspace color configuration is locked: ${lock}`);
      await delay(25);
    }
  }
  try {
    const overrides = await readOverrides(path);
    if (normalized === null) delete overrides[identity];
    else overrides[identity] = normalized;
    await writeFile(temporary, `${JSON.stringify({ version: 1, overrides }, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    await rename(temporary, path);
  } finally {
    await rm(temporary, { force: true });
    await rm(lock, { recursive: true });
  }
}

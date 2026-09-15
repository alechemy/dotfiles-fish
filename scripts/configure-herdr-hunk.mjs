#!/usr/bin/env node
import { constants, copyFileSync, lstatSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [pluginRoot, configDir] = process.argv.slice(2);
if (!pluginRoot || !configDir) throw new Error("Expected plugin root and config directory.");
const { installKeys, resolveHerdrConfigPath } = await import(
  pathToFileURL(join(pluginRoot, "dist/keys-install.js"))
);
const target = join(configDir, "config.toml");
const seed = fileURLToPath(new URL(
  "../stow/herdr/_seed/.config/herdr/plugins/config/jhochenbaum.hunkdiff/config.toml",
  import.meta.url,
));
mkdirSync(configDir, { recursive: true });
try {
  lstatSync(target);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  copyFileSync(seed, target, constants.COPYFILE_EXCL);
}

const bindings = [
  ["prefix+f", "review", "hunk: review changes"],
  ["prefix+shift+f", "send-review", "hunk: send review to agent"],
  ["prefix+shift+c", "review:commit", "hunk: review the last commit"],
  ["prefix+shift+b", "review:branch", "hunk: review branch changes"],
  ["prefix+shift+a", "review:staged", "hunk: review staged changes"],
].map(([key, action, description]) => ({ key, action: `jhochenbaum.hunkdiff.${action}`, description }));
const result = installKeys(resolveHerdrConfigPath(process.env), bindings);
if (!result.ok || result.skipped?.length) throw new Error(result.message);
console.log("Hunk review and send bindings configured.");

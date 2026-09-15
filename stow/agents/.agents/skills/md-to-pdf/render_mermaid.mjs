import { createHash } from "node:crypto";
import { readFile, writeFile, realpath } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

function hash(data) {
  return createHash("sha256").update(data).digest("hex");
}

async function packageInfo(entry) {
  let directory = path.dirname(entry);
  while (directory !== path.dirname(directory)) {
    try {
      const data = JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
      if (data.name && data.version) return { directory, version: data.version };
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    directory = path.dirname(directory);
  }
  throw new Error("Package metadata was not found.");
}

async function main() {
  const [root, operation] = process.argv.slice(2);
  const require = createRequire(path.join(root, "package.json"));
  const entry = require.resolve("@mermaid-js/mermaid-cli");
  const cli = await packageInfo(entry);
  const dependencies = createRequire(entry);
  const mermaidEntry = dependencies.resolve("mermaid");
  const puppeteerEntry = dependencies.resolve("puppeteer");
  if (operation === "--describe") {
    const bundle = path.join(path.dirname(mermaidEntry), "mermaid.esm.mjs");
    console.log(JSON.stringify({
      cli: cli.version,
      mermaid: (await packageInfo(mermaidEntry)).version,
      puppeteer: (await packageInfo(puppeteerEntry)).version,
      node: process.version,
      cliSource: hash(await readFile(entry)),
      mermaidBundle: hash(await readFile(bundle)),
    }));
    return;
  }
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const { renderMermaid } = await import(pathToFileURL(entry));
  const { default: puppeteer } = await import(pathToFileURL(puppeteerEntry));
  const indexUrl = pathToFileURL(await realpath(path.join(cli.directory, "dist/index.html"))).href;
  const dependencyRoot = await realpath(path.join(root, "node_modules"));
  const font = (await readFile(input.font)).toString("base64");
  const fontCss = `@font-face {font-family: "Source Sans 3"; src: url(data:font/ttf;base64,${font}); font-weight: 100 900;}`;
  const browser = await puppeteer.launch({
    executablePath: input.browser,
    headless: true,
    pipe: true,
    userDataDir: path.join(input.output, "browser-profile"),
    args: ["--disable-background-networking", "--disable-component-update", "--disable-sync"],
  });
  let blocked = 0;
  let diagnostics = 0;
  const previousWarn = console.warn;
  console.warn = () => { diagnostics += 1; };
  const isolated = {
    async newPage() {
      const page = await browser.newPage();
      await page.setOfflineMode(true);
      await page.setRequestInterception(true);
      page.on("request", async (request) => {
        const target = new URL(request.url());
        let approved = target.href === indexUrl || target.protocol === "data:";
        if (target.origin === "https://mermaid-cli-intercept.invalid") {
          try {
            const file = await realpath(fileURLToPath(new URL(target.pathname, "file://")));
            const relative = path.relative(dependencyRoot, file);
            approved = relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
          } catch {
            approved = false;
          }
        }
        if (approved) {
          await request.continue({}, -10);
        } else {
          blocked += 1;
          await request.abort("blockedbyclient", 100);
        }
      });
      const goto = page.goto.bind(page);
      page.goto = async (...args) => {
        const result = await goto(...args);
        await page.evaluate(() => {
          const meta = document.createElement("meta");
          meta.httpEquiv = "Content-Security-Policy";
          meta.content = "default-src 'none'; script-src https://mermaid-cli-intercept.invalid 'unsafe-eval'; " +
            "style-src 'unsafe-inline' https://mermaid-cli-intercept.invalid; " +
            "font-src data: https://mermaid-cli-intercept.invalid; img-src data:; " +
            "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'";
          document.head.prepend(meta);
        });
        await page.addStyleTag({ content: fontCss });
        await page.evaluate(() => document.fonts.load('20px "Source Sans 3"'));
        return result;
      };
      return page;
    },
  };
  const results = [];
  try {
    for (const [index, diagram] of input.diagrams.entries()) {
      let rendered;
      try {
        rendered = await renderMermaid(isolated, diagram.source, "svg", {
          viewport: { width: 1600, height: 1200, deviceScaleFactor: 1 },
          backgroundColor: "white",
          mermaidConfig: { ...input.config, deterministicIDSeed: diagram.id },
          svgId: `diagram-${diagram.id}`,
        });
      } catch {
        if (blocked || diagnostics) {
          throw new Error(`Mermaid diagram ${index + 1} attempted a blocked request or produced browser diagnostics.`);
        }
        throw new Error(`Mermaid diagram ${index + 1} failed to render locally.`);
      }
      if (blocked || diagnostics) {
        throw new Error(`Mermaid diagram ${index + 1} attempted a blocked request or produced browser diagnostics.`);
      }
      await writeFile(path.join(input.output, `${diagram.id}.svg`), rendered.data);
      results.push({ id: diagram.id, title: rendered.title, description: rendered.desc });
    }
  } finally {
    console.warn = previousWarn;
    await browser.close();
  }
  console.log(JSON.stringify(results));
}

main().catch((error) => {
  const message = /^Mermaid diagram \d+ /.test(error.message)
    ? error.message : "The local Mermaid renderer could not start or complete.";
  console.error(message);
  process.exitCode = 1;
});

# Chrome DevTools adapter pilot

## Retirement

[Agent-browser](agent-browser.md) replaces this proposed integration and the active Claude Chrome DevTools plugin. The Pi adapter was never activated. The outcome and adoption gates below are historical records, not prerequisites for the replacement.

## Outcome

The finite browser checks passed on Pi `0.85.1` with signed Google Chrome `152.0.7977.83`. Both synthetic adapter modes also passed. Browser calls used the published adapter's `initializeMcp` and `executeCall` through Pi's installed `loadExtensions`, with catalog assertions on the same connection. This is an isolated fictional test, not a live Pi model or TUI session. Live adoption and Claude fallback retirement remain deferred. Keep native Context7, Things and the agent-reader overlay.

Homebrew upgraded only Pi from `0.85.0` to `0.85.1` after user approval. The corrected release removes the transitive unbundled-loader import defect that blocked the earlier retry. Historical Pi `0.84.4` synthetic successes remain separate evidence. The earlier Chrome for Testing `152.0.7977.42` archive remains rejected because its ad-hoc/linker-signed bundle lacks sealed signature resources. That packaging constraint is not evidence of tampering.

These opt-in resources live under `scripts/browser-pilot/`, outside Stow discovery, setup and app-owned configuration. No adapter was activated in live Pi. There is no production browser command, settings override or reusable profile.

## Reviewed inputs

| Input | Exact version or revision | Evidence and ownership |
|---|---|---|
| Pi | `0.85.1`, Git `d981de1229ef899957bbe968bc8dcda02a21f477` | Official npm integrity, installed source/docs, isolated loader import and runtime checks. Homebrew owns the host. |
| Node | `v24.18.0` | Installed runtime, version checked again in a cleared environment before test imports. |
| MCP adapter | `pi-mcp-adapter@2.32.1`, Git `10a45367e033a32026987a75d6f401e37340c86f` | Published npm source, registry SHA-512 verified. |
| Chrome server | `chrome-devtools-mcp@1.8.0`, Git `45f187b1e3202c9f32ddba913be5d68751c3caa3` | Official published source, registry SHA-512 and source tree verified; finite runtime checks passed. |
| Earlier browser | Chrome for Testing `152.0.7977.42`, `mac-arm64` | Official exact-version metadata and Google Storage archive. Failed strict signature gate; never launched. |
| Signed candidate | Google Chrome `152.0.7977.83`, `x86_64` + `arm64` | Official Google DMG fetched through Homebrew. Strict signature and Gatekeeper checks passed; finite headless launch checked. |
| Blocked host retry | Installed Pi `0.85.0` | Unbundled loader import failed before synthetic adapter registration. Historical unbundled-loader defect; no browser launched in that attempt. |

[`provenance.json`](../scripts/browser-pilot/provenance.json) records the release identities, integrity values, source-tree digests, Pi loader digest, lock digest and browser archive digest. [`package-lock.json`](../scripts/browser-pilot/package-lock.json) pins all 56 resolved registry entries. The scripts-disabled installation omitted optional packages and installed 39 packages. Every installed package tree matched its independently integrity-verified extracted tarball tree before execution.

Tree digests hash sorted `SHA256 + two spaces + relative POSIX filename + LF` records for every regular file in a package. Symlinks fail the runtime source check. The runner requires the exact 39-package inventory, package trees, lock, Pi loader, root index, transitive main entrypoint and top-level versions. It does not refresh a digest on mismatch.

The adapter's direct runtime resolutions were MCP client/core `2.0.0`, ext-apps `1.7.5`, keyring `1.3.0`, AJV `8.20.0`, ajv-formats `3.0.1`, cross-spawn `7.0.6`, open `10.2.0`, recheck `4.5.0`, smol-toml `1.8.0`, strip-json-comments `5.0.3` and Zod `4.5.4`. Pi supplies its own peers through the installed loader. The private adapter workspace installed no Pi peer, TypeScript loader or test framework.

All 56 downloaded manifests were checked for lifecycle hooks. None declared `preinstall`, `install` or `postinstall`. Ext-apps declares `prepare` and `prepack`; eventsource declares `prepare`; the adapter declares `prepack`. No hooks ran. Optional keyring native bindings and recheck native/JAR packages were omitted. Regex analysis, keyring, browser opening and Apps branches remain outside the executed scope. `--legacy-peer-deps` prevents automatic peer installation; the Pi loader supplies host peers deliberately.

The Chrome server bundles MCP SDK `1.30.0`, Puppeteer Core `25.8.0`, Lighthouse `13.4.1`, core-js `3.50.0`, semver `7.8.5`, yargs `18.1.0` and DevTools frontend `6fdb53e0f7a5cb1ebf16e40c45439a5c8007d9be`. Its bundle inventory is not a complete SBOM. Registry integrity binds downloaded bytes to the recorded metadata; publisher attestations, all dependency internals, Chrome itself, OS policy and external services were not audited.

## Source findings that constrain use

The adapter review covered configuration precedence, imports, server startup, filtering, proxy/direct approvals, lifecycle, auth boundaries, scripting, Apps and output retention. Important paths relative to the adapter package are `config.ts`, `index.ts`, `init.ts`, `server-manager.ts`, `proxy-modes.ts`, `direct-tools.ts`, `tool-metadata.ts`, `tool-approval.ts`, `ui-server.ts`, `ui-session.ts`, `metadata-cache.ts`, `mcp-output-guard.ts` and `tool-registrar.ts`.

- A missing metadata cache starts all enabled servers, including lazy servers. `init.ts:242-309` implements this cold-cache discovery. Seeding an empty cache avoids it for the test. That is a test precondition, not an upstream fix or a promise that ordinary lazy installation never starts a process.
- Programmatic `createMcpAdapter({ config })` or `initializeMcp(..., { config })` bypasses file configuration loading. `hostConfigDiscovery: "off"` alone does not disable shared/project files, imports or package declarations. Pi project trust does not establish an adapter configuration gate.
- `includeTools: []` means allow all. The finite runner rejects empty, wildcard and malformed allowlists. The normal proxy resolves filtered metadata and enforces approval before `tools/call`. Approval does not gate process startup or discovery.
- Apps iframe dispatch bypasses `includeTools` and `excludeTools`, at `ui-server.ts:435-551`. There is no universal Apps/prompt/resource-discovery off-switch. `MCP_UI_VIEWER=none` suppresses a viewer, not the underlying Apps server. `exposeResources: false` does not stop discovery. A tools-only Chrome catalog was therefore a required boundary, not permission to use arbitrary MCP servers.
- Sampling, elicitation, scripting, automatic auth and direct-tool registration are off in the test configuration. The separate direct-executor test checks approval refusal only. No approval broker is installed.
- Stdio children inherit the adapter's whole environment. A server `env: {}` does not clear credentials. The runner creates a new environment before any import, with synthetic HOME, agent, XDG, cwd, temporary and npm directories. It disables npm user/global config, uses memory-only test auth storage and disables auth caching/keyring recovery.
- Full text/JSON spills remain in `pi-mcp-output-*` after normal teardown, with file mode `0600`. Metadata cache and Pi sessions have separate owners. Images are not bounded by the text guard, and guard limits are not transport-frame or memory limits. This pilot tested text/JSON spills only.

The dependency review traced MCP 2.0.0's legacy stdio spawning, initialization, capability handling, request dispatch, result validation, timeout/cancellation and close paths in `client/dist/stdio.mjs`, `client/dist/index.mjs` and `client/dist/src-D_zzAWoS.mjs`. Legacy negotiation avoids the SDK's automatic probe-sibling process path. The stdio transport uses `shell: false`, closes stdin, then escalates to SIGTERM/SIGKILL on bounded waits. The actual fixture process exits were checked independently of adapter status.

The Chrome source review covered its entrypoints/options, browser launch and shutdown, required tools, telemetry/update/CrUX paths, output writers and CLI daemon. Relevant paths are `build/src/bin/`, `build/src/browser.js`, `build/src/config/`, `build/src/tools/`, `build/src/McpContext.js`, `build/src/McpResponse.js` and selected bundled Puppeteer code.

`navigate_page.initScript` executes injected JavaScript even when adapter scripting and `evaluate_script` are excluded. The finite driver rejects it. URL-pattern rules do not establish complete egress isolation; resource-loader redirects and suppressed Puppeteer rule-setup errors remain limits. Header redaction does not redact URLs, response bodies, console messages, screenshots or traces.

## Checked synthetic behavior

Both modes passed again on Pi `0.85.1`, with the same counts as the earlier `0.84.4` runs. Results used the unchanged published adapter through Pi's installed `loadExtensions` loader. There were no prompts, model calls, SDK agent sessions or ambient resource/package discovery. The small supplied headless context is not a full Pi session runtime.

| Layer | Observed result |
|---|---|
| Finite Pi-registered test tool using adapter `initializeMcp`, `executeConnect`, `executeCall` and direct executor | One cold-cache start; zero seeded lazy starts before explicit connect; disabled server never started. |
| Filtering and approval on that connection, repeated after reconnect | Only `allowed_echo` survived include/exclude filtering. Blocked, unexpected and hyphen/underscore names never dispatched. Six headless proxy/direct attempts returned `approval_required`. |
| Separately permitted synthetic-only configuration | Exactly five `allowed_echo` calls, including oversized fictional output. This configuration sets server approval false only inside the finite positive test; it is not a general headless approval workaround. The separate browser mode permits only its fixed fictional requests. |
| Protocol and catalog | Five tools-only fixture connections; no advertised sampling or elicitation; ten unsolicited requests received `-32601`. Catalog assertions reject resources, prompts, added tools, UI metadata and unexpected capabilities. These assertions run after discovery, not as a pre-discovery firewall. |
| Real `createMcpAdapter` registration, separate run | Actual `mcp` and `mcp__pilot` tools refused approval-gated calls and filtered blocked calls. No scripting tool registered. Zero fixture tool calls; one fixture start/exit. |
| Lifecycle and retention | Five fixture starts matched five exits in the seam run. Two `0600` text/JSON spill files survived adapter shutdown. The runner verified fixture PIDs had exited and removed each owned runtime tree, including spills/cache. Successful runs required no forced group cleanup. |
| Repository regressions | Eighteen offline Python tests include four Node stdlib regressions. They cover cleared environment, finite launch/arguments, catalog mutation refusal, source digests, version refusal, fixed fixture exchange and owned process cleanup, including PID identity changes. |

The registration test uses actual Pi registration APIs and actual adapter tool definitions. Its headless context and selected runtime tool-list methods are supplied by the finite driver. It does not test Pi's model loop, TUI approval dialog, default file discovery, persistent sessions or live extension coexistence. The seam test provides same-connection catalog visibility that the normal registration interface does not expose.

These finite checks do not cover hostile catalog-change races, every filtering alias, broker decisions, in-flight idle protection, timeouts/cancellation during initialization, binary-resource cleanup, image passthrough or crash recovery. The source findings about Apps and retention remain. Cleared credentials and separate directories are not an OS sandbox, network-denial mechanism or security certification.

## Browser acquisition gate

The exact official metadata URL was:

```text
https://googlechromelabs.github.io/chrome-for-testing/152.0.7977.42.json
```

The downloaded archive was:

```text
https://storage.googleapis.com/chrome-for-testing-public/152.0.7977.42/mac-arm64/chrome-mac-arm64.zip
SHA256 c9a7b6bfb57731944990ffb7cafc17ae2f2a2e25ad1f145f45584d7b799d3ce8
```

This digest records acquisition evidence, not an independently published trusted checksum. macOS `ditto -x -k` extracted the archive into an owned private directory outside the repository and `/Applications`. Static checks found bundle identifier `com.google.chrome.for.testing`, version `152.0.7977.42` and a Mach-O arm64 executable. ZIP CRC validation passed. All five archive symlinks were preserved.

`codesign --verify --deep --strict` failed on the top-level app with `code has no resources but signature indicates they must be present`. The archive has 645 entries and no `_CodeSignature` entries. `codesign -dv --verbose=4` reports `adhoc,linker-signed`, an unbound Info.plist, no TeamIdentifier and no sealed resources. Re-extraction cannot restore files absent from the archive.

The browser was never launched, even for `--version`. No quarantine removal, signing changes, signature relaxation, browser-sandbox changes or everyday-browser settings changes were made. The acquired app/extraction and archive were removed after saving diagnostic metadata. No profile was created.

## Corrected host and signed browser execution

The standard Google Chrome candidate has this acquisition identity:

```text
Version 152.0.7977.83
Bundle com.google.Chrome
Architectures x86_64 arm64
Developer ID Application: Google LLC (EQHXZ8M8AV)
TeamIdentifier EQHXZ8M8AV
Gatekeeper source Notarized Developer ID
DMG SHA256 9fe77bfc6f6e08bffba887da0730c7b513e6787ce7fc28bb681ba5e22ecee469
```

The user-supplied Homebrew cache matched those bytes. A read-only mount supplied only `Google Chrome.app`, copied with `ditto` into an owned private directory outside the repository and `/Applications`. The mount was detached and removed before execution. Strict `codesign --verify --deep --strict`, signing identity, exact version, universal architectures and `spctl --assess --type execute` all passed again. The runner repeats these gates before browsing. The cached DMG and app signatures remain unchanged.

Pi `0.85.0` previously failed before adapter registration through this import graph:

```text
dist/core/extensions/loader.js -> dist/index.js -> dist/main.js
-> dist/experimental/server.js -> undeclared @earendil-works/pi-server
```

The official `0.85.0` npm archive reproduced the installed defect. `PI_EXPERIMENTAL` did not gate static linking. This was a failure of the pilot's unbundled loader route, not an established failure of Pi's normal bundled CLI. No adapter, fixture, Chrome server or browser started in that attempt.

The official `0.85.1` archive removes the offending imports and excludes experimental modules. Its root index and loader are unchanged from `0.85.0`. The review checked all eleven changed unbundled JavaScript files and then inspected the installed import graph and Pi peers. Installed `extensions.md`, `sdk.md`, `packages.md`, `session-format.md` and the dynamic-tool and SDK extension examples were read completely. A comparison matched 203 installed unbundled JavaScript files to the integrity-verified official archive. This excludes bundles and launch scripts affected by Homebrew shebang rewriting; it is not a full host or dependency audit.

Homebrew's stale signed API metadata initially hid `0.85.1`. A supported forced API refresh exposed the release without updating Homebrew Git checkouts. The user then authorized `brew upgrade --formula pi-coding-agent`, with automatic updates and install cleanup disabled. Homebrew poured its `arm64_tahoe` bottle and warned that macOS 27 is prerelease. Node remained `v24.18.0`. A credential-free isolated import of the official installed loader passed before the accepted host gate changed. Both synthetic modes passed before any browser call. No installed-source patch or substitute loader was used.

### Browser coverage and limits

The runner deliberately seeds an empty metadata cache and supplies programmatic configuration. It launches the exact source-checked server through the actual adapter, not a direct MCP client. A separate finite configuration sets approval false only for the twelve named browser tools and fixed fictional arguments. The refusal modes keep approval enabled.

| Check | Actual result |
|---|---|
| Catalog and filtering | Two dispatch connections each exposed the expected 27 SDK-normalized tools and only the twelve filtered tool names. Tools/logging capabilities, exact schema/metadata digests and absence of prompts/resources/UI metadata passed before calls and after reconnect. |
| Blank page and ownership | Both fresh servers launched the approved executable over a debugging pipe, each with one blank page and an owned temporary user-data-dir. Checks used only owned PID metadata, never profile contents. |
| DOM and screenshot | Snapshot UIDs drove a fictional field fill and button click. A fresh snapshot contained the expected greeting. The PNG was exactly 1280x720. |
| Console and network | Known fictional message/request retrieval passed. The fixture received the fictional authorization header; returned authorization and set-cookie headers were redacted while the expected response body remained visible. |
| Performance | One five-second automatic trace and one short explicit start/stop trace passed. Both trace files and the PNG were nonempty, bounded and mode `0600`. |
| URL rules | Direct navigation, an ordinary page subresource fetch and a redirect targeted a second owned loopback listener outside the allowlist. The page reported failures and that listener received zero requests. This does not establish complete egress isolation. |
| Shutdown and retention | Nineteen calls across two connections passed. EOF and separately signaled SIGTERM shutdown each stopped the owned server/browser. Four server/browser exits and two removed profiles were checked. All observed owned descendants exited without forced cleanup before the runtime, cache, spills and output tree were deleted. |

The process observer tracks PID and start time, not mutable command titles. Server title changes therefore preserve descendant discovery. Changed birth identities refuse signaling that PID and make cleanup fail; the runtime remains for inspection. Regressions cover title changes followed by detached-child discovery and cleanup, PID reuse, metadata-query errors and retained-runtime failure.

[`browser-catalog.json`](../scripts/browser-pilot/browser-catalog.json) keeps separate full source-projection and expected SDK-normalized digests. The source projection invoked Chrome's registered `tools/list` handler without a transport or browser. MCP client `2.0.0`'s legacy `ToolAnnotationsSchema` strips `annotations.category`; it retains `execution: {taskSupport: "forbidden"}`. Only category is removed from source expectations. Observed tools are never normalized again to force a match. Two preliminary catalog-only runs stopped on this mismatch before any browser launch, and their owned processes/runtime trees were removed.

These are SDK-normalized catalog assertions, not raw-wire capture. The SDK strips unknown annotation keys generally, so this interface cannot establish that arbitrary unknown wire annotations were absent. Exact source pins, visible `_meta` checks and unchanged expected schemas remain required. Offline mutations verify rejection of SDK-visible changes, including execution, schemas, capabilities and UI metadata. They do not claim to detect fields already discarded by the SDK. Post-discovery assertions are not a pre-discovery firewall, and catalog-change races are not covered.

The small supplied headless Pi context is not a full agent session. No model loop, TUI approval dialog, real transcript, persistent session, ambient model/resource discovery or live extension coexistence was tested. Browser-internal networking, OS permission behavior, complete egress control, crash recovery and all dependency internals remain outside the evidence. Cleared HOME/environment and URL rules are not an OS sandbox. Header redaction does not sanitize URLs, bodies, screenshots, console messages or traces.

After correcting the ownership race, the parent reran both synthetic modes and all nineteen browser calls. Four server/browser exits, two removed profiles and twenty observed owned process exits passed without forced cleanup. The parent then removed the exact dependency/source and signed-app workspaces. Runtime profiles and outputs were already gone; the user's cached DMG still matches its recorded digest. Bounded source and review evidence remains in private artifacts.

### Repeating signed acquisition

After approval, `HOMEBREW_NO_AUTO_UPDATE=1 brew fetch --cask google-chrome` obtains the cask download without installing the app or triggering a Homebrew update. Obtain the cached DMG path with `HOMEBREW_NO_AUTO_UPDATE=1 brew --cache --cask google-chrome`. Homebrew's moving Google URL and `sha256 :no_check` do not authenticate the archive through a cask checksum. Record the downloaded SHA-256 and verify the extracted app's exact version, architecture, bundle identity, Google signing authority and Gatekeeper result on every acquisition. A changed candidate needs a new approval.

Mount the cached DMG read-only with `hdiutil attach -readonly -nobrowse -noautoopen`, using a private owned mountpoint. Copy only `Google Chrome.app` with `ditto` into a separate private directory, detach the mount, then run the strict codesign and Gatekeeper checks above. Record metadata without launching the executable. Do not use `brew install`, `open`, `/Applications`, signing changes or quarantine removal for this pilot. Preserve the user's cached DMG. The acquisition owner removes only its recorded mount/extraction directories after review.

## Repeating the synthetic checks

The offline suite installs and imports no downloaded code:

```bash
python3 -m unittest discover -s scripts/tests -p test_agent_browser_pilot.py
python3 -m unittest discover -s scripts/tests
node --check scripts/browser-pilot/load.mjs
```

Adapter execution is opt-in. First obtain approval for the exact locked source and execution scope. Do not use live Pi package directories, `pi install`, `pi -e npm:...`, `npx`, a project settings file or a global extension location for this test.

The following shell recipe creates only a private dependency workspace. Run it from the repository after approval, using the reviewed installed Node/npm and Pi 0.85.1. It does not download or launch a browser. Keep the printed workspace path until review and cleanup are complete.

```bash
set -euo pipefail
umask 077
TMPBASE="${TMPDIR:-/tmp}"
NODE="$(command -v node)"
NPM="$(command -v npm)"
PI_ROOT=/opt/homebrew/Cellar/pi-coding-agent/0.85.1/libexec/lib/node_modules/@earendil-works/pi-coding-agent
PILOT="$(mktemp -d "${TMPBASE%/}/pi-browser-deps.XXXXXX")"
mkdir "$PILOT/home" "$PILOT/tmp" "$PILOT/cache" "$PILOT/work"
: > "$PILOT/user.npmrc"
: > "$PILOT/global.npmrc"
cp scripts/browser-pilot/package.json scripts/browser-pilot/package-lock.json "$PILOT/work/"
(
  cd "$PILOT/work" || exit
  env -i HOME="$PILOT/home" TMPDIR="$PILOT/tmp" \
    PATH="$(dirname "$NODE"):/usr/bin:/bin" \
    npm_config_userconfig="$PILOT/user.npmrc" npm_config_globalconfig="$PILOT/global.npmrc" \
    npm_config_cache="$PILOT/cache" npm_config_update_notifier=false \
    "$NPM" ci --ignore-scripts --omit=optional --legacy-peer-deps --no-audit --no-fund
)
python3 scripts/test-agent-browser-pilot.py \
  --node "$NODE" --pi-root "$PI_ROOT" \
  --adapter "$PILOT/work/node_modules/pi-mcp-adapter"
printf '%s\n' "$PILOT"
```

The runner checks installed source bytes before adapter execution, copies only the finite test resources into fresh private runtime directories, and emits counts/booleans. The synthetic path accepts no URL or tool input. The opt-in browser path accepts only separately reviewed private source/app paths; it creates both loopback fixtures and all arguments itself. It removes successful runtime trees. On a tool failure it may retain only private synthetic diagnostics under a printed `pi-browser-failure-*` path; it does not print raw diagnostics. Synthetic timeout/error cleanup targets only that run's process group. Browser cleanup also tracks its owned detached descendants by PID identity. Review any cleanup failure before retrying.

To disable the pilot, stop invoking the runner. No live settings need removal. After the process and reviewer have finished, delete only the exact dependency or failure directory created by that invocation. For the recipe above, retain the shell's original `PILOT` value and use:

```bash
case "$PILOT" in
  "${TMPBASE%/}"/pi-browser-deps.*) rm -rf -- "$PILOT" ;;
  *) printf '%s\n' 'Refusing an unexpected cleanup path.' >&2 ;;
esac
```

Do not clean shared temp directories with a wildcard. Keep retained evidence outside git and do not inspect profiles, live authentication or generated settings to diagnose this test.

## Repeating browser checks and adoption gate

After acquiring and verifying the exact Chrome server tarball and signed app above, append both private paths to the synthetic runner command:

```bash
python3 scripts/test-agent-browser-pilot.py \
  --node "$NODE" --pi-root "$PI_ROOT" \
  --adapter "$PILOT/work/node_modules/pi-mcp-adapter" \
  --chrome-server "$CHROME_SERVER" --chrome-app "$CHROME_APP"
```

`CHROME_SERVER` identifies the independently integrity-verified `chrome-devtools-mcp@1.8.0` package tree. `CHROME_APP` identifies `Google Chrome.app` inside the acquisition owner's `pi-browser-app-*` directory directly under the OS temporary directory. No browser acquisition, dependency resolution or installation runs implicitly. Both synthetic modes must pass before browser preflight and execution. A changed package or browser candidate requires a new source review and approval.

The fixed server arguments are `--headless`, `--isolated`, the explicit signed `--executable-path`, `--viewport=1280x720`, `--no-usage-statistics`, `--no-performance-crux`, `--no-category-emulation`, `--redact-network-headers` and one exact owned loopback fixture URL pattern. `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1` precedes imports and execution. Attach options, existing profiles, custom Chrome flags, sandbox changes, `initScript`, script evaluation and uploads remain forbidden. Stop for user action if macOS requests consent.

The exact allowlist is `list_pages`, `navigate_page`, `take_snapshot`, `click`, `fill`, `take_screenshot`, `list_console_messages`, `get_console_message`, `list_network_requests`, `get_network_request`, `performance_start_trace` and `performance_stop_trace`. The driver asserts fixed argument shapes and source-reviewed catalog digests before each adapter call. It never accepts arbitrary browsing requests.

The runner bounds waits and output, records only owned descendant identities and verifies exit before deleting runtime/profile/output trees. Failures copy only private diagnostics, the last fictional result and catalog evidence to a named failure directory. If process cleanup cannot be established, the owned runtime is retained and the invocation fails. Do not retry or remove that runtime until its owned processes have been checked. Never kill system/default browsers or clean shared temp trees.

General adoption requires a separate production configuration/approval decision, upstream handling or explicit acceptance of cold-cache startup and Apps filtering limitations, and live Pi/TUI verification. SDK-normalized catalog visibility is another explicit limit. App-owned overrides, caches, auth, profiles and sessions must remain unstowed. The phase 3 package fragment, model pins and measurement gates are unchanged.

## MCP versus official CLI

The shipped `chrome-devtools` CLI can perform all required tasks through the same tools. Exact-revision CLI docs call it experimental while the packaged README does not; that inconsistency is not the reason to choose MCP.

The CLI uses a detached daemon and retains browser state until `stop`. Explicit `start` and implicit first-tool startup differ in isolation defaults. `--viaCli` enables broader defaults, including memory debugging and extensions, and daemon-version mismatch warns rather than refuses. Images create additional temporary files. These paths add lifecycle/state owners and do not test the Pi adapter. Full stdio MCP is the checked finite route. `--slim` lacks the required DOM/console/network/performance tools. No CLI path was executed.

Updates require a new source review, exact lock resolution with scripts disabled, hook inspection, integrity checks, intentional provenance changes and fresh synthetic gates. Do not refresh source digests just to make a test pass. Homebrew Pi updates require rechecking official docs and the loader boundary; a package pin does not pin the host or prove dependency safety. [Agent tooling maintenance](agent-tooling-maintenance.md) continues to own the existing three-package baseline.

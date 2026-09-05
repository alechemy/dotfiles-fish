# Chrome DevTools adapter pilot

## Outcome

Checkpoint 4 remains **deferred** for browser execution and live adoption. The earlier isolated synthetic adapter checks passed on Pi `0.84.4`. The signed Chrome retry stopped before adapter registration because the installed Pi `0.85.0` unbundled extension loader cannot resolve an undeclared dependency. Browser-task parity remains untested. Keep the Claude browser fallback, native Context7, Things helper and agent-reader overlay.

The separately approved standard Google Chrome `152.0.7977.83` candidate passes strict bundle-signature verification and Gatekeeper assessment. It resolves the browser acquisition gate without relaxing it. The earlier official Chrome for Testing `152.0.7977.42` archive remains rejected by that gate because its bundle is ad-hoc/linker-signed and lacks sealed signature resources. That historical packaging constraint is not evidence of corruption or tampering.

No Chrome server or browser code executed. No adapter was loaded into live Pi. These opt-in test resources live under `scripts/browser-pilot/`, outside Stow discovery, setup and app-owned configuration. There is no production browser command, bridge, settings override or profile in this change.

## Reviewed inputs

| Input | Exact version or revision | Evidence and ownership |
|---|---|---|
| Pi | `0.84.4` | Installed official docs and extension loader. Homebrew owns the host. |
| Node | `v24.18.0` | Installed runtime, version checked again in a cleared environment before test imports. |
| MCP adapter | `pi-mcp-adapter@2.32.1`, Git `10a45367e033a32026987a75d6f401e37340c86f` | Published npm source, registry SHA-512 verified. |
| Chrome server | `chrome-devtools-mcp@1.8.0`, Git `45f187b1e3202c9f32ddba913be5d68751c3caa3` | Official published source, registry SHA-512 verified. Source review only. |
| Earlier browser | Chrome for Testing `152.0.7977.42`, `mac-arm64` | Official exact-version metadata and Google Storage archive. Failed strict signature gate; never launched. |
| Signed candidate | Google Chrome `152.0.7977.83`, `x86_64` + `arm64` | Official Google DMG fetched through Homebrew. Strict signature and Gatekeeper checks passed; never launched. |
| Blocked host retry | Installed Pi `0.85.0` | Unbundled loader import failed before synthetic adapter registration. Not accepted as a replacement for the `0.84.4` execution gate. |

[`provenance.json`](../scripts/browser-pilot/provenance.json) records the release identities, integrity values, source-tree digests, Pi loader digest, lock digest and browser archive digest. [`package-lock.json`](../scripts/browser-pilot/package-lock.json) pins all 56 resolved registry entries. The scripts-disabled installation omitted optional packages and installed 39 packages. Every installed package tree matched its independently integrity-verified extracted tarball tree before execution.

Tree digests hash sorted `SHA256 + two spaces + relative POSIX filename + LF` records for every regular file in a package. Symlinks fail the runtime source check. The runner requires the exact 39-package inventory, package trees, lock, Pi loader and top-level versions. It does not refresh a digest on mismatch.

The adapter's direct runtime resolutions were MCP client/core `2.0.0`, ext-apps `1.7.5`, keyring `1.3.0`, AJV `8.20.0`, ajv-formats `3.0.1`, cross-spawn `7.0.6`, open `10.2.0`, recheck `4.5.0`, smol-toml `1.8.0`, strip-json-comments `5.0.3` and Zod `4.5.4`. Pi supplies its own peers through the installed loader. No new Pi, TypeScript loader or test framework was installed.

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

`navigate_page.initScript` executes injected JavaScript even when adapter scripting and `evaluate_script` are excluded. A future finite driver must omit it. URL-pattern rules do not establish complete egress isolation; resource-loader redirects and suppressed Puppeteer rule-setup errors remain limits. Header redaction does not redact URLs, response bodies, console messages, screenshots or traces.

## Checked synthetic behavior

Run results used the unchanged published adapter through Pi's installed `loadExtensions` loader. There were no prompts, model calls, SDK agent sessions or ambient resource/package discovery. The small supplied headless context is not a full Pi session runtime.

| Layer | Observed result |
|---|---|
| Finite Pi-registered test tool using adapter `initializeMcp`, `executeConnect`, `executeCall` and direct executor | One cold-cache start; zero seeded lazy starts before explicit connect; disabled server never started. |
| Filtering and approval on that connection, repeated after reconnect | Only `allowed_echo` survived include/exclude filtering. Blocked, unexpected and hyphen/underscore names never dispatched. Six headless proxy/direct attempts returned `approval_required`. |
| Separately permitted synthetic-only configuration | Exactly five `allowed_echo` calls, including oversized fictional output. This configuration sets server approval false only inside the finite positive test; it is not a headless approval workaround for real browsing. |
| Protocol and catalog | Five tools-only fixture connections; no advertised sampling or elicitation; ten unsolicited requests received `-32601`. Catalog assertions reject resources, prompts, added tools, UI metadata and unexpected capabilities. These assertions run after discovery, not as a pre-discovery firewall. |
| Real `createMcpAdapter` registration, separate run | Actual `mcp` and `mcp__pilot` tools refused approval-gated calls and filtered blocked calls. No scripting tool registered. Zero fixture tool calls; one fixture start/exit. |
| Lifecycle and retention | Five fixture starts matched five exits in the seam run. Two `0600` text/JSON spill files survived adapter shutdown. The runner verified fixture PIDs had exited and removed each owned runtime tree, including spills/cache. Successful runs required no forced group cleanup. |
| Repository regressions | Nine offline Python tests cover cleared environment, configuration, allowlist validation, source digests, version refusal, fixed fixture exchange and owned process-group cleanup, including exit between probe and signal. |

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

All browser runtime tasks remain pending: actual raw catalog/schema verification, navigation, DOM fill/click, snapshot, screenshot, console/network capture and header redaction, short automatic/manual traces, URL-rule tests, server/browser shutdown and reconnect, profile cleanup and artifact cleanup. Unsupported-browser behavior has not been attributed to the adapter.

## Signed Chrome retry and host blocker

The user fetched the standard Google Chrome DMG through Homebrew. A read-only mount supplied `Google Chrome.app`, copied into a private temporary directory outside the repository and `/Applications`; the mount was detached before this retry. Static acquisition evidence is:

```text
Version 152.0.7977.83
Bundle com.google.Chrome
Architectures x86_64 arm64
Developer ID Application: Google LLC (EQHXZ8M8AV)
TeamIdentifier EQHXZ8M8AV
Gatekeeper source Notarized Developer ID
DMG SHA256 9fe77bfc6f6e08bffba887da0730c7b513e6787ce7fc28bb681ba5e22ecee469
```

Both `codesign --verify --deep --strict` and `spctl --assess --type execute` passed, including a repeat check during implementation. The app was not launched or modified. The original Homebrew DMG cache remains unchanged. [`provenance.json`](../scripts/browser-pilot/provenance.json) retains the failed CfT acquisition separately from this signed candidate.

The installed `0.84.4` host path was absent; Homebrew's installed host was now `0.85.0`. A separately approved, source-only compatibility review read the installed packages, extensions, SDK and session-format documentation and relevant examples. The direct loader diff only moves the unchanged `isBundledNode` expression into `config.js`. Registration, runtime tool-list methods, aliases and explicit-path loading are unchanged. That comparison was insufficient: the transitive `main.js` imports changed too.

With temporary approval to test `0.85.0`, the first synthetic mode failed during module linking:

```text
dist/core/extensions/loader.js:24 -> dist/index.js:33
-> dist/main.js:42 -> dist/experimental/server.js:10-11
ERR_MODULE_NOT_FOUND: @earendil-works/pi-server
```

The exact official npm `0.85.0` archive passed registry SHA-512 verification. Its manifest, shrinkwrap, loader, `main.js` and `experimental/server.js` match the installed files byte for byte. The server imports `@earendil-works/pi-server` and its `/unix` export, but the package does not declare that dependency and its shrinkwrap does not include it. `PI_EXPERIMENTAL` gates command execution, not static module linking. No documented alternate extension loader avoids this graph. The current Homebrew formula consumes the same npm artifact without a dependency correction.

This establishes a defect in this pilot's unbundled loader route, not failure of the normal bundled Pi CLI. The package's `bin` points to `dist/bundle/cli.js`, not `dist/cli.js`; normal CLI behavior was not tested. No adapter, synthetic MCP fixture, Chrome server or browser started during the failed retry. The second synthetic mode did not run. Earlier successful `0.84.4` results above remain historical evidence, not `0.85.0` results.

The attempted host-gate update was reverted. The runner still requires the accepted Pi `0.84.4` version and loader digest, and refuses the installed `0.85.0` host. No dependency injection, installed-source patch, stub loader or direct MCP substitute is part of this pilot. A corrected upstream distribution, separately reviewed and installed through the host's owner, is needed before rerunning both synthetic modes and implementing browser checks. No host repair was attempted.

A subsequent source-only review verified the exact official Pi `0.85.1` archive. It removes the offending experimental imports from `main.js` and excludes experimental modules from the published package. No `pi-server` imports remain in shipped unbundled JavaScript; the loader and root index are unchanged from `0.85.0`. This addresses the known import defect in source, not runtime compatibility. Both Homebrew's formula and API still offered `0.85.0` at this check. Installation and isolated import/synthetic verification remain pending Homebrew availability. The candidate's integrity and revision are recorded in provenance; the accepted execution gate remains unchanged.

The failed runtime process exited, and the runner confirmed its owned process group had exited before removing the temporary runtime. After independent review, the parent removed the exact source, dependency, failure-diagnostic and app-extraction workspaces. Source comparisons, integrity metadata and review reports remain in the private review artifacts. The original Homebrew DMG remains intact. No browser profile or browser output was created.

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

The following shell recipe creates only a private dependency workspace. Run it from the repository after approval, using the reviewed installed Node/npm and Pi 0.84.4. It does not download or launch a browser. Keep the printed workspace path until review and cleanup are complete.

```bash
set -euo pipefail
umask 077
TMPBASE="${TMPDIR:-/tmp}"
NODE="$(command -v node)"
NPM="$(command -v npm)"
PI_ROOT=/opt/homebrew/Cellar/pi-coding-agent/0.84.4/libexec/lib/node_modules/@earendil-works/pi-coding-agent
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

The runner checks installed source bytes before adapter execution, copies only the finite test resources into fresh private runtime directories, and emits counts/booleans. It never accepts a URL, browser path or arbitrary tool input. It removes successful runtime trees. On a tool failure it may retain only private synthetic diagnostics under a printed `pi-browser-failure-*` path; it does not print raw diagnostics. Timeout/error cleanup targets only the process group created for that run. Review any cleanup failure before retrying.

To disable the pilot, stop invoking the runner. No live settings need removal. After the process and reviewer have finished, delete only the exact dependency or failure directory created by that invocation. For the recipe above, retain the shell's original `PILOT` value and use:

```bash
case "$PILOT" in
  "${TMPBASE%/}"/pi-browser-deps.*) rm -rf -- "$PILOT" ;;
  *) printf '%s\n' 'Refusing an unexpected cleanup path.' >&2 ;;
esac
```

Do not clean shared temp directories with a wildcard. Keep retained evidence outside git and do not inspect profiles, live authentication or generated settings to diagnose this test.

## Requirements to resume browser work

The exact signed candidate above has acquisition approval, but the Pi host blocker must be resolved through a separately approved corrected distribution and source review. Do not weaken the version/digest gate or repair installed imports locally. Then rerun both synthetic modes and review the exact raw tool schemas before any browser tool execution.

The source-reviewed full MCP launch would use an absolute Node/server entrypoint and separately approved signed Google Chrome executable, with `--headless`, `--isolated`, `--viewport=1280x720`, `--no-usage-statistics`, `--no-performance-crux`, `--no-category-emulation`, `--redact-network-headers` and one exact loopback fixture URL pattern. `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1` must be set before the entrypoint, including help/version calls. No attach flags, custom Chrome arguments, existing profile, `initScript`, uploads or `evaluate_script` belong in that finite test.

Only a separate finite fixture configuration may set `approveTools: false` for the exact 12 browser tools and fixed fictional requests. It must not change the headless-refusal tests or imply general browsing authorization. Confirm the actual browser executable, pipe transport and owned temporary `--user-data-dir` using only owned PID metadata; native macOS HOME handling is not an OS sandbox. Verify server/browser exit before removing owned profile/output trees, including failure paths. Stop for user interaction if macOS requests consent.

The proposed 12-tool allowlist is `list_pages`, `navigate_page`, `take_snapshot`, `click`, `fill`, `take_screenshot`, `list_console_messages`, `get_console_message`, `list_network_requests`, `get_network_request`, `performance_start_trace` and `performance_stop_trace`. Source projects 27 raw tools with the selected flags. Raw names, capabilities, schemas and absence of resources/prompts/UI metadata still need runtime confirmation on each dispatch connection. Do not infer an application-wide permission boundary from a finite driver assertion.

General adoption requires a separate production configuration/approval decision, upstream handling or explicit acceptance of cold-cache startup and Apps filtering limitations, a demonstrated browser lifecycle/output policy, and live Pi/TUI verification. App-owned adapter overrides, caches, auth, profiles and sessions must remain unstowed. Do not edit the phase 3 package fragment or measurement gates as part of a test rerun.

## MCP versus official CLI

The shipped `chrome-devtools` CLI can perform all required tasks through the same tools. Exact-revision CLI docs call it experimental while the packaged README does not; that inconsistency is not the reason to choose MCP.

The CLI uses a detached daemon and retains browser state until `stop`. Explicit `start` and implicit first-tool startup differ in isolation defaults. `--viaCli` enables broader defaults, including memory debugging and extensions, and daemon-version mismatch warns rather than refuses. Images create additional temporary files. These paths add lifecycle/state owners and do not test the Pi adapter. Full stdio MCP remains the proposed route. `--slim` lacks the required DOM/console/network/performance tools. No CLI path was executed.

Updates require a new source review, exact lock resolution with scripts disabled, hook inspection, integrity checks, intentional provenance changes and fresh synthetic gates. Do not refresh source digests just to make a test pass. Homebrew Pi updates require rechecking official docs and the loader boundary; a package pin does not pin the host or prove dependency safety. [Agent tooling maintenance](agent-tooling-maintenance.md) continues to own the existing three-package baseline.

# Copilot context and credit guard

The Stow package owns `stow/pi/.pi/agent/extensions/copilot-guard/`. Pi loads its entry point as a personal extension. It wraps the existing `github-copilot` provider while preserving its authentication, model discovery, transport, request hooks, and cancellation. Other providers retain their context windows and do not consume this budget.

## Limits

`config.json` declares the portable policy:

```json
{
  "contextCeiling": 272000,
  "dailyCredits": 1550,
  "sessionCredits": 1550,
  "estimateMargin": 1.1
}
```

The context ceiling applies to every Copilot chat model, including newly discovered models. Smaller model windows remain smaller. Pi's default 16,384-token reserve triggers compaction around 255,616 tokens for a 272,000-token window. VCC continues to handle the summary step.

The credit caps use a 31,000-credit monthly allowance divided by 20 workdays. The daily cap is shared across guarded Pi processes and resets at midnight UTC. The session cap survives compaction, reload, resume, and midnight. Guarded subagents charge their parent's session as well as the shared daily budget. Starting a separate session does not reset that day's total.

`/copilot-budget` reports the enforcement setting, estimates, and outstanding reservations. The status line shows the daily estimate when Copilot is selected and marks disabled caps with `caps OFF`. Edit the tracked configuration and reload to change the limits.

To disable or restore both credit caps:

```text
/copilot-budget off
/copilot-budget on
```

Enforcement defaults to on. This setting is shared by all guarded Pi sessions and subagents and persists across reloads, restarts, and midnight until changed. Each request reads it under the ledger lock. Existing requests retain their reservations and output allowances; other sessions refresh their status display on their next normal status update or `/copilot-budget` command. After disabling the caps, submit your prompt again to continue a stopped task.

With enforcement off, requests can exceed both caps without a budget-imposed output reduction. Usage tracking, reservations, the context ceiling, pricing validation, storage validation, and deferred-request blocking remain active. Turning enforcement back on uses all accumulated charges, including usage while disabled, and can immediately block another request. Neither command resets totals or refunds reservations. GitHub's own billing and quota controls are unchanged.

## Credit estimates and interruption

The reviewed rates in `policy.mjs` come from [GitHub's model pricing table](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing). One AI credit represents $0.01 of model usage. The guard accounts for input, output, cached input, and cache writes, then adds a 10% margin. It charges uncached input at the higher of the input and cache-write rates to allow for missing cache-write reporting. Models with a 200,000-token pricing threshold use long-context rates conservatively when their configured window exceeds that threshold.

Before contacting the model, the guard reserves the estimated maximum input and output cost under an exclusive file lock. With enforcement on, it lowers the request's output allowance when the remaining budget cannot cover the usual maximum. If the budget cannot cover the full context-window input estimate and at least 1,024 output tokens, or a deliberately smaller requested output such as a one-token cache refresh, the guard returns a non-retryable error before calling the transport. It therefore can stop before the displayed estimate reaches 1,550, particularly during parallel work.

After the response, reported usage replaces the reservation. Missing usage, an interrupted request, or a crash retains the conservative maximum charge. With enforcement on, reservations prevent simultaneous processes from allocating the same remaining credits. Provider-level retries are disabled for guarded requests; Pi-level retries must reserve credits again. Corrupt configuration, corrupt ledger data, unavailable storage, and models without reviewed prices block requests even with credit enforcement off. Deferred requests are blocked because they cannot be accounted for by this synchronous guard.

This is an estimated spending limit, not a GitHub billing balance. It starts counting when activated and does not reconstruct earlier usage. Other Copilot clients, external CLI agents, and extensions that call `pi-ai/compat` or HTTP directly are outside the guard. GitHub's account-level billing controls remain separate.

## Subagents and ownership

The settings fragment declares `subagents.defaultExtensions: ["~/.pi/agent/extensions/copilot-guard"]`. The guarded Subagents build loads it explicitly for native cloud children, including foreground children that disable ambient extensions and background runners. The local scout and editor retain their explicit empty extension lists and consume no Copilot credits.

The extension passes the root session identity through the process-local `PI_COPILOT_BUDGET_OWNER` environment variable. Foreground children inherit it while loading their extension; background runners inherit it at launch. Interactive Pi sessions select their own session identity. The ledger stores the enforcement setting, a hash of that identity, dates, and numeric charges or reservations, never prompts, responses, credentials, or transcript paths.

Custom agents with explicit extension lists must include the guard when using Copilot. Project settings can override Subagents defaults. Running Pi with extensions disabled also disables this safeguard. Reload every existing Pi process after activation; an older process can still make unguarded requests or launch unguarded children.

## Runtime files and recovery

The private ledger lives at `~/.pi/agent/copilot-budget/usage.json`, with mode 600 in a mode-700 directory. It stores usage and the shared enforcement setting as app-owned runtime state, not stowed or committed. Do not delete it to reload or resume a session.

A process killed during a ledger transaction can leave `usage.lock`. The guard then blocks requests. Close all Pi sessions and runners before removing that lock file. Keep `usage.json` and its reservations intact. Abandoned reservations remain charged rather than being automatically refunded.

## Verification

Run the credential-free tests with the installed host and guarded Subagents package:

```bash
PI_PACKAGE_ROOT="$(brew --prefix pi-coding-agent)/libexec/lib/node_modules/@earendil-works/pi-coding-agent" \
PI_SUBAGENTS_ROOT="$HOME/.pi/agent/local/provider-local-delegation/node_modules/pi-subagents" \
node --test scripts/tests/test_pi_copilot_guard.mjs
```

The tests cover context ceilings, credit arithmetic, session persistence across midnight, cross-process reservations, conservative unknown usage, corrupt state, output allowances, transport blocking, native Pi loading and reload, cloud-role discovery, and the actual foreground child-session factory. They also cover persistent enforcement toggles, accounting while disabled, re-enabled caps, invalid toggle state, and toggling enforcement for an existing child. They use disposable state and synthetic providers with network access blocked. Live GitHub billing accuracy remains unverified.

import { capModel, ratesFor, usageUnits } from "./policy.mjs";

const originalProvider = Symbol.for("dotfiles.copilot-guard.original-provider");

export function wrapProvider(provider, ledger, config, createStream, getSessionId) {
  const base = provider[originalProvider] ?? provider;
  function stream(model, context, options = {}, simple = false) {
    const output = createStream();
    const capped = capModel(model, config);
    void (async () => {
      let reservation;
      let lastMessage;
      let settled = false;
      try {
        options.signal?.throwIfAborted();
        if (options.deferred) throw new Error("Copilot guard: deferred requests are not supported; request blocked.");
        const rates = ratesFor(capped);
        reservation = await ledger.reserve(getSessionId(), capped, options, rates, options.signal);
        options.signal?.throwIfAborted();
        const requestModel = { ...capped, maxTokens: reservation.maxTokens };
        const requestOptions = { ...options, maxTokens: reservation.maxTokens, maxRetries: 0 };
        const inner = simple ? base.streamSimple(requestModel, context, requestOptions) : base.stream(requestModel, context, requestOptions);
        for await (const event of inner) {
          lastMessage = event.message ?? event.error ?? event.partial ?? lastMessage;
          if (event.type === "done" || event.type === "error") {
            await ledger.settle(reservation, usageUnits(lastMessage?.usage, rates, config.estimateMargin));
            settled = true;
          }
          output.push(event);
        }
        if (!settled) throw new Error("Copilot guard: provider ended without reporting final usage.");
        output.end();
      } catch (error) {
        const aborted = options.signal?.aborted ?? false;
        const message = {
          role: "assistant", content: [], api: model.api, provider: model.provider, model: model.id,
          usage: lastMessage?.usage ?? {
            input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
          },
          stopReason: aborted ? "aborted" : "error",
          errorMessage: error.message?.startsWith("Copilot") ? error.message :
            "Copilot guard: request interrupted or budget storage unavailable; requests blocked. Reserved credits remain counted.",
          timestamp: Date.now(),
        };
        output.push({ type: "error", reason: message.stopReason, error: message });
        output.end();
      }
    })();
    return output;
  }
  return {
    ...base,
    [originalProvider]: base,
    getModels: () => base.getModels().map((model) => capModel(model, config)),
    ...(base.getAllModels ? { getAllModels: () => base.getAllModels().map((model) => capModel(model, config)) } : {}),
    stream: (model, context, options) => stream(model, context, options),
    streamSimple: (model, context, options) => stream(model, context, options, true),
    ...(base.fetchDeferred ? { fetchDeferred: () => {
      throw new Error("Copilot guard: deferred requests are not supported; request blocked.");
    } } : {}),
  };
}

export const CREDIT_SCALE = 1_000_000;

const prices = {
  "gpt-5-mini": [0.25, 2, 0.025, 0],
  "gpt-5.3-codex": [1.75, 14, 0.175, 0],
  "gpt-5.4": [2.5, 15, 0.25, 0],
  "gpt-5.4-mini": [0.75, 4.5, 0.075, 0],
  "gpt-5.4-nano": [0.2, 1.25, 0.02, 0],
  "gpt-5.5": [5, 30, 0.5, 0],
  "gpt-5.6-luna": [0.2, 1.2, 0.02, 0.25],
  "gpt-5.6-sol": [4, 20, 0.4, 5],
  "gpt-5.6-terra": [2, 12, 0.2, 2.5],
  "gpt-6-astra": [10, 50, 1, 12.5],
  "gpt-6-luna": [0.1, 0.5, 0.01, 0.125],
  "gpt-6-sol": [2, 10, 0.2, 2.5],
  "gpt-6.1-sol": [2, 10, 0.1, 2.5],
  "claude-haiku-4.5": [1, 5, 0.1, 1.25],
  "claude-sonnet-4": [3, 15, 0.3, 3.75],
  "claude-sonnet-4.6": [3, 15, 0.3, 3.75],
  "claude-opus-4.8": [5, 25, 0.5, 6.25],
  "claude-opus-5": [5, 25, 0.5, 6.25],
  "claude-opus-5.5": [4, 20, 0.2, 5],
  "claude-sonnet-5": [2, 10, 0.2, 2.5],
  "claude-sonnet-5.5": [2, 10, 0.2, 2.5],
  "claude-fable-5": [10, 50, 1, 12.5],
  "claude-fable-5.1": [10, 50, 0.25, 12.5],
  "gemini-3.7-flash": [0.75, 3.75, 0.075, 0],
  "gemini-3.8-flash": [0.75, 3.75, 0.075, 0],
  "mai-code-1.1-flash": [0.2, 1.2, 0.02, 0],
  "grok-4.5": [2, 6, 0.5, 0],
  "grok-4.6": [2, 6, 0.5, 0],
  "grok-4.7": [2, 6, 0.5, 0],
  "kimi-k3": [3, 15, 0.3, 0],
};

export function validateConfig(config) {
  for (const key of ["contextCeiling", "dailyCredits", "sessionCredits"]) {
    if (!Number.isSafeInteger(config[key]) || config[key] <= 0) {
      throw new Error(`Copilot guard: invalid ${key}.`);
    }
  }
  if (!Number.isFinite(config.estimateMargin) || config.estimateMargin < 1) {
    throw new Error("Copilot guard: estimateMargin must be at least 1.");
  }
  return config;
}

export function capModel(model, config) {
  if (model.provider !== "github-copilot" || model.type && model.type !== "chat") return model;
  return { ...model, contextWindow: Math.min(model.contextWindow, config.contextCeiling) };
}

export function ratesFor(model) {
  const id = model.id.toLowerCase();
  let price = prices[id];
  if (id === "claude-opus-4.8-fast") price = [10, 50, 1, 12.5];
  if (!price) throw new Error(`Copilot guard: no reviewed credit pricing for ${model.id}; request blocked.`);
  const [input, output, cacheRead, cacheWrite] = price;
  const threshold = id.startsWith("grok-") || id === "gpt-5.6-luna" ? 200000 : 272000;
  const longContext = model.contextWindow > threshold && (id.startsWith("gpt-") || id.startsWith("grok-"));
  return {
    input: input * (longContext ? 2 : 1),
    output: output * (longContext ? id.startsWith("grok-") ? 2 : 1.5 : 1),
    cacheRead: cacheRead * (longContext ? 2 : 1),
    cacheWrite: cacheWrite * (longContext ? 2 : 1),
  };
}

export function usageUnits(usage, rates, margin) {
  if (!usage) return undefined;
  const keys = ["input", "output", "cacheRead", "cacheWrite"];
  if (keys.some((key) => !Number.isSafeInteger(usage[key]) || usage[key] < 0)) return undefined;
  if (!keys.some((key) => usage[key] > 0)) return undefined;
  const dollarsPerMillion = usage.input * Math.max(rates.input, rates.cacheWrite) +
    usage.output * rates.output + usage.cacheRead * rates.cacheRead + usage.cacheWrite * rates.cacheWrite;
  return Math.ceil(dollarsPerMillion * margin * CREDIT_SCALE / 10000);
}

export function requestUnits(model, outputTokens, rates, margin) {
  return Math.ceil((model.contextWindow * Math.max(rates.input, rates.cacheWrite) +
    outputTokens * rates.output) * margin * CREDIT_SCALE / 10000);
}

export function affordableOutput(model, options, availableUnits, rates, margin) {
  const inputUnits = requestUnits(model, 0, rates, margin);
  const affordable = Math.floor((availableUnits - inputUnits) * 10000 / (rates.output * margin * CREDIT_SCALE));
  return Math.min(model.maxTokens, options?.maxTokens ?? model.maxTokens, affordable);
}

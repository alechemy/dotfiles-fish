import { createHash, randomUUID } from "node:crypto";
import { closeSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout } from "node:timers/promises";
import { affordableOutput, CREDIT_SCALE, requestUnits } from "./policy.mjs";

function units(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function validate(state) {
  if (state?.version !== 1 || !state.days || !state.sessions || !state.reservations ||
    (state.enforcementEnabled !== undefined && typeof state.enforcementEnabled !== "boolean") ||
    [state.days, state.sessions, state.reservations].some((value) => typeof value !== "object" || Array.isArray(value)) ||
    [...Object.values(state.days), ...Object.values(state.sessions)].some((value) => !units(value)) ||
    Object.values(state.reservations).some((value) => !value || !units(value.units) ||
      typeof value.day !== "string" || typeof value.session !== "string")) {
    throw new Error("Copilot guard: invalid budget ledger; requests blocked.");
  }
  return state;
}

export class BudgetLedger {
  constructor(directory, config, now = () => new Date()) {
    this.directory = directory;
    this.config = config;
    this.now = now;
    this.path = join(directory, "usage.json");
    this.lock = join(directory, "usage.lock");
  }

  async transaction(update, signal) {
    mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    let fd;
    for (let attempt = 0; attempt < 100; attempt++) {
      signal?.throwIfAborted();
      try {
        fd = openSync(this.lock, "wx", 0o600);
        break;
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
        await setTimeout(50, undefined, { signal });
      }
    }
    if (fd === undefined) throw new Error("Copilot guard: budget ledger is locked; requests blocked.");
    const temporary = join(this.directory, `.${randomUUID()}.json`);
    try {
      let state;
      try {
        state = validate(JSON.parse(readFileSync(this.path, "utf8")));
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        state = { version: 1, days: {}, sessions: {}, reservations: {} };
      }
      const result = update(state);
      const output = openSync(temporary, "wx", 0o600);
      try {
        writeFileSync(output, JSON.stringify(validate(state)) + "\n");
        fsyncSync(output);
      } finally {
        closeSync(output);
      }
      renameSync(temporary, this.path);
      return result;
    } finally {
      try { unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
      closeSync(fd);
      unlinkSync(this.lock);
    }
  }

  identity(sessionId) {
    if (typeof sessionId !== "string" || !sessionId) throw new Error("Copilot guard: missing session identity; request blocked.");
    return createHash("sha256").update(sessionId).digest("hex");
  }

  totals(state, day, session) {
    let daily = state.days[day] ?? 0;
    let sessionTotal = state.sessions[session] ?? 0;
    for (const reservation of Object.values(state.reservations)) {
      if (reservation.day === day) daily += reservation.units;
      if (reservation.session === session) sessionTotal += reservation.units;
    }
    return { daily, session: sessionTotal };
  }

  async reserve(sessionId, model, options, rates, signal) {
    const session = this.identity(sessionId);
    const day = this.now().toISOString().slice(0, 10);
    return this.transaction((state) => {
      const totals = this.totals(state, day, session);
      const available = Math.min(this.config.dailyCredits * CREDIT_SCALE - totals.daily,
        this.config.sessionCredits * CREDIT_SCALE - totals.session);
      const enforcementEnabled = state.enforcementEnabled ?? true;
      const maxTokens = enforcementEnabled ? affordableOutput(model, options, available, rates, this.config.estimateMargin) :
        Math.min(model.maxTokens, options?.maxTokens ?? model.maxTokens);
      const minimumOutput = Math.min(1024, options?.maxTokens ?? model.maxTokens, model.maxTokens);
      if (!Number.isSafeInteger(maxTokens) || minimumOutput < 1 || maxTokens < minimumOutput) {
        throw new Error(`Copilot budget stopped: insufficient credits for another request. Daily estimate ${
          (totals.daily / CREDIT_SCALE).toFixed(1)}/${this.config.dailyCredits}; session estimate ${
          (totals.session / CREDIT_SCALE).toFixed(1)}/${this.config.sessionCredits}. Run /copilot-budget for details.`);
      }
      const id = randomUUID();
      const reservation = { day, session, units: requestUnits(model, maxTokens, rates, this.config.estimateMargin) };
      if (enforcementEnabled && reservation.units > available) throw new Error("Copilot budget stopped: insufficient credits for another request.");
      state.reservations[id] = reservation;
      return { id, maxTokens, ...reservation };
    }, signal);
  }

  async settle(reservation, actualUnits) {
    return this.transaction((state) => {
      const saved = state.reservations[reservation.id];
      if (!saved) throw new Error("Copilot guard: missing request reservation; requests blocked.");
      const charged = actualUnits ?? saved.units;
      if (!units(charged)) throw new Error("Copilot guard: invalid request usage; requests blocked.");
      state.days[saved.day] = (state.days[saved.day] ?? 0) + charged;
      state.sessions[saved.session] = (state.sessions[saved.session] ?? 0) + charged;
      delete state.reservations[reservation.id];
      return charged;
    });
  }

  async setEnforcement(enabled) {
    if (typeof enabled !== "boolean") throw new Error("Copilot guard: invalid enforcement setting.");
    return this.transaction((state) => { state.enforcementEnabled = enabled; });
  }

  async status(sessionId) {
    const session = this.identity(sessionId);
    const day = this.now().toISOString().slice(0, 10);
    return this.transaction((state) => ({ day, ...this.totals(state, day, session),
      enforcementEnabled: state.enforcementEnabled ?? true, reservations: Object.keys(state.reservations).length }));
  }
}

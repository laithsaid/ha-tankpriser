// The accuracy card's rendering, run against the real card file.
//
// Its `_body`/`_car`/`_table` build strings and touch no DOM, so they can be
// exercised directly on the prototype — no jsdom, same trick the other card
// tests use. What is checked is what the card *says*, because the whole point
// of it is to state a verdict a person can act on, and the two ways it could
// mislead are showing a raw "service not found" when the feature is simply off,
// and losing the sign of the error.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const CARD = path.join(
  __dirname, "..", "custom_components", "tankpriser", "www", "tankpriser-card.js"
);
const SRC = fs.readFileSync(CARD, "utf8");

const registry = new Map();
const store = new Map();
const ctx = {
  console,
  setTimeout,
  clearTimeout,
  URL,
  Image: class { set src(_v) {} },
  navigator: { userAgent: "", maxTouchPoints: 0 },
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => registry.set(t, c),
  },
  HTMLElement: class {},
  document: {
    head: { appendChild() {} },
    querySelector: () => null,
    createElement: () => ({
      style: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      appendChild() {},
      addEventListener() {},
      setAttribute() {},
    }),
  },
};
ctx.window = ctx;
ctx.window.location = { origin: "http://ha.local:8123" };
ctx.window.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
vm.createContext(ctx);
vm.runInContext(SRC, ctx, { filename: "tankpriser-card.js" });

const Card = registry.get("tankpriser-accuracy-card");
assert.ok(Card, "the accuracy card element is defined");
assert.ok(
  ctx.window.customCards.some((c) => c.type === "tankpriser-accuracy-card"),
  "and is listed in the card picker"
);

function card(state) {
  const instance = Object.create(Card.prototype);
  instance.setConfig(state.config || {});
  instance._report = state.report === undefined ? null : state.report;
  instance._error = state.error || "";
  instance._loading = Boolean(state.loading);
  return instance;
}

const GRADED = {
  car: "Passat",
  verdict: "consistently optimistic by about 21 % — the model is leaning the same way nearly every tank",
  tanks_scored: 5,
  mean_abs_error_pct: 22.4,
  bias_pct: 21.1,
  worst_error_pct: -38.0,
  within_10_pct: 1,
  low_finishes: 2,
  trend_pct: -4.5,
  tanks: [
    { index: 1, started: 1e9, ended: 1.0001e9, consumed_litres: 46.2, actual_days: 9.4,
      predicted_days: 11.2, predicted_rate: 4.1, actual_rate: 4.9, error_pct: 19.1,
      tanks_known: 1, confidence: 0.3, basis: "one tank", method: "time", finished_low: true },
    { index: 2, started: 1.001e9, ended: 1.002e9, consumed_litres: 44.0, actual_days: 12.0,
      predicted_days: 9.5, predicted_rate: 4.6, actual_rate: 3.7, error_pct: -20.8,
      tanks_known: 2, confidence: 0.5, basis: "tanks", method: "odometer", finished_low: false },
  ],
};

// 1. Off, not broken. The action does not exist until the option is switched
//    on, and "Service not found" would send someone hunting a bug.
const off = card({ error: "notavailable" });
const offHtml = off._body();
assert.ok(/Not switched on/.test(offHtml), "says it is switched off");
assert.ok(/Grade the fuel prediction/.test(offHtml), "and names the option to turn on");
assert.ok(!/not found/i.test(offHtml), "without leaking the raw error");

// A real failure still surfaces, rather than being swallowed as "off".
assert.ok(/boom/.test(card({ error: "boom" })._body()), "a genuine error is shown");

// 2. A car with nothing to grade explains why, instead of showing zeroes.
const fresh = card({ report: [{ car: "Polo", verdict: "not enough data", tanks_scored: 0 }] });
const freshHtml = fresh._body();
assert.ok(/first refuel scores\s+nothing/.test(freshHtml), "explains the first-tank rule");
assert.ok(!/lean/.test(freshHtml), "and shows no statistics it does not have");

// 3. A graded car: the lean is the headline, and the sign survives.
const graded = card({ report: [GRADED] });
const html = graded._body();
assert.ok(/Passat/.test(html), "names the car");
assert.ok(/consistently optimistic/.test(html), "leads with the verdict");
assert.ok(/\+21,1 %/.test(html), "the lean keeps its plus sign");
assert.ok(/-38 %/.test(html), "and the worst tank keeps its minus sign");
assert.ok(/1\/5/.test(html), "says how many landed close");
assert.ok(/fa-warn/.test(html), "a car leaning >10 % is marked as a warning");
assert.ok(
  /Positive means optimistic/.test(html),
  "and always says which direction is which"
);

// A car with no lean is not dressed up as a problem.
const level = card({ report: [{ ...GRADED, bias_pct: 2.0, verdict: "matching reality" }] });
assert.ok(/fa-good/.test(level._body()), "a centred model is marked good");
assert.ok(!/fa-warn/.test(level._body()), "and not as a warning");

// 4. Running tanks near empty is what makes optimism actually cost something.
assert.ok(
  /2 tanks ended/.test(html),
  "mentions the tanks that finished low"
);
assert.ok(
  !/tanks ended/.test(card({ report: [{ ...GRADED, low_finishes: 0 }] })._body()),
  "and stays quiet when none did"
);

// 5. The correction, stated as before/after rather than as a bare multiplier.
const corrected = card({
  report: [{
    ...GRADED,
    calibration_in_use: 1.07,
    corrected: { ...GRADED, bias_pct: 9.2 },
  }],
});
const corrHtml = corrected._body();
assert.ok(/shortened by 7 %/.test(corrHtml), "says what the correction does");
assert.ok(/burns faster/.test(corrHtml), "and why, in plain words");
assert.ok(/leaned \+21,1 %/.test(corrHtml), "shows the raw lean");
assert.ok(/\+9,2 %/.test(corrHtml), "and the corrected one, for comparison");

const slower = card({
  report: [{ ...GRADED, calibration_in_use: 0.9, corrected: { ...GRADED, bias_pct: -3 } }],
});
assert.ok(/lengthened by 10 %/.test(slower._body()), "the other direction reads correctly");
assert.ok(/burns slower/.test(slower._body()), "and says so");

const uncorrected = card({ report: [{ ...GRADED, calibration_in_use: 1.0 }] });
assert.ok(
  /No correction is being applied/.test(uncorrected._body()),
  "a factor of 1 is explained, not shown as '+0 %'"
);
assert.ok(
  !/Correction:/.test(card({ report: [GRADED] })._body()),
  "and a report with no correction data says nothing about one"
);

// 6. The table: newest first, both errors present, low tanks marked.
assert.ok(/<table class="fa-tanks">/.test(html), "the table is shown by default");
const rows = html.split("<tr>").slice(2);
assert.ok(/-20,8 %/.test(rows[0]), "newest tank first");
assert.ok(/fa-low/.test(rows[1]), "a tank that finished low is marked");
assert.ok(
  !/<table/.test(card({ config: { show_tanks: false }, report: [GRADED] })._body()),
  "show_tanks: false drops it"
);

// 7. A car name is data, and data is escaped.
const nasty = card({ report: [{ ...GRADED, car: '<img src=x onerror=alert(1)>' }] });
assert.ok(!/<img/.test(nasty._body()), "a car name cannot inject markup");
assert.ok(/&lt;img/.test(nasty._body()), "it is escaped, not dropped");

// 8. Config defaults.
const defaults = card({});
assert.strictEqual(defaults._config.car, "", "no car means all cars");
assert.strictEqual(defaults._config.show_tanks, true, "the table is on by default");
assert.strictEqual(defaults._config.title, "Prediction accuracy", "and it has a title");

console.log("accuracy card tests passed");

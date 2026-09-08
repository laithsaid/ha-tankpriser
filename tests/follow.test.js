// Drives the real card, in a real DOM, with a car that moves — the case the
// feature exists for: crossing Germany, where there is no national list and the
// only way to know where to fill up is to keep asking around the car.
//
// What matters here is not that a marker appears. It is *when* the card spends
// a request: every refresh is a real `tankpriser.nearby` call against the
// user's own API key, and a card that refetched on every `hass` update would
// burn that key in an afternoon.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const WWW = path.join(__dirname, "..", "custom_components", "tankpriser", "www");
const read = (p) => fs.readFileSync(path.join(WWW, p), "utf8");

const dom = new JSDOM(
  `<!doctype html><html><body><div id="host"></div></body></html>`,
  { pretendToBeVisual: true, runScripts: "dangerously", url: "http://ha.local:8123/" }
);
const { window } = dom;

Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { value: 800 });
Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { value: 500 });
Object.defineProperty(window.HTMLElement.prototype, "offsetWidth", { value: 800 });
Object.defineProperty(window.HTMLElement.prototype, "offsetHeight", { value: 500 });
window.HTMLElement.prototype.getBoundingClientRect = () => ({
  top: 0, left: 0, bottom: 500, right: 800, width: 800, height: 500, x: 0, y: 0,
});

function run(code, name) {
  const s = window.document.createElement("script");
  s.textContent = code;
  window.document.head.appendChild(s);
  if (!s.textContent) throw new Error("script did not run: " + name);
}

run(read("vendor/leaflet.js"), "leaflet");
run(read("vendor/leaflet.markercluster.js"), "markercluster");
run(read("tankpriser-card.js"), "card");
const Card = window.customElements.get("tankpriser-card");
assert.ok(Card, "card element defined");

const TRACKER = "device_tracker.tankpriser_sim";
const PRICE_ENTITY = "sensor.flensburg_super_e10";

// Somewhere on the A1 between Hamburg and Bremen.
const START = [53.5511, 9.9937];

let calls = [];
let answer = {
  fuel_type: "Super E10",
  country: "de",
  unit: "€/L",
  moving: true,
  searched_km: 135,
  stations: [
    { name: "BMÖ Syker Str. 29-31", company: "BMÖ", city: "Syke", latitude: 52.9142, longitude: 8.8206, price: 2.129, distance_km: 102.4 },
    { name: "tankpoint Hauptstraße 125", company: "tankpoint", city: "Weyhe", latitude: 52.9800, longitude: 8.8700, price: 2.139, distance_km: 119.9 },
  ],
};

function hassWith(position) {
  return {
    states: {
      [TRACKER]: {
        state: "not_home",
        attributes: { latitude: position[0], longitude: position[1], source_type: "gps", speed: 36.1, course: 229 },
      },
      [PRICE_ENTITY]: {
        state: "2.229",
        attributes: {
          friendly_name: "Super E10", fuel_type: "Super E10", unit_of_measurement: "€/L",
          // The anchored circle round Flensburg — what the map shows when there
          // is nothing fetched around the car yet.
          stations: [
            { name: "STAR Schleswiger Straße 17", company: "STAR", city: "Böklund", latitude: 54.6018, longitude: 9.5843, price: 2.229 },
          ],
        },
      },
    },
    themes: { darkMode: false },
    language: "en",
    user: { id: "u1", name: "Laith" },
    connection: {
      subscribeEvents: () => Promise.resolve(() => {}),
      sendMessagePromise: () => Promise.resolve({ stations: [] }),
    },
    callWS: async () => ({}),
    callService: async (domain, service, data) => {
      calls.push({ domain, service, data });
      return { response: answer };
    },
  };
}

async function settle(times = 25) {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 30));
}

const card = new Card();
card.setConfig({
  type: "custom:tankpriser-card",
  entity: PRICE_ENTITY,
  show_map: true,
  coverage: "area",
  show_my_location: false,
  cluster: false,
  follow_tracker: TRACKER,
});
window.document.getElementById("host").appendChild(card);

function markerCount(el) {
  return el.querySelectorAll(".leaflet-marker-icon").length;
}

(async () => {
  // --- first look: the card asks once, around the car ----------------------
  card.hass = hassWith(START);
  await settle();

  assert.strictEqual(calls.length, 1, "one nearby call on first render");
  assert.strictEqual(calls[0].service, "nearby", "it is the nearby action");
  assert.strictEqual(calls[0].domain, "tankpriser");
  assert.ok(
    Math.abs(calls[0].data.latitude - START[0]) < 1e-6 &&
      Math.abs(calls[0].data.longitude - START[1]) < 1e-6,
    "asked around the car, not around the anchor"
  );
  console.log("fetch  -> asked tankpriser.nearby at the car's position");

  const plotted = [...card.querySelectorAll(".ff-mprice")].map((e) => e.textContent);
  assert.ok(
    plotted.some((t) => t.includes("2,13")) || plotted.some((t) => t.includes("2.13")),
    "the stations fetched around the car are the ones on the map, got: " + plotted.join(" | ")
  );
  assert.ok(
    !plotted.some((t) => t.includes("2,23") || t.includes("2.23")),
    "the anchored Flensburg station is replaced once the car's own pool arrives"
  );
  console.log("plot   -> markers come from the car's pool, not the anchor circle");

  // --- a nudge down the road must not spend a request ----------------------
  calls = [];
  card._followAt = Date.now() - 2 * 60 * 1000; // past the one-a-minute floor
  card.hass = hassWith([53.5420, 9.9800]); // ~1.2 km
  await settle();
  assert.strictEqual(calls.length, 0, "1 km of driving does not refetch");
  console.log("thrift -> 1 km moved, no request");

  // --- far enough that the old pool no longer describes where you are ------
  card._followAt = Date.now() - 2 * 60 * 1000;
  card.hass = hassWith([53.4000, 9.7000]); // ~26 km
  await settle();
  assert.strictEqual(calls.length, 1, "26 km of driving refetches once");
  console.log("refresh-> 26 km moved, exactly one request");

  // --- the once-a-minute floor holds even when the car is flying ----------
  calls = [];
  card.hass = hassWith([53.2833, 9.5028]); // another ~22 km, seconds later
  await settle();
  assert.strictEqual(calls.length, 0, "the minute floor holds whatever the distance");
  console.log("floor  -> a second big jump within the minute is refused");

  // --- once the pool describes where you are, standing still costs nothing --
  // The jump above was refused by the floor, so the car is still 22 km from
  // the position its prices were fetched at. Let that catch up first...
  calls = [];
  card._followAt = Date.now() - 2 * 60 * 1000;
  card.hass = hassWith([53.2833, 9.5028]);
  await settle();
  assert.strictEqual(calls.length, 1, "the refused jump is picked up once the floor lifts");
  console.log("catchup-> the jump refused by the floor is fetched a minute later");

  // ...and now the car is parked where its prices came from.
  calls = [];
  card._followAt = Date.now() - 30 * 60 * 1000; // half an hour ago
  card.hass = hassWith([53.2833, 9.5028]); // has not moved since that fetch
  await settle();
  assert.strictEqual(calls.length, 0, "a stationary car never refetches");
  console.log("parked -> half an hour still, no request");

  // --- the view frames the car and the prices around it --------------------
  // Inherited from a national view the zoom can be street level, and then the
  // stations this mode exists to show are all off the screen.
  const bounds = card._map.getBounds();
  assert.ok(
    bounds.contains([53.2833, 9.5028]),
    "the car is in view"
  );
  assert.ok(
    bounds.contains([52.9142, 8.8206]),
    "so is the cheapest station it was offered, 100 km down the road"
  );
  console.log("frame  -> car and its nearest stations both in view");

  // --- the car itself is on the map ---------------------------------------
  assert.ok(markerCount(card) > 0, "markers exist");
  assert.ok(
    card.querySelector(".ff-follow-wrap"),
    "the followed car has its own marker"
  );
  console.log("car    -> the followed car is drawn on the map");

  console.log("\nfollow tests passed");
})().catch((e) => {
  console.error("FAIL", e && e.message);
  process.exit(1);
});

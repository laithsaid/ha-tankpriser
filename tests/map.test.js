// Drives the real card against real Leaflet + markercluster in a jsdom DOM, so
// the map layers are actually exercised — the headless-string tests cannot see
// a marker that never reaches the map.
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

// jsdom has no layout: Leaflet needs a non-zero map size to place anything.
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
assert.ok(window.L && window.L.markerClusterGroup, "Leaflet + plugin loaded");

// The card registers custom elements; jsdom supports customElements.
run(read("tankpriser-card.js"), "card");
const Card = window.customElements.get("tankpriser-card");
assert.ok(Card, "card element defined");

// --- fake hass -------------------------------------------------------------
const CAR_A = "sensor.passat_days_until_refuel";
const CAR_B = "sensor.polo_days_until_refuel";
const carState = (name, lat, lon, pct) => ({
  state: "12.3",
  attributes: {
    is_car: true, car_name: name, friendly_name: name,
    latitude: lat, longitude: lon, current_level_percent: pct,
    status: "ready", fuel_type: "Blyfri 95",
  },
});
// A price sensor, shaped like the real one: `stations` is already cheapest-first.
const PRICE_ENTITY = "sensor.tankpriser_blyfri_95_e10";
const priceState = () => ({
  state: "16.79",
  attributes: {
    friendly_name: "Blyfri 95 (E10)",
    fuel_type: "Blyfri 95 (E10)",
    fuel_key: "blyfri95",
    unit_of_measurement: "kr./L",
    area: "Home",
    radius: "10 km",
    station_count: 2,
    cheapest_station: "OK Nordre Ringvej",
    cheapest_price: 16.79,
    stations: [
      {
        name: "OK Nordre Ringvej", company: "OK", postnummer: "8600",
        city: "Silkeborg", address: "Nordre Ringvej 110", price: 16.79,
        updated: "2026-07-26", latitude: 56.18, longitude: 9.55,
        coord_approx: false,
      },
      {
        name: "F24 Motorvejen nord", company: "F24", postnummer: "4000",
        city: "Roskilde", address: "", price: 17.29,
        updated: "", latitude: 55.65, longitude: 12.08, coord_approx: true,
      },
    ],
  },
});

const hass = (cars) => ({
  states: cars,
  config: { latitude: 56.16, longitude: 10.2 },
  themes: { darkMode: false },
  connection: { subscribeEvents: () => Promise.resolve(() => {}), sendMessagePromise: () => Promise.resolve({ stations: [] }) },
  user: { id: "u-test" },
  locale: { language: "da" },
});

async function mount(cars, config = {}) {
  const card = new Card();
  card.setConfig({
    entity: "sensor.tankpriser_blyfri_95_e10",
    show_map: true,
    coverage: "area",
    show_my_location: false,
    ...config,
  });
  window.document.getElementById("host").appendChild(card);
  card.hass = hass(cars);
  // _updateMap is async (it awaits the Leaflet loader + icon preloads).
  for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 5));
  return card;
}

const carIcons = (card) =>
  card.querySelectorAll(".ff-car").length;
const clusterIcons = (card) =>
  card.querySelectorAll(".ff-ccars").length;

(async () => {
  // 1. Two cars in DIFFERENT places -> two car markers on the map.
  let card = await mount({
    [CAR_A]: carState("Passat", 56.16, 10.2, 80),
    [CAR_B]: carState("Polo", 56.4, 10.9, 30),
  });
  assert.ok(card._map, "map was created");
  assert.ok(card._carLayer, "car layer was created");
  console.log("apart  -> car icons:", carIcons(card), " cluster icons:", clusterIcons(card));
  assert.strictEqual(carIcons(card), 2, "two cars apart must both be drawn");

  // 2. Two cars at the IDENTICAL position -> one grouped marker.
  card = await mount({
    [CAR_A]: carState("Passat", 56.16, 10.2, 80),
    [CAR_B]: carState("Polo", 56.16, 10.2, 30),
  });
  console.log("same   -> car icons:", carIcons(card), " cluster icons:", clusterIcons(card));
  assert.strictEqual(clusterIcons(card), 1, "cars at one spot must group into one marker");
  const faces = card.querySelectorAll(".ff-ccar").length;
  assert.strictEqual(faces, 2, "the group must show both faces");

  // 2b. Tapping the group must spread the cars apart at *this* zoom — cars with
  //     identical coordinates never separate by zooming, so the default
  //     zoom-to-bounds behaviour would never open the group.
  //     A real DOM click, not group.fire(): Leaflet does not route a manually
  //     fired "clusterclick" to its own handler.
  const cluster = card._carLayer._featureGroup
    .getLayers()
    .find((layer) => layer._childCount > 1);
  assert.ok(cluster, "a cluster marker is on the map");
  const clusterEl = card.querySelector(".ff-ccars").parentElement;
  clusterEl.dispatchEvent(
    new window.MouseEvent("click", { bubbles: true, cancelable: true, view: window })
  );
  await new Promise((r) => setTimeout(r, 400)); // spiderfy animates
  const legs = cluster.getAllChildMarkers().filter((m) => m._spiderLeg).length;
  console.log("tapped -> car icons:", carIcons(card), " legs:", legs);
  assert.strictEqual(carIcons(card), 2, "tapping the group must reveal both cars");
  // Leaflet only gives the leg a DOM class when it has an SVG renderer, which
  // jsdom lacks, so assert on the leg objects rather than on `.leaflet-cluster-
  // spider-leg` nodes.
  assert.strictEqual(legs, 2, "each revealed car needs its leg back to the spot");

  // 3. A single car still shows up.
  card = await mount({ [CAR_A]: carState("Passat", 56.16, 10.2, 80) });
  console.log("single -> car icons:", carIcons(card), " cluster icons:", clusterIcons(card));
  assert.strictEqual(carIcons(card), 1, "a lone car must be drawn");
  // The marker draws a real SVG icon, not a text glyph.
  assert.strictEqual(
    card.querySelectorAll(".ff-car svg.ff-carsvg").length, 1,
    "the car icon renders as an inline SVG inside the marker"
  );
  assert.ok(
    card.querySelector(".ff-car svg path").getAttribute("d").startsWith("M5,11"),
    "and it is the mdi:car path"
  );

  // 4. Hiding a car removes it from the map.
  card = await mount({
    [CAR_A]: carState("Passat", 56.16, 10.2, 80),
    [CAR_B]: carState("Polo", 56.4, 10.9, 30),
  });
  card._setCarHidden(CAR_A, true);
  await new Promise((r) => setTimeout(r, 20));
  console.log("hidden -> car icons:", carIcons(card));
  assert.strictEqual(carIcons(card), 1, "hiding one car leaves the other");

  // 5. show_my_location: false must remove BOTH position buttons, not just ➤.
  //    Leaving ◎ behind was a real bug: tapping it called getCurrentPosition,
  //    so the option that means "do not use my location" still produced a
  //    browser permission prompt.
  card = await mount({ [CAR_A]: carState("Passat", 56.16, 10.2, 80) });
  const ctlOff = {
    bar: card.querySelectorAll(".ff-mapctl").length,
    recenter: card.querySelectorAll(".ff-recenter").length,
    follow: card.querySelectorAll(".ff-follow").length,
  };
  console.log("loc off ->", JSON.stringify(ctlOff));
  assert.strictEqual(ctlOff.recenter, 0, "◎ must not exist with show_my_location: false");
  assert.strictEqual(ctlOff.follow, 0, "➤ must not exist with show_my_location: false");
  assert.strictEqual(ctlOff.bar, 0, "the whole control bar goes with them");

  // 6. …and with it on, both are there.
  card = await mount({ [CAR_A]: carState("Passat", 56.16, 10.2, 80) }, {
    show_my_location: true,
  });
  const ctlOn = {
    recenter: card.querySelectorAll(".ff-recenter").length,
    follow: card.querySelectorAll(".ff-follow").length,
  };
  console.log("loc on  ->", JSON.stringify(ctlOn));
  assert.strictEqual(ctlOn.recenter, 1, "◎ is drawn when location is enabled");
  assert.strictEqual(ctlOn.follow, 1, "➤ is drawn when location is enabled");

  // 7. The price list names the town. Sorted by price alone, the cheapest row
  //    can be an hour away, so a row that says only "OK Nordre Ringvej" cannot
  //    be judged. The map popup always showed the city; the list did not.
  card = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false });
  const rows = [...card.querySelectorAll("tr")];
  assert.strictEqual(rows.length, 2, "one row per station");
  const first = rows[0].textContent.replace(/\s+/g, " ").trim();
  console.log("list   ->", first);
  assert.ok(first.includes("OK Nordre Ringvej"), first);
  assert.ok(first.includes("8600 Silkeborg"), "the row must name the town");
  assert.ok(first.includes("2026-07-26"), "and keep the chain's price date");
  // A station with no date still gets its town, with no stray separator.
  const second = rows[1].textContent.replace(/\s+/g, " ").trim();
  assert.ok(second.includes("4000 Roskilde"), second);
  assert.ok(!second.includes("·"), "no dangling separator when there is no date");

  console.log("\nmap tests passed");
})().catch((err) => {
  console.error("\nFAILED:", err && err.message);
  process.exit(1);
});

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

  // 8. Distances. With no live position the list measures from Home, and says
  //    so — "3,2 km" from an unstated origin is not information.
  card = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false });
  const head = card.querySelector(".ff-sub").textContent.replace(/\s+/g, " ").trim();
  const dists = [...card.querySelectorAll(".ff-dist")].map((el) => el.textContent.trim());
  console.log("dist   ->", head, "|", dists.join(" / "));
  assert.ok(head.includes("from home"), `the header must name the origin: ${head}`);
  assert.strictEqual(dists.length, 2, "every placed station gets a distance");
  // Home is 56.16,10.2 (Aarhus-ish): Silkeborg ~40 km west, Roskilde ~130 km east.
  assert.ok(/^4[01],\d km$/.test(dists[0]), `Silkeborg from Home: ${dists[0]}`);
  assert.ok(/^13\d,\d km$/.test(dists[1]), `Roskilde from Home: ${dists[1]}`);
  // The Roskilde pin is a postnummer centre, not the forecourt. One ≈ per row:
  // it sits beside the name, where it already carries the explanation, rather
  // than being repeated against a distance derived from the same estimate.
  const roskilde = [...card.querySelectorAll("tr")][1].textContent;
  assert.ok(roskilde.includes("≈"), "an estimated position stays marked");
  assert.strictEqual(roskilde.split("≈").length - 1, 1, "and marked exactly once");

  // A live fix takes over from Home, and says *that* — this is the in-the-car
  // case, where distances from the house are worse than none.
  card._onPosition({ coords: { latitude: 56.1755, longitude: 9.5455, accuracy: 12 } });
  const live = card.querySelector(".ff-sub").textContent.replace(/\s+/g, " ").trim();
  const liveDist = card.querySelector(".ff-dist").textContent.trim();
  console.log("live   ->", live, "|", liveDist);
  assert.ok(live.includes("from you"), `origin must switch to you: ${live}`);
  assert.ok(/^\d+ m$/.test(liveDist), `standing next to it reads in metres: ${liveDist}`);

  // Switched off, nothing is measured and the header says nothing about it.
  card = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false, show_distance: false });
  assert.strictEqual(card.querySelectorAll(".ff-dist").length, 0, "no distances when off");
  assert.ok(
    !card.querySelector(".ff-sub").textContent.includes("from"),
    "and no origin in the header"
  );

  // 9. sort: distance reorders the list — the sensor delivers cheapest-first,
  //    and Silkeborg is both dearer and nearer than Roskilde from Home.
  card = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false });
  const byPrice = [...card.querySelectorAll(".ff-name")].map((el) => el.textContent.trim());
  card = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false, sort: "distance" });
  const byDistance = [...card.querySelectorAll(".ff-name")].map((el) => el.textContent.trim());
  console.log("order  ->", byPrice[0].split("\n")[0], "|", byDistance[0].split("\n")[0]);
  assert.ok(byPrice[0].startsWith("OK Nordre Ringvej"), byPrice[0]);
  assert.ok(byDistance[0].startsWith("OK Nordre Ringvej"), byDistance[0]);
  // Same first row here (it is both cheapest and nearest), so prove the sort
  // works by moving Home next to the far station instead.
  const eastHass = hass({ [PRICE_ENTITY]: priceState() });
  eastHass.config = { latitude: 55.65, longitude: 12.08 }; // Roskilde
  const east = new Card();
  east.setConfig({ entity: PRICE_ENTITY, show_map: false, sort: "distance" });
  window.document.getElementById("host").appendChild(east);
  east.hass = eastHass;
  await new Promise((r) => setTimeout(r, 20));
  const fromEast = [...east.querySelectorAll(".ff-name")].map((el) => el.textContent.trim());
  assert.ok(
    fromEast[0].startsWith("F24 Motorvejen nord"),
    `nearest first from Roskilde, got ${fromEast[0]}`
  );

  // 10. The prediction card says which car it is about. One card shows one car,
  //     and two of them side by side were identical panels of numbers.
  const Pred = window.customElements.get("tankpriser-prediction-card");
  const predState = (name) => ({
    state: "9.4",
    attributes: {
      car_name: name, friendly_name: `${name} Days until refuel`,
      status: "ready", current_level_percent: 35, current_level_l: 23,
      fuel_type: "Blyfri 95", device_class: "duration",
    },
  });
  const PRED_ENTITY = "sensor.passat_days_until_refuel";
  const mountPred = (config) => {
    const el = new Pred();
    el.setConfig({ entity: PRED_ENTITY, ...config });
    window.document.getElementById("host").appendChild(el);
    el.hass = hass({ [PRED_ENTITY]: predState("Passat") });
    return el;
  };
  const header = (el) => el.querySelector("ha-card").getAttribute("header");

  console.log("pred   -> header:", JSON.stringify(header(mountPred({}))));
  assert.strictEqual(header(mountPred({})), "Passat", "untitled cards name the car");
  assert.strictEqual(header(mountPred({ title: "Min bil" })), "Min bil", "an explicit title wins");
  assert.strictEqual(header(mountPred({ title: "" })), null, "an empty title means no header");

  // The car picker must offer cars, not every Tankpriser sensor: only the
  // per-car predictions carry the duration device class. And it takes several.
  const predFields = window.eval("PRED_EDITOR_FIELDS");
  assert.strictEqual(
    predFields.entities.selector.entity.device_class,
    "duration",
    "the entity picker must be narrowed to the car sensors"
  );
  assert.strictEqual(
    predFields.entities.selector.entity.multiple,
    true,
    "and must accept more than one car"
  );

  // 11. Several cars in one card: a section each, named, and a single ask.
  const TWO = ["sensor.passat_days_until_refuel", "sensor.polo_days_until_refuel"];
  const both = new Pred();
  both.setConfig({ entities: TWO });
  window.document.getElementById("host").appendChild(both);
  both.hass = hass({ [TWO[0]]: predState("Passat"), [TWO[1]]: predState("Polo") });
  const names = [...both.querySelectorAll(".tp-pred-car")].map((el) => el.textContent.trim());
  console.log("cars   ->", names.join(" + "), "| sections:", both.querySelectorAll(".tp-pred-section").length);
  assert.deepStrictEqual(names, ["Passat", "Polo"], "each section names its car");
  assert.strictEqual(both.querySelectorAll(".tp-pred-section").length, 2, "one section per car");
  assert.strictEqual(
    both.querySelectorAll(".tp-pred-donate").length, 1,
    "the donation ask appears once per card, not once per car"
  );
  // It is not a setting. A leftover `show_donate: false` from an older config
  // must not take it away — the prediction is given in full, and this is the
  // only thing asked in return.
  assert.strictEqual(
    mountPred({ show_donate: false }).querySelectorAll(".tp-pred-donate").length, 1,
    "show_donate cannot hide the prediction card's ask"
  );
  const noDonate = await mount({ [PRICE_ENTITY]: priceState() }, {
    show_map: false, show_donate: false,
  });
  assert.strictEqual(
    noDonate.querySelectorAll(".ff-donate").length, 1,
    "nor the price card's"
  );
  assert.strictEqual(header(both), null, "a multi-car card takes no car's name as its header");
  assert.strictEqual(header(mountPred({})), "Passat", "…but a single-car card still does");
  // A single car must not be labelled twice — the ha-card header already says it.
  assert.strictEqual(
    mountPred({}).querySelectorAll(".tp-pred-car").length, 0,
    "no per-section name on a one-car card"
  );

  // A car that has gone missing must not take the other one down with it.
  const half = new Pred();
  half.setConfig({ entities: [TWO[0], "sensor.gone_days_until_refuel"] });
  window.document.getElementById("host").appendChild(half);
  half.hass = hass({ [TWO[0]]: predState("Passat") });
  assert.strictEqual(half.querySelectorAll(".tp-pred-section").length, 1, "the live car still renders");
  assert.ok(
    half.querySelector(".tp-pred-notice").textContent.includes("sensor.gone_days_until_refuel"),
    "and the missing one is named"
  );

  // The old single-entity config keeps working.
  const legacy = new Pred();
  legacy.setConfig({ entity: TWO[0] });
  assert.deepStrictEqual([...legacy._config.entities], [TWO[0]], "entity: still accepted");

  console.log("\nmap tests passed");
})().catch((err) => {
  console.error("\nFAILED:", err && err.message);
  process.exit(1);
});

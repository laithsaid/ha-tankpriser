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
  ctlOff.pick = card.querySelectorAll(".ff-pick").length;
  console.log("loc off ->", JSON.stringify(ctlOff));
  assert.strictEqual(ctlOff.recenter, 0, "◎ must not exist with show_my_location: false");
  assert.strictEqual(ctlOff.follow, 0, "➤ must not exist with show_my_location: false");
  // 📍 survives on purpose: pointing at a place is the one map mode that is not
  // about where you are, so switching your own dot off must not remove it.
  assert.strictEqual(ctlOff.pick, 1, "📍 is independent of show_my_location");

  // 5b. ...and ask_on_map: false removes it, leaving no bar at all once the
  //     position buttons are gone too.
  const noPick = await mount(
    { [CAR_A]: carState("Passat", 56.16, 10.2, 80) },
    { ask_on_map: false }
  );
  console.log("pick off -> bar:", noPick.querySelectorAll(".ff-mapctl").length);
  assert.strictEqual(
    noPick.querySelectorAll(".ff-pick").length,
    0,
    "ask_on_map: false removes 📍"
  );

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

  // …and it cannot be pointed somewhere else either. A dashboard is shared, and
  // its YAML gets copied between installs; redirecting the project's own ask
  // was a setting that only ever helped whoever changed it.
  const hijack = { donate_url: "https://evil.example/pay" };
  const hijacked = await mount({ [PRICE_ENTITY]: priceState() }, { show_map: false, ...hijack });
  const priceLink = hijacked.querySelector(".ff-donate a").getAttribute("href");
  assert.strictEqual(priceLink, "https://paypal.me/tankpriser", priceLink);
  const predLink = mountPred(hijack).querySelector(".tp-pred-donate a").getAttribute("href");
  assert.strictEqual(predLink, "https://paypal.me/tankpriser", predLink);

  // The integration is the source of truth: `const.py` puts the link on the
  // sensor, and the card follows it, so changing it in one place is enough.
  const moved = new Pred();
  moved.setConfig({ entity: PRED_ENTITY });
  window.document.getElementById("host").appendChild(moved);
  const movedState = predState("Passat");
  movedState.attributes.donate_url = "https://ko-fi.com/tankpriser";
  moved.hass = hass({ [PRED_ENTITY]: movedState });
  assert.strictEqual(
    moved.querySelector(".tp-pred-donate a").getAttribute("href"),
    "https://ko-fi.com/tankpriser",
    "the backend's link wins over the card's built-in copy"
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

  // --- the plugin must survive the global being handed back ----------------
  //
  // The harness above pre-loads Leaflet into the window, which is the one path
  // where this cannot break: the card sees a global it did not create, leaves
  // it alone, and _releaseGlobal never fires. When the card loads Leaflet
  // ITSELF it owns the global and gives it back afterwards, and markercluster
  // resolves `L` from the global inside its own function bodies — so every
  // cluster call made after the hand-back threw, and took the map's tiles down
  // with it. Assert the property directly: plugin bound to a private L, no
  // global anywhere, cluster call still works.
  {
    const iso = new JSDOM(`<!doctype html><html><body></body></html>`, {
      runScripts: "dangerously",
      url: "http://ha.local:8123/",
    });
    const w = iso.window;
    const s = w.document.createElement("script");
    s.textContent = read("vendor/leaflet.js");
    w.document.head.appendChild(s);
    const priv = w.L;
    assert.ok(priv, "leaflet loaded into the isolated window");

    // Exactly what the card does now: L as a parameter, never via the global.
    new w.Function("L", read("vendor/leaflet.markercluster.js"))(priv);
    assert.ok(priv.markerClusterGroup, "plugin attached to our private handle");

    // Now hand the global back, the way _releaseGlobal does.
    if (typeof priv.noConflict === "function") priv.noConflict();
    else delete w.L;
    assert.strictEqual(w.L, undefined, "the global really is gone");

    const group = priv.markerClusterGroup({ maxClusterRadius: 48 });
    assert.ok(group, "clustering still works with no global L");
    assert.ok(group instanceof priv.MarkerClusterGroup, "and it is a real cluster group");
    console.log("global -> clustering survives handing window.L back");
  }

  // --- the border shows both sides -----------------------------------------
  // The service answers a border position with one block per country, because
  // one ranked list cannot hold two currencies. A card that plotted only the
  // top-level `stations` drew half the map and stopped at the border.
  {
    const card = await mount({});
    const answer = {
      fuel_type: "Blyfri 95 (E10)",
      unit: "kr./L",
      stations: [
        { name: "OK Kruså", company: "ok", latitude: 54.84, longitude: 9.40,
          price: 17.59, distance_km: 4.2, coord_approx: false },
      ],
      countries: [
        {
          country: "dk", country_name: "Denmark", unit: "kr./L", decimals: 2,
          fuel_type: "Blyfri 95 (E10)",
          stations: [
            { name: "OK Kruså", company: "ok", latitude: 54.84, longitude: 9.40,
              price: 17.59, distance_km: 4.2, coord_approx: false },
          ],
        },
        {
          country: "de", country_name: "Germany", unit: "€/L", decimals: 3,
          fuel_type: "Super E10",
          stations: [
            { name: "team Flensburg", company: "team", latitude: 54.78,
              longitude: 9.43, price: 2.229, distance_km: 9.8,
              coord_approx: false },
          ],
        },
      ],
    };
    const plotted = card._answerStations(answer);
    assert.strictEqual(plotted.length, 2, "both countries' stations are plotted");
    const units = plotted.map((s) => s.unit).sort();
    // Compared as JSON, not with deepStrictEqual: `plotted` is built inside the
    // jsdom realm, so its Array has a different prototype and a strict deep
    // compare rejects two arrays whose contents are identical.
    assert.strictEqual(
      JSON.stringify(units),
      JSON.stringify(["kr./L", "€/L"].sort()),
      "each station keeps its own currency"
    );

    // An answer from before `countries` existed must still plot.
    const legacy = card._answerStations({
      fuel_type: "Blyfri 95 (E10)", unit: "kr./L", stations: answer.stations,
    });
    assert.strictEqual(legacy.length, 1, "an answer with no countries still plots");

    // The popup groups by country, prices each in its own decimals, and never
    // claims one side is cheaper than the other.
    const html = card._pickPopupHtml(answer);
    assert.ok(html.includes("Denmark") && html.includes("Germany"),
      "the popup names both countries");
    assert.ok(html.includes("17,59"), "Danish price to two decimals");
    assert.ok(html.includes("2,229"), "German price to three");
    // Each country may name its OWN cheapest — that is the whole grouped
    // answer. What must never appear is a comparison BETWEEN them, which no
    // rate in the integration could honestly support.
    assert.ok(
      !/cheaper|billigere|end i |than in |vs\.?\s/i.test(html),
      "no cross-currency comparison"
    );

    const empty = card._pickPopupHtml({ searched_km: 25, countries: [] });
    assert.ok(/25/.test(empty), "an empty answer says how far it looked");
    console.log("border -> 2 plotted, both currencies, grouped popup");

    // --- a pick puts the stations ON the map, not just in the bubble --------
    // Listing them inside the pin answered "what does it cost there" and not
    // "where is it", which is half of what a map is for.
    card._hass = {
      ...card._hass,
      callService: async () => ({ response: answer }),
    };
    await card._askAt(window.L, 54.84, 9.40);
    const pins = card._pickLayer.getLayers();
    // One pin per station, plus the 📍 itself.
    assert.strictEqual(pins.length, 3, "both stations plotted alongside the pin");
    const prices = pins
      .map((m) => m.options.ffPrice)
      .filter((v) => v != null)
      .sort((a, b) => a - b);
    assert.strictEqual(
      JSON.stringify(prices),
      JSON.stringify([2.229, 17.59]),
      "the plotted pins carry both countries' prices"
    );
    // Cheapest is per currency: across a border there is no single cheapest.
    assert.strictEqual(
      pins.filter((m) => m.options.ffCheap).length,
      2,
      "each currency gets its own cheapest, rather than one winner"
    );

    // The pins live in their own layer, so a price refresh that clears the
    // area pool must not wipe the pick.
    card._markerLayer.clearLayers();
    assert.strictEqual(
      card._pickLayer.getLayers().length,
      3,
      "clearing the area pool leaves the pick alone"
    );

    // A second pick replaces the first rather than piling up.
    await card._askAt(window.L, 54.80, 9.42);
    assert.strictEqual(
      card._pickLayer.getLayers().length,
      3,
      "a second pick replaces the first"
    );

    // The bubble is now a summary, not a repeat of the list.
    const summary = card._pickPopupHtml(answer, 2);
    assert.ok(!summary.includes("OK Kruså"), "the bubble no longer lists stations");
    assert.ok(/2 vist/.test(summary), "it says how many were plotted");
    console.log("pick   -> stations on the map, own layer, summary bubble");
  }

  // 8. A kilogram price must never stand in for a litre price.
  //
  //    CNG is sold by the kilogram. A station's headline price falls back to
  //    the lowest price on it when the card's first fuel is not sold there —
  //    and before units were tracked, that fallback happily picked 1,65 EUR/kg,
  //    printed it on the pin beside real litre prices and crowned it the
  //    cheapest forecourt on the map.
  {
    const PETROL = "sensor.tankpriser_euro_95";
    const CNG = "sensor.tankpriser_cng";
    const petrolState = {
      state: "2.429",
      attributes: {
        friendly_name: "Euro 95 (E10)", fuel_type: "Euro 95 (E10)",
        fuel_key: "blyfri95", unit_of_measurement: "\u20ac/L",
        area: "Home", radius: "10 km",
        stations: [{
          name: "AVIA de Poel", company: "AVIA", postnummer: "7891",
          city: "Klazienaveen", address: "", price: 2.419, updated: "",
          latitude: 52.72, longitude: 7.00, coord_approx: false,
        }],
      },
    };
    const cngState = {
      state: "1.699",
      attributes: {
        friendly_name: "CNG", fuel_type: "CNG",
        fuel_key: "cng", unit_of_measurement: "\u20ac/kg",
        area: "Home", radius: "10 km",
        stations: [
          {
            name: "AVIA de Poel", company: "AVIA", postnummer: "7891",
            city: "Klazienaveen", address: "", price: 1.699, updated: "",
            latitude: 52.72, longitude: 7.00, coord_approx: false,
          },
          {
            // Sells the gas and nothing else — the fallback's whole domain.
            name: "CNG Punt Emmen", company: "CNG Punt", postnummer: "7811",
            city: "Emmen", address: "", price: 1.649, updated: "",
            latitude: 52.78, longitude: 6.90, coord_approx: false,
          },
        ],
      },
    };
    const card = new Card();
    card.setConfig({
      entities: [PETROL, CNG],
      show_map: false,
      coverage: "area",
      show_my_location: false,
    });
    window.document.getElementById("host").appendChild(card);
    card.hass = hass({ [PETROL]: petrolState, [CNG]: cngState });
    const rows = card._areaStations();

    const both = rows.find((r) => r.name === "AVIA de Poel");
    assert.strictEqual(
      both.price, 2.419,
      "a station selling both is headlined by the litre price"
    );

    const gasOnly = rows.find((r) => r.name === "CNG Punt Emmen");
    assert.strictEqual(
      gasOnly.price, null,
      "a station selling only CNG has no litre price to show - 1,649 per kg is not one"
    );

    const gasLine = gasOnly.lines.find((l) => l.label === "CNG");
    assert.strictEqual(gasLine.unit, "\u20ac/kg", "its own line is labelled per kilogram");
    const petrolLine = both.lines.find((l) => l.label === "Euro 95 (E10)");
    assert.strictEqual(petrolLine.unit, "\u20ac/L", "while the petrol line stays per litre");
    console.log("units  -> a kilogram price never stands in for a litre price");
  }

  console.log("\nmap tests passed");
})().catch((err) => {
  console.error("\nFAILED:", err && err.message);
  process.exit(1);
});

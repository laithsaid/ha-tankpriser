// Runs the real card file in a stubbed browser context and checks the new
// helpers: navigate URLs per platform, and the per-device car filter.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const CARD = path.join(
  __dirname, "..", "custom_components", "tankpriser", "www", "tankpriser-card.js"
);
const SRC = fs.readFileSync(CARD, "utf8");

const store = new Map();
const registry = new Map();
const ctx = {
  console,
  setTimeout,
  clearTimeout,
  URL,
  Image: class { set src(_v) {} },
  navigator: { userAgent: "", maxTouchPoints: 0 },
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => {
      if (registry.has(t)) throw new Error(`'${t}' has already been used`);
      registry.set(t, c);
    },
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

// 1. The file executes cleanly, and a *duplicate* load must not throw
//    (extra_module_url + a leftover Lovelace resource can both load it).
//    The second load is wrapped in its own function scope, because that is what
//    a browser gives each ES module: separate top-level scope, shared registry.
vm.runInContext(SRC, ctx, { filename: "tankpriser-card.js" });
assert.ok(registry.has("tankpriser-card"), "card element defined");
assert.doesNotThrow(
  () => vm.runInContext(`(function(){\n${SRC}\n})()`, ctx, { filename: "second-load.js" }),
  "second load must be harmless"
);
assert.strictEqual(
  ctx.window.customCards.filter((c) => c.type === "tankpriser-card").length,
  1,
  "card listed once in the picker after two loads"
);

// 2. Navigate links.
const { _navUrl } = ctx;
const exact = { name: "Q8 Hummeltoftevej 45", city: "Virum", lat: 55.78226, lon: 12.48449, approx: false };
const approx = { name: "F24 Motorvejen nord", city: "Roskilde", lat: 55.65, lon: 12.08, approx: true };

const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15";
const IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15";
const DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126";

function on(ua, touch = 0) {
  ctx.navigator.userAgent = ua;
  ctx.navigator.maxTouchPoints = touch;
}

on(ANDROID);
assert.strictEqual(
  _navUrl(exact),
  "geo:55.78226,12.48449?q=55.78226,12.48449(Q8%20Hummeltoftevej%2045%2C%20Virum)"
);
on(IPHONE);
assert.strictEqual(_navUrl(exact), "https://maps.apple.com/?daddr=55.78226,12.48449&dirflg=d");
on(IPAD, 5); // iPadOS masquerades as a Mac
assert.strictEqual(_navUrl(exact), "https://maps.apple.com/?daddr=55.78226,12.48449&dirflg=d");
on(IPAD, 0); // a real Mac
assert.strictEqual(_navUrl(exact), "https://www.google.com/maps/dir/?api=1&destination=55.78226,12.48449");
on(DESKTOP);
assert.strictEqual(_navUrl(exact), "https://www.google.com/maps/dir/?api=1&destination=55.78226,12.48449");

// An estimated pin must never be offered as a destination, on any platform or
// in any forced mode: a postnummer centre would be navigated to confidently.
for (const ua of [ANDROID, IPHONE, DESKTOP]) {
  on(ua);
  assert.strictEqual(_navUrl(approx), "", `approx must not navigate on ${ua}`);
  for (const mode of ["auto", "geo", "apple", "google", "osm"]) {
    assert.strictEqual(_navUrl(approx, mode), "", `approx + ${mode}`);
  }
}

// Forced modes ignore the platform.
on(DESKTOP);
assert.ok(_navUrl(exact, "geo").startsWith("geo:"));
assert.ok(_navUrl(exact, "apple").startsWith("https://maps.apple.com/"));
assert.ok(_navUrl(exact, "osm").startsWith("https://www.openstreetmap.org/directions"));
assert.ok(_navUrl(exact, "google").startsWith("https://www.google.com/maps/dir/"));

// No coordinates -> no link at all (rather than a broken one).
assert.strictEqual(_navUrl({ name: "x", lat: null, lon: null }), "");
assert.strictEqual(_navUrl({ name: "x", lat: "abc", lon: 5 }), "");

// A name that would break out of an href must come back encoded.
const nasty = _navUrl(
  { name: '"><img src=x onerror=alert(1)>', city: "", lat: 55, lon: 10, approx: false },
  "geo"
);
assert.ok(!nasty.includes('"') && !nasty.includes("<"), nasty);

// 3. Per-device car filter.
const { _hiddenKey, _loadHidden, _saveHidden } = ctx;
const alice = { user: { id: "u-alice" } };
const bob = { user: { id: "u-bob" } };
assert.strictEqual(_hiddenKey(alice), "tankpriser.hidden_cars.u-alice");
assert.strictEqual(_hiddenKey({}), "tankpriser.hidden_cars.anon");
assert.strictEqual(_loadHidden(alice).size, 0);

_saveHidden(alice, new Set(["sensor.passat_days_until_refuel"]));
assert.deepStrictEqual([..._loadHidden(alice)], ["sensor.passat_days_until_refuel"]);
// Two logins on one device must not inherit each other's filter.
assert.strictEqual(_loadHidden(bob).size, 0);

// Garbage in storage must not take the card down.
store.set(_hiddenKey(bob), "{not json");
assert.strictEqual(_loadHidden(bob).size, 0);
store.set(_hiddenKey(bob), '{"a":1}');
assert.strictEqual(_loadHidden(bob).size, 0);

// Storage that throws (private mode) must not throw out of the card.
const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
ctx.window.localStorage = broken;
assert.strictEqual(_loadHidden(alice).size, 0);
assert.doesNotThrow(() => _saveHidden(alice, new Set(["x"])));

// 4. The marker shown for several cars at one spot.
//    Called on the prototype with a stubbed L, so no map is needed.
const proto = registry.get("tankpriser-card").prototype;
const L = { divIcon: (opts) => opts };
const car = (id, pct, pic) => ({
  options: { ffCar: { id, a: { current_level_percent: pct, car_picture: pic } } },
});
const iconFor = (markers) =>
  proto._carClusterIcon.call(
    { _carColor: proto._carColor, _escape: proto._escape },
    L,
    { getAllChildMarkers: () => markers }
  );

const faceCount = (html) => html.split('class="ff-ccar"').length - 1;

let icon = iconFor([car("a", 100), car("b", 0)]);
assert.strictEqual(faceCount(icon.html), 2, "two faces");
assert.ok(!icon.html.includes("ff-cmore"), "no +n for two cars");
// Fuel level still readable at a glance: full is green, empty is red.
assert.ok(icon.html.includes("hsl(120,"), icon.html);
assert.ok(icon.html.includes("hsl(0,"), icon.html);

// More than three: show three faces and a +n.
icon = iconFor(["a", "b", "c", "d", "e"].map((id) => car(id, 50)));
assert.strictEqual(faceCount(icon.html), 3, "capped at three faces");
assert.ok(icon.html.includes(">+2<"), icon.html);

// A car photo is used when it has one, and a URL cannot break out of the img tag.
icon = iconFor([car("a", 50, "/local/passat.png"), car("b", 50)]);
// _safeUrl resolves it against the HA origin, same as a single car marker does.
assert.ok(icon.html.includes('src="http://ha.local:8123/local/passat.png"'), icon.html);
// No photo -> the inlined mdi:car, not a platform-dependent emoji.
assert.ok(icon.html.includes('viewBox="0 0 24 24"'), "falls back to the car icon");
assert.ok(icon.html.includes('fill="currentColor"'), "the icon follows the theme colour");
assert.ok(!/\p{Emoji_Presentation}/u.test(icon.html), "no emoji left in the markup");
icon = iconFor([car("a", 50, 'javascript:alert(1)"><script>'), car("b", 50)]);
assert.ok(!icon.html.includes("javascript:"), icon.html);
assert.ok(!icon.html.includes("<script"), icon.html);

// An unknown fuel level must not produce a broken colour.
icon = iconFor([car("a", null), car("b", undefined)]);
assert.ok(!icon.html.includes("NaN"), icon.html);

console.log("card helper tests passed");

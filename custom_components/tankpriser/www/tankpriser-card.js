/*
 * Tankpriser Lovelace card
 * Plots local fuel stations on a map — each marker shows the chain's icon and
 * the price — with an optional price table below.
 *
 * Config:
 *   type: custom:tankpriser-card
 *   entity: sensor.tankpriser_...        # or entities: [ ... ]
 *   title: "Fuel near me"                # optional
 *   show_map: true                       # optional, default false
 *   map_height: 420                      # optional px, default 420
 *   map_theme: auto                      # optional: auto | light | dark
 *                                        #   auto follows the HA theme
 *   coverage: national                   # optional: national (default) | area
 *                                        #   national = all DK stations, opens
 *                                        #   centred on your location, the map
 *                                        #   viewport is the filter
 *   fuel: blyfri95                       # optional internal fuel key for the
 *                                        #   national map when you have no entity
 *   cluster: true                        # optional, default true — group nearby
 *                                        #   stations; cluster shows lowest price
 *   show_cars: true                      # optional, default true — plot your
 *                                        #   configured cars, ringed by fuel
 *                                        #   level (green full → red empty)
 *   car_picker: true                     # optional, default true — car button to
 *                                        #   hide cars on THIS device only
 *                                        #   (remembered per device + HA user)
 *   navigation: auto                     # optional: auto (default) | geo | apple
 *                                        #   | google | osm | off. "Navigate here"
 *                                        #   in a station popup; auto uses the
 *                                        #   device's own navigator
 *   show_my_location: true               # optional, default true — live GPS dot
 *   follow_me: false                     # optional, default false — start with
 *                                        #   follow-me armed (➤ button toggles it)
 *   show_list: false                     # optional; default: shown only when the
 *                                        #   map is off. Set true to show both.
 *   highlight_cheapest: true             # optional, default true
 *   max_stations: 0                      # optional, 0 = all (list only)
 *
 * Company icons are each chain's official favicon, bundled with the
 * integration and served by Home Assistant — no third-party requests. A
 * colored code badge is shown as a fallback if an icon cannot load. Map tiles
 * need internet from the browser; the price table works offline.
 */

// Leaflet is vendored under the integration's own static path, so the browser
// only ever talks to Home Assistant for the map machinery. A public CDN is kept
// purely as a fallback for installs that predate the vendored copy: relying on
// it as the primary source broke the map whenever the *client* device could not
// reach unpkg (DNS filtering, Private Relay, WAN down while HA is local).
const VENDOR = "/tankpriser/vendor";

// Where "Support the project" points unless a dashboard overrides it. Kept in
// step with DONATE_URL in const.py, which the prediction sensor publishes as an
// attribute — the price card never reads that attribute, so this copy is the
// one it renders.
const DONATE_URL = "https://paypal.me/tankpriser";

// The integration publishes this file both as a frontend extra_module_url and
// as a Lovelace resource. Normally that is one and the same URL, so the browser
// runs it once — but a leftover hand-added resource (a different URL, or an old
// copy under /local/) makes it run twice, and a bare customElements.define()
// then throws "name has already been used". That exception aborts the rest of
// the file, which is how a *second* copy could take out the editor and the
// prediction card. Defining defensively keeps a duplicate load harmless.
function _define(tag, cls) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
}

// Only http(s) links may be rendered. A dashboard config is trusted, but
// "javascript:..." in donate_url would run in Home Assistant's origin, and
// dashboard YAML gets copied between users.
function _safeUrl(url) {
  if (!url) return "";
  try {
    const parsed = new URL(String(url), window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch (e) {
    return "";
  }
}
// -- navigation ------------------------------------------------------------
// Which maps URL a "Navigate here" link should use. Sniffing the platform is
// the only way to get one link that lands in a real navigator everywhere:
//   Android  geo: — the OS shows *its* chooser, so whatever the user installed
//                   (Google Maps, Waze, HERE, Organic Maps) is offered.
//   iOS      Apple Maps. It is always installed, and iOS has no geo: handler
//            at all, so a geo: link there is simply a dead tap.
//   desktop  Google Maps in a new tab.
function _platform() {
  const ua = navigator.userAgent || "";
  // iPadOS reports itself as Macintosh; touch points give it away.
  if (/iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) {
    return "ios";
  }
  return /Android/.test(ua) ? "android" : "desktop";
}

// Number(null) and Number("") are both 0, which would silently place a
// coordinate-less station or car off the coast of Africa. Anything that is not a
// real number has to come back as null and be skipped.
function _coord(value) {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  return isFinite(num) ? num : null;
}

// Destination link for one station. Only ever called for a station with a
// *known* position (see _navHtml): an estimated pin must not be offered as a
// destination, or the navigator would drive you confidently to the wrong place.
function _navUrl(station, mode) {
  const lat = _coord(station.lat);
  const lon = _coord(station.lon);
  if (lat === null || lon === null || station.approx) return "";
  const label = encodeURIComponent([station.name, station.city].filter(Boolean).join(", "));
  const target = !mode || mode === "auto" ? _platform() : mode;

  if (target === "android" || target === "geo") {
    // geo:lat,lon?q=… — the label keeps the pin named in the chosen app.
    return `geo:${lat},${lon}?q=${lat},${lon}(${label})`;
  }
  if (target === "ios" || target === "apple") {
    return `https://maps.apple.com/?daddr=${lat},${lon}&dirflg=d`;
  }
  if (target === "osm") {
    return `https://www.openstreetmap.org/directions?to=${lat},${lon}`;
  }
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
}

// -- per-device car filter -------------------------------------------------
// Which cars *this* device hides. A dashboard config is shared by everyone who
// can see the dashboard, so a "just for me" filter cannot live there. The HA
// user id is part of the key as well, so two accounts on one tablet do not
// inherit each other's choice.
function _hiddenKey(hass) {
  const uid = (hass && hass.user && hass.user.id) || "anon";
  return `tankpriser.hidden_cars.${uid}`;
}

function _loadHidden(hass) {
  try {
    const raw = window.localStorage.getItem(_hiddenKey(hass));
    const list = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(list) ? list : []);
  } catch (e) {
    return new Set(); // private mode / storage disabled: no filter, no crash
  }
}

function _saveHidden(hass, hidden) {
  try {
    window.localStorage.setItem(_hiddenKey(hass), JSON.stringify([...hidden]));
  } catch (e) {
    // Out of quota or storage blocked: the filter still applies to this view,
    // it just will not survive a reload. Nothing worth breaking the card over.
  }
}

// Lets two Tankpriser cards on the same dashboard stay in step.
const CARS_CHANGED = "tankpriser-cars-changed";

// Cars live in their own map pane, above the station pins: a car must never be
// buried under a forecourt marker that happens to share its patch of road.
const CAR_PANE = "tankpriserCars";

// Material Design's "car" (mdi:car), inlined. An emoji was the obvious first
// choice and the wrong one: it renders as a different cartoon on every platform,
// carries its own colours into a marker that is already colour-coded by fuel
// level, and looks nothing like the rest of Home Assistant. This is the same
// icon HA itself would draw, needs no icon font, no network request and no
// <ha-icon> inside a Leaflet marker, and `currentColor` makes it follow the
// theme's text colour.
const CAR_PATH =
  "M5,11L6.5,6.5H17.5L19,11M17.5,16A1.5,1.5 0 0,1 16,14.5A1.5,1.5 0 0,1 17.5," +
  "13A1.5,1.5 0 0,1 19,14.5A1.5,1.5 0 0,1 17.5,16M6.5,16A1.5,1.5 0 0,1 5,14.5A" +
  "1.5,1.5 0 0,1 6.5,13A1.5,1.5 0 0,1 8,14.5A1.5,1.5 0 0,1 6.5,16M18.92,6C18.72," +
  "5.42 18.16,5 17.5,5H6.5C5.84,5 5.28,5.42 5.08,6L3,12V20A1,1 0 0,0 4,21H5A1,1 " +
  "0 0,0 6,20V19H18V20A1,1 0 0,0 19,21H20A1,1 0 0,0 21,20V12L18.92,6Z";

function _carSvg(size) {
  return (
    `<svg class="ff-carsvg" viewBox="0 0 24 24" width="${size}" height="${size}" ` +
    `aria-hidden="true" focusable="false"><path fill="currentColor" d="${CAR_PATH}"/></svg>`
  );
}

const CDN_LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist";
const CDN_CLUSTER = "https://unpkg.com/leaflet.markercluster@1.5.3/dist";

// Subresource Integrity for the CDN copies. Without these, a compromised or
// spoofed unpkg response would execute as script inside Home Assistant's own
// origin, with access to the logged-in session. The digests are of the exact
// pinned versions above, verified byte-identical to the vendored files, so a
// tampered CDN response simply fails to load and the map degrades instead.
const SRI = {
  "leaflet.js":
    "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH",
  "leaflet.css":
    "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H",
  "leaflet.markercluster.js":
    "sha384-eXVCORTRlv4FUUgS/xmOyr66XBVraen8ATNLMESp92FKXLAMiKkerixTiBvXriZr",
  "MarkerCluster.css":
    "sha384-pmjIAcz2bAn0xukfxADbZIb3t8oRT9Sv0rvO+BR5Csr6Dhqq+nZs59P0pPKQJkEV",
  "MarkerCluster.Default.css":
    "sha384-wgw+aLYNQ7dlhK47ZPK7FRACiq7ROZwgFNg0m04avm4CaXS+Z9Y7nMu8yNjBKYC+",
};

// Integrity only applies to the cross-origin CDN copies; the vendored files are
// served by Home Assistant itself over the same origin.
function _sriFor(url) {
  if (!url.startsWith("https://unpkg.com/")) return "";
  return SRI[url.split("/").pop()] || "";
}

const LEAFLET_JS = [`${VENDOR}/leaflet.js`, `${CDN_LEAFLET}/leaflet.js`];
const LEAFLET_CSS = [`${VENDOR}/leaflet.css`, `${CDN_LEAFLET}/leaflet.css`];
const CLUSTER_JS = [
  `${VENDOR}/leaflet.markercluster.js`,
  `${CDN_CLUSTER}/leaflet.markercluster.js`,
];
const CLUSTER_CSS = [
  [`${VENDOR}/MarkerCluster.css`, `${CDN_CLUSTER}/MarkerCluster.css`],
  [`${VENDOR}/MarkerCluster.Default.css`, `${CDN_CLUSTER}/MarkerCluster.Default.css`],
];

// Tile basemaps. "dark" uses CARTO's free dark basemap (no API key). Both
// credit OpenStreetMap; CARTO additionally.
const TILES = {
  light: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap",
    subdomains: "abc",
    maxZoom: 19,
  },
  dark: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: "&copy; OpenStreetMap, &copy; CARTO",
    subdomains: "abcd",
    maxZoom: 20,
  },
};

// Company → brand colour + short code + favicon domain. Matched loosely against
// the station's `company` string. Colour/code are only shown as an icon
// fallback and for tinting clusters.
// Internal fuel key -> display label (used for national-mode popups, which
// carry raw keys rather than the entity's display name).
const FUEL_LABELS = {
  blyfri95: "Blyfri 95 (E10)",
  blyfri98: "Blyfri 98",
  blyfri95plus: "Blyfri 95 Extra (E5)",
  oktan100: "Oktan 100",
  diesel: "Diesel (B7)",
  dieselplus: "Diesel Extra",
  hvo100: "HVO100",
};

// Brand icons are bundled and served by Home Assistant. They used to be
// fetched from a third-party favicon service, which told that service the
// user's IP, when they opened the dashboard and which chains they follow —
// on every single render. Nothing here leaves the local network.
const ICON_BASE = "/tankpriser/vendor/icons/";
const COMPANIES = [
  { test: /oil/i, code: "OIL!", color: "#D81E05", icon: "oiltankstationer.dk.png" },
  { test: /f24/i, code: "F24", color: "#7A1FA2", icon: "f24.dk.png" },
  { test: /q8/i, code: "Q8", color: "#00843D", icon: "q8.dk.png" },
  { test: /shell/i, code: "Shell", color: "#D9A400", icon: "shell.dk.ico" },
  { test: /circle ?k/i, code: "CK", color: "#E4002B", icon: "circlek.dk.ico" },
  { test: /go.?on/i, code: "Go'on", color: "#2E9C48", icon: "goon.nu.png" },
  { test: /uno.?x/i, code: "Uno-X", color: "#111", icon: "uno-x.dk.ico" },
  { test: /ok/i, code: "OK", color: "#E4571B", icon: "ok.dk.ico" },
];
function companyMeta(company) {
  const c = company || "";
  for (const m of COMPANIES) if (m.test.test(c)) return m;
  return { code: c.slice(0, 4) || "?", color: "#607d8b", icon: null };
}

const _iconStatus = {}; // url -> 'ok' | 'fail'
const _iconPromises = {};
function preloadIcon(url) {
  if (!url) return Promise.resolve(false);
  if (_iconStatus[url]) return Promise.resolve(_iconStatus[url] === "ok");
  if (_iconPromises[url]) return _iconPromises[url];
  _iconPromises[url] = new Promise((res) => {
    const img = new Image();
    const done = (ok) => {
      _iconStatus[url] = ok ? "ok" : "fail";
      res(ok);
    };
    const timer = setTimeout(() => done(false), 4000);
    img.onload = () => {
      clearTimeout(timer);
      done(true);
    };
    img.onerror = () => {
      clearTimeout(timer);
      done(false);
    };
    img.src = url;
  });
  return _iconPromises[url];
}

function _loadScript(src) {
  return new Promise((resolve, reject) => {
    let el = document.querySelector(`script[src="${src}"]`);
    if (el) {
      if (el.dataset.loaded) return resolve();
      el.addEventListener("load", () => resolve());
      el.addEventListener("error", () => reject(new Error("load " + src)));
      return;
    }
    el = document.createElement("script");
    const integrity = _sriFor(src);
    if (integrity) {
      el.integrity = integrity;
      el.crossOrigin = "anonymous"; // required for integrity to be enforced
    }
    el.src = src;
    el.onload = () => {
      el.dataset.loaded = "1";
      resolve();
    };
    el.onerror = () => {
      // Drop the failed tag, otherwise the lookup above would later resolve
      // against a <script> that will never load.
      el.remove();
      reject(new Error("load " + src));
    };
    document.head.appendChild(el);
  });
}

// Try each candidate URL in order (local copy first, CDN as a fallback).
async function _loadFirst(sources) {
  let lastErr;
  for (const src of sources) {
    try {
      return await _loadScript(src);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error("no sources");
}

// Only the JS is loaded globally (window.L). The CSS must live INSIDE the card
// because HA renders custom cards in a shadow DOM that document-level styles
// cannot reach (see the <link>s injected in _build).
//
// NOTE: both caches below are cleared when the load fails. Caching a *rejected*
// promise meant one transient network blip poisoned the map for the lifetime of
// the page — which on the mobile companion app can be days, since its WebView
// survives pull-to-refresh.
let _leafletPromise = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (_leafletPromise) return _leafletPromise;
  _leafletPromise = (async () => {
    await _loadFirst(LEAFLET_JS);
    return window.L;
  })().catch((e) => {
    _leafletPromise = null;
    throw e;
  });
  return _leafletPromise;
}

let _clusterPromise = null;
function loadCluster() {
  if (_clusterPromise) return _clusterPromise;
  _clusterPromise = (async () => {
    const L = await loadLeaflet();
    if (L.markerClusterGroup) return L;
    await _loadFirst(CLUSTER_JS);
    return L;
  })().catch((e) => {
    _clusterPromise = null;
    throw e;
  });
  return _clusterPromise;
}

class TankpriserCard extends HTMLElement {
  setConfig(config) {
    const entities = config.entities || (config.entity ? [config.entity] : []);
    if (!entities.length && !config.fuel) {
      throw new Error("Define an 'entity'/'entities' (a Tankpriser sensor) or a 'fuel' key.");
    }
    const showMap = config.show_map === true;
    this._config = {
      title: config.title,
      entities,
      fuel: config.fuel || null,
      highlight_cheapest: config.highlight_cheapest !== false,
      max_stations: config.max_stations || 0,
      show_map: showMap,
      // Coerced to a number: it is interpolated into a style attribute, so a
      // string from YAML could otherwise close the attribute and inject markup.
      map_height: Number(config.map_height) || 420,
      map_theme: ["light", "dark"].includes(config.map_theme) ? config.map_theme : "auto",
      // "national" (default) shows every Danish station; the map viewport is the
      // filter. "area" restricts the map to the sensor's Home-based area.
      coverage: config.coverage === "area" ? "area" : "national",
      cluster: config.cluster !== false,
      // list shows by default only when there is no map to carry the info
      show_list: config.show_list !== undefined ? config.show_list === true : !showMap,
      show_donate: config.show_donate !== false,
      donate_url: _safeUrl(config.donate_url) || DONATE_URL,
      // Show a live "you are here" dot on the map, updated while you move.
      show_my_location: config.show_my_location !== false,
      // Start with follow-me armed. Off by default: it takes control of the map
      // and keeps the GPS in high-accuracy mode, so you ask for it explicitly.
      follow_me: config.follow_me === true,
      // Plot your configured cars on the map, ringed by fuel level (green full →
      // red empty). Optional explicit list; otherwise every car is auto-detected.
      show_cars: config.show_cars !== false,
      cars: Array.isArray(config.cars) ? config.cars : null,
      // The car-button control for hiding cars on this device. The dashboard config
      // still decides which cars exist here (show_cars / cars:); this only
      // narrows that down per device.
      car_picker: config.car_picker !== false,
      // "Navigate here" in the station popup. auto = per platform (see
      // _navUrl); force with geo | apple | google | osm, or "off".
      navigation: ["off", "geo", "apple", "google", "osm"].includes(config.navigation)
        ? config.navigation
        : "auto",
    };
    this._built = false;
    this._map = null;
    this._markerLayer = null;
    this._carLayer = null;
    this._carSig = null;
    this._pickerSig = null;
    this._hidden = null;        // cars hidden on this device (loaded with hass)
    this._fitted = false;
    this._mapSig = null;
    this._national = null;      // national station list (fetched over websocket)
    this._nationalLoading = false;
    this._nationalStale = false;
    // keep the map centred on me while I move (see the ➤ control)
    this._follow = this._config.follow_me && this._config.show_my_location;
  }

  set hass(hass) {
    this._hass = hass;
    // Needs hass: the stored filter is keyed by the logged-in user.
    if (this._hidden === null) this._hidden = _loadHidden(hass);
    this._subscribeUpdates();
    this._update();
  }

  connectedCallback() {
    this._subscribeUpdates();
    if (this._config && this._config.show_map && this._config.show_my_location) {
      this._startWatchingPosition();
    }
    // A second Tankpriser card on the same view shares the filter.
    this._onCarsChanged = (ev) => {
      if (!this._hass || !ev.detail || ev.detail.key !== _hiddenKey(this._hass)) return;
      this._hidden = _loadHidden(this._hass);
      this._refreshCars();
    };
    window.addEventListener(CARS_CHANGED, this._onCarsChanged);
  }

  disconnectedCallback() {
    if (this._onCarsChanged) {
      window.removeEventListener(CARS_CHANGED, this._onCarsChanged);
      this._onCarsChanged = null;
    }
    // Stop the GPS watch as soon as the card leaves the screen; a live watch is
    // the one genuinely battery-hungry thing this card does.
    this._stopWatchingPosition();
    if (this._unsub) {
      this._unsub.then((off) => off()).catch(() => {});
      this._unsub = null;
    }
  }

  _subscribeUpdates() {
    // The station list is refetched when the integration says there is new
    // data — not on a timer of its own. The coordinator fires this after every
    // successful poll, so the card is fresh exactly when the backend is.
    if (this._unsub || !this._hass || !this._hass.connection) return;
    this._unsub = this._hass.connection.subscribeEvents(() => {
      this._nationalStale = true;
      this._ensureNational();
    }, "tankpriser_price_updated");
    this._unsub.catch(() => {
      this._unsub = null;
    });
  }

  getCardSize() {
    return this._config && this._config.show_map ? 8 : 3;
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((e) =>
      e.startsWith("sensor.") && hass.states[e].attributes.stations
    );
    return { entity: entity || "sensor.tankpriser", show_map: true };
  }

  static getConfigElement() {
    return document.createElement("tankpriser-card-editor");
  }

  // -- lifecycle ------------------------------------------------------------
  _update() {
    if (!this._hass || !this._config) return;
    if (!this._built) this._build();

    // Always show the list when there is no map, else honour show_list.
    const showList = this._config.show_map ? this._config.show_list : true;
    this._bodyEl.innerHTML = showList
      ? this._config.entities.map((e) => this._section(e)).filter(Boolean).join("") ||
        this._notice("No Tankpriser sensor found.")
      : "";

    if (this._config.show_map) this._updateMap();
  }

  _build() {
    // Leaflet CSS must be injected inside the card (shadow DOM); a document
    // <head> stylesheet does not reach here and would scatter the map tiles.
    const mapCss = this._config.show_map
      ? [LEAFLET_CSS, CLUSTER_CSS[0], CLUSTER_CSS[1]]
          .map(
            ([local, cdn]) =>
              `<link rel="stylesheet" href="${local}" data-fallback="${cdn}" ` +
              `data-integrity="${_sriFor(cdn)}">`
          )
          .join("\n")
      : "";
    const mapBlock = this._config.show_map
      ? `<div class="ff-map" style="height:${this._config.map_height}px"></div>`
      : "";
    const donate = this._config.show_donate
      ? `<div class="ff-donate">Enjoying this card?
           <a href="${this._escape(this._config.donate_url)}" target="_blank" rel="noopener">Support the project ♥</a>
         </div>`
      : "";

    this.innerHTML = `
      ${mapCss}
      <ha-card ${this._config.title ? `header="${this._escape(this._config.title)}"` : ""}>
        ${mapBlock}
        <div class="ff-body"></div>
        ${donate}
      </ha-card>
      <style>
        .ff-body { padding: 0 16px 8px; }
        .ff-map { width:100%; }
        .leaflet-container { font: inherit; background: var(--card-background-color); }
        /* make popups follow the HA theme (dark-friendly) */
        .leaflet-popup-content-wrapper, .leaflet-popup-tip {
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
        }
        .leaflet-popup-content a { color: var(--primary-color); }
        /* "Navigate here" — a proper tap target, not a link in a wall of text */
        .ff-nav { margin-top: 8px; }
        .ff-nav a {
          display:inline-block; padding:5px 11px; border-radius:14px;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          text-decoration:none; font-weight:600; white-space:nowrap;
        }
        .ff-carhide {
          display:inline-block; margin-top:8px; font-size:0.9em;
          color: var(--secondary-text-color); text-decoration:none;
        }
        /* why there is no navigate button on an estimated pin */
        .ff-nav-est {
          margin-top:8px; font-size:0.9em; font-style:italic;
          color: var(--warning-color, #b8860b);
        }
        .ff-pin-wrap, .ff-cluster-wrap { background: transparent !important; border: 0 !important; }
        /* station marker: company icon + price */
        .ff-pin {
          display:inline-flex; align-items:center; gap:3px;
          transform: translate(-50%, -50%);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
          border: 1.5px solid #1f6feb; border-radius: 11px;
          padding: 1px 5px 1px 2px; font-size: 12px; font-weight: 600;
          font-variant-numeric: tabular-nums; white-space: nowrap;
          box-shadow: 0 1px 3px rgba(0,0,0,.4);
        }
        .ff-pin.cheap { border-color:#2e9c48; box-shadow: 0 0 0 2px rgba(46,156,72,.45), 0 1px 3px rgba(0,0,0,.4); }
        .ff-pin.cheap .ff-mprice { color:#2e9c48; }
        .ff-pin.approx { border-style: dashed; opacity: .9; }
        .ff-mico { width:16px; height:16px; border-radius:3px; display:block; object-fit:contain; }
        .ff-mcode { font-size:9px; font-weight:700; color:#fff; border-radius:3px; padding:0 3px; line-height:16px; }
        /* cluster: the distinct chains it contains + the lowest price */
        .ff-cluster {
          display:inline-flex; align-items:center; gap:2px;
          transform: translate(-50%, -50%);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
          border: 1.5px solid #1f6feb; border-radius: 13px;
          padding: 1px 6px 1px 3px; font-size: 12px; font-weight: 700;
          font-variant-numeric: tabular-nums; white-space: nowrap;
          box-shadow: 0 1px 4px rgba(0,0,0,.45);
        }
        .ff-cluster.cheap { border-color:#2e9c48; box-shadow: 0 0 0 2px rgba(46,156,72,.45), 0 1px 4px rgba(0,0,0,.45); }
        .ff-cluster.cheap .ff-cprice { color:#2e9c48; }
        .ff-cico { width:14px; height:14px; border-radius:3px; object-fit:contain; display:block; }
        .ff-ccode { font-size:8px; font-weight:700; color:#fff; border-radius:3px; padding:0 2px; line-height:14px; }
        .ff-cmore { font-size:9px; color: var(--secondary-text-color); font-weight:600; }
        .ff-cprice { margin-left:3px; }
        .ff-cluster-n { font-size:9px; opacity:.65; margin-left:1px; }
        /* map controls — styled like Leaflet's own zoom buttons */
        .ff-mapctl a {
          display:flex; align-items:center; justify-content:center;
          width:30px; height:30px; font-size:17px; line-height:1;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
          cursor:pointer; text-decoration:none;
        }
        .ff-mapctl a:hover { background: var(--secondary-background-color, #f4f4f4); }
        .ff-mapctl a.busy { opacity:.5; }
        /* car picker: the car button and its drop-down list. The Leaflet bar's own
           frame is dropped, or its border would box in the open panel too. */
        .ff-carctl { background: transparent; box-shadow: none; border: 0; }
        .ff-carctl a.ff-carbtn {
          display:flex; align-items:center; justify-content:center; gap:1px;
          width:auto; min-width:30px; height:30px; padding:0 4px;
          font-size:15px; line-height:1; text-decoration:none;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111); cursor:pointer;
          border-radius:4px; box-shadow: 0 1px 5px rgba(0,0,0,.4);
        }
        .ff-carn { font-size:10px; font-weight:700; }
        .ff-carpanel {
          display:none; margin-top:4px; padding:8px 10px;
          min-width:150px; max-height:220px; overflow:auto;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
          border-radius:6px; box-shadow: 0 2px 8px rgba(0,0,0,.4);
          font-size:13px; text-align:left;
        }
        .ff-carpanel.open { display:block; }
        .ff-carpanel-h { font-weight:700; margin-bottom:6px; }
        .ff-carpanel-n {
          margin-top:6px; padding-top:5px; font-size:11px;
          color: var(--secondary-text-color); border-top:1px solid var(--divider-color);
        }
        .ff-carrow {
          display:flex; align-items:center; gap:7px; padding:3px 0; cursor:pointer;
        }
        .ff-carrow input { margin:0; }
        .ff-carrow-nopos span { color: var(--secondary-text-color); }
        /* follow-me: clearly "armed" when on, since it moves the map for you */
        .ff-mapctl a.ff-follow { font-size:14px; transform:none; }
        .ff-mapctl a.ff-follow.active { background:#1f6feb; color:#fff; }
        .ff-mapctl a.ff-follow.active:hover { background:#1a5fd0; }
        /* live "you are here" dot */
        .ff-me-wrap { background: transparent !important; border: 0 !important; }
        .ff-me {
          width:14px; height:14px; border-radius:50%;
          transform: translate(-50%, -50%);
          background:#1f6feb; border:2px solid #fff;
          box-shadow: 0 0 0 1px rgba(0,0,0,.35);
        }
        /* car marker: fuel-coloured ring around a car glyph, with a % badge */
        .ff-car-wrap { background: transparent !important; border: 0 !important; }
        .ff-car { position: relative; transform: translate(-50%, -50%); width:26px; height:26px; }
        .ff-car-disc {
          position: relative; box-sizing: border-box;
          width:26px; height:26px; border-radius:50%; overflow:hidden;
          background: var(--card-background-color, #fff);
          border:3px solid #888; box-shadow: 0 1px 4px rgba(0,0,0,.45);
          display:flex; align-items:center; justify-content:center;
          color: var(--primary-text-color, #111);
        }
        /* the inlined mdi:car takes its colour from the element around it */
        .ff-carsvg { display:block; }
        /* Absolutely filled + clipped by the disc, so any photo becomes a circle */
        .ff-car-img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
        /* cars sharing a spot: one marker showing each car's face, tapped to
           spiderfy them apart — the same gesture as a station cluster. */
        .ff-ccars {
          display:inline-flex; align-items:center; padding:2px;
          transform: translate(-50%, -50%);
          border-radius:14px;
          background: var(--card-background-color, #fff);
          box-shadow: 0 1px 4px rgba(0,0,0,.45);
        }
        .ff-ccar {
          position:relative; overflow:hidden; box-sizing:border-box;
          width:22px; height:22px; border-radius:50%; margin-left:-7px;
          border:2px solid #888; background: var(--card-background-color, #fff);
          display:flex; align-items:center; justify-content:center;
          color: var(--primary-text-color, #111);
        }
        .ff-ccar:first-child { margin-left:0; }
        .ff-ccars .ff-cmore { margin:0 2px 0 3px; }
        .ff-car-pct {
          position:absolute; bottom:-8px; left:50%; transform:translateX(-50%);
          font-size:11px; font-weight:700; color:#fff; line-height:1;
          padding:1px 5px; border-radius:9px; white-space:nowrap;
          box-shadow: 0 1px 2px rgba(0,0,0,.4);
        }
        .ff-popup-updated { color: var(--secondary-text-color); font-size: 0.9em; }
        .ff-section { margin-bottom: 12px; }
        .ff-head {
          display:flex; justify-content:space-between; align-items:baseline;
          padding: 8px 0 4px; font-weight: 600;
        }
        .ff-sub { color: var(--secondary-text-color); font-size: 0.85em; font-weight: 400; }
        table.ff { width:100%; border-collapse: collapse; font-size: 0.95em; }
        table.ff td { padding: 4px 0; border-top: 1px solid var(--divider-color); }
        table.ff td.price { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
        table.ff tr.cheapest td { font-weight: 700; color: var(--primary-color); }
        .ff-name { color: var(--primary-text-color); }
        .ff-updated { color: var(--secondary-text-color); font-size: 0.8em; }
        .ff-approx { color: var(--warning-color, #b8860b); font-size: 0.8em; }
        /* the loyalty-discount marker: "−20" next to a price you pay less for */
        .ff-disc {
          display:inline-block; margin-right:5px; padding:0 4px; border-radius:7px;
          font-size:0.75em; font-weight:700; vertical-align:middle;
          background: var(--success-color, #2e9c48); color:#fff;
        }
        .ff-popup-disc { color: var(--success-color, #2e9c48); font-size: 0.9em; }
        .ff-notice { padding: 12px 0; color: var(--secondary-text-color); }
        .ff-donate {
          padding: 8px 16px 12px; text-align:center; font-size:0.85em;
          color: var(--secondary-text-color);
        }
        .ff-donate a { color: var(--primary-color); text-decoration: none; }
      </style>
    `;
    this._bodyEl = this.querySelector(".ff-body");
    this._mapEl = this.querySelector(".ff-map");
    // Swap a stylesheet to its CDN copy if the local one cannot be served.
    for (const link of this.querySelectorAll("link[data-fallback]")) {
      link.addEventListener("error", () => {
        const cdn = link.dataset.fallback;
        if (!cdn || link.href === cdn) return;
        // Set integrity before href: a stylesheet fetch starts as soon as href
        // is assigned, so a later integrity attribute would not be enforced.
        const integrity = link.dataset.integrity;
        if (integrity) {
          link.integrity = integrity;
          link.crossOrigin = "anonymous";
        }
        link.href = cdn;
      });
    }
    this._built = true;
  }

  // -- table ----------------------------------------------------------------
  _section(entityId) {
    const st = this._hass.states[entityId];
    if (!st) return this._notice(`Unknown entity: ${entityId}`);
    const a = st.attributes;
    const stations = a.stations || [];
    const unit = a.unit_of_measurement || "";
    const fuel = a.fuel_type || a.friendly_name || entityId;

    let rows = stations;
    if (this._config.max_stations > 0) rows = rows.slice(0, this._config.max_stations);

    const cheapest = a.cheapest_price;
    const body = rows.length
      ? rows
          .map((s) => {
            const isCheap = this._config.highlight_cheapest && s.price === cheapest;
            const approx = s.coord_approx
              ? `<span class="ff-approx" title="Approximate location (postnummer centre)"> ≈</span>`
              : "";
            const cut = s.discount_ore
              ? `<span class="ff-disc" title="Pumpepris ${this._price(s.list_price, unit)} · rabat ${this._escape(s.discount_ore)} øre">−${this._escape(s.discount_ore)}</span>`
              : "";
            return `
              <tr class="${isCheap ? "cheapest" : ""}">
                <td class="ff-name">${this._escape(s.name)}${approx}
                  ${s.updated ? `<div class="ff-updated">${this._escape(s.updated)}</div>` : ""}
                </td>
                <td class="price">${cut}${this._price(s.price, unit)}</td>
              </tr>`;
          })
          .join("")
      : `<tr><td colspan="2" class="ff-notice">No prices available.</td></tr>`;

    return `
      <div class="ff-section">
        <div class="ff-head">
          <span>${this._escape(fuel)}</span>
          <span class="ff-sub">${this._escape(a.area || a.postnummer || "")} · ${this._escape(a.radius || "")} · ${a.station_count || rows.length} st.</span>
        </div>
        <table class="ff"><tbody>${body}</tbody></table>
      </div>`;
  }

  // -- map data -------------------------------------------------------------
  // Both sources produce the same shape:
  //   { name, company, city, lat, lon, approx, price, lines:[{label,price}] }
  // where `price` is the headline (primary fuel) shown on the marker.

  _areaStations() {
    const byKey = new Map();
    let primaryFuel = null;
    for (const entityId of this._config.entities) {
      const st = this._hass.states[entityId];
      if (!st) continue;
      const fuel = st.attributes.fuel_type || entityId;
      if (primaryFuel === null) primaryFuel = fuel;
      for (const s of st.attributes.stations || []) {
        if (s.latitude == null || s.longitude == null) continue;
        const k = `${s.name}|${s.postnummer}`;
        let rec = byKey.get(k);
        if (!rec) {
          rec = {
            name: s.name, company: s.company, city: s.city,
            lat: s.latitude, lon: s.longitude, approx: !!s.coord_approx,
            updated: s.updated || null, pf: {}, listPf: {},
            discount: s.discount_ore || null,
          };
          byKey.set(k, rec);
        }
        if (!rec.updated && s.updated) rec.updated = s.updated;
        if (s.price != null) rec.pf[fuel] = s.price;
        if (s.list_price != null) rec.listPf[fuel] = s.list_price;
      }
    }
    return [...byKey.values()].map((r) => {
      const vals = Object.values(r.pf);
      const price =
        r.pf[primaryFuel] != null ? r.pf[primaryFuel] : vals.length ? Math.min(...vals) : null;
      const lines = Object.entries(r.pf).map(([label, p]) => ({ label, price: p }));
      return {
        name: r.name, company: r.company, city: r.city,
        lat: r.lat, lon: r.lon, approx: r.approx,
        updated: r.updated, price, lines,
        discount: r.discount,
        listPrice: r.listPf[primaryFuel] != null ? r.listPf[primaryFuel] : null,
      };
    });
  }

  _nationalStations() {
    if (!this._national) return [];
    const st0 = this._config.entities.length
      ? this._hass.states[this._config.entities[0]]
      : null;
    const key = this._config.fuel || (st0 && st0.attributes.fuel_key);
    if (!key) return []; // no fuel selected / sensor predates fuel_key
    const list = [];
    for (const s of this._national) {
      const price = s.prices ? s.prices[key] : null;
      if (price == null) continue; // only stations selling the selected fuel
      const lines = Object.entries(s.prices || {}).map(([k, p]) => ({
        label: FUEL_LABELS[k] || k,
        price: p,
      }));
      list.push({
        name: s.name, company: s.company, city: s.city,
        lat: s.latitude, lon: s.longitude, approx: !!s.coord_approx,
        updated: s.updated || null, price, lines,
        discount: s.discount_ore || null,
        listPrice: (s.list_prices && s.list_prices[key]) || null,
      });
    }
    return list;
  }

  _ensureNational() {
    // Fetched once when the card appears, then only when the backend reports
    // new prices (see _subscribeUpdates). There is deliberately no polling
    // interval here: a second one would only ever re-read the coordinator's
    // cache and hand back identical data.
    if (this._nationalLoading) return;
    if (this._national && !this._nationalStale) return;
    const conn = this._hass && this._hass.connection;
    if (!conn) return;
    this._nationalLoading = true;
    this._nationalStale = false;
    conn
      .sendMessagePromise({ type: "tankpriser/stations" })
      .then((res) => {
        this._national = (res && res.stations) || [];
        this._nationalLoading = false;
        this._mapSig = null; // force a rebuild with the fresh data
        this._update();
      })
      .catch((err) => {
        this._nationalLoading = false;
        console.warn("tankpriser: national stations fetch failed", err);
        // Retry on a timer only after a failure, so a backend hiccup during
        // startup does not leave an empty map until the next price update.
        if (!this._retryTimer) {
          this._retryTimer = setTimeout(() => {
            this._retryTimer = null;
            this._nationalStale = true;
            this._update();
          }, 60000);
        }
      });
  }

  _iconUrl(company) {
    const meta = companyMeta(company);
    return meta.icon ? `${ICON_BASE}${meta.icon}` : null;
  }

  _effectiveTheme() {
    if (this._config.map_theme === "dark" || this._config.map_theme === "light") {
      return this._config.map_theme;
    }
    // auto: follow Home Assistant's dark mode
    return this._hass && this._hass.themes && this._hass.themes.darkMode
      ? "dark"
      : "light";
  }

  _applyTiles(L) {
    const theme = this._effectiveTheme();
    if (theme === this._tileTheme) return;
    this._tileTheme = theme;
    if (this._tileLayer) this._map.removeLayer(this._tileLayer);
    const t = TILES[theme] || TILES.light;
    this._tileLayer = L.tileLayer(t.url, {
      attribution: t.attribution,
      subdomains: t.subdomains,
      maxZoom: t.maxZoom,
    });
    this._tileLayer.addTo(this._map);
  }

  async _updateMap() {
    if (!this._mapEl) return;
    let L;
    try {
      // The cluster plugin is also what groups two cars parked in the same
      // place, so it is needed even when station clustering is switched off.
      const needCluster = this._config.cluster || this._config.show_cars;
      L = needCluster ? await loadCluster() : await loadLeaflet();
    } catch (e) {
      // Not permanent: the loader forgets the failure, so the next hass update
      // retries. Leave a notice in the meantime.
      this._mapEl.innerHTML = `<div class="ff-notice">Kortet kunne ikke indlæses. Prøver igen…</div>`;
      this._mapFailed = true;
      return;
    }
    if (this._mapFailed) {
      this._mapEl.innerHTML = ""; // clear the notice before Leaflet takes over
      this._mapFailed = false;
    }

    let stations;
    if (this._config.coverage === "national") {
      this._ensureNational();
      stations = this._nationalStations();
    } else {
      stations = this._areaStations();
    }

    // Preload the distinct company icons so markers render with the icon on
    // first paint (cached afterwards; a failed icon falls back to a code badge).
    const urls = [...new Set(stations.map((s) => this._iconUrl(s.company)).filter(Boolean))];
    await Promise.all(urls.map(preloadIcon));

    const priceVals = stations.map((s) => s.price).filter((p) => p != null);
    const globalMin = priceVals.length ? Math.min(...priceVals) : null;

    if (!this._map) {
      this._map = L.map(this._mapEl, { attributionControl: true });
      this._markerLayer =
        this._config.cluster && L.markerClusterGroup
          ? L.markerClusterGroup({
              showCoverageOnHover: false,
              maxClusterRadius: 48,
              spiderfyOnMaxZoom: true,
              iconCreateFunction: (cluster) => this._clusterIcon(L, cluster),
            })
          : L.layerGroup();
      this._map.addLayer(this._markerLayer);
      this._map.on("dragstart", () => {
        this._userMoved = true;
        this._setFollow(false); // panning by hand means "stop chasing me"
      });
      // Both ◎ and ➤ are about *your* position, so show_my_location: false
      // takes the pair away with the dot. Adding the bar anyway left a button
      // labelled "centre on my position" that still asked the browser for a
      // fix — a permission prompt from the one option set to prevent exactly
      // that.
      if (this._config.show_my_location) {
        this._addMapControls(L);
        this._startWatchingPosition();
        // A fix may already have arrived before the map existed.
        if (this._pos) this._drawMe(this._pos, this._posAccuracy);
      }
      this._addCarControl(L);
      setTimeout(() => this._map && this._map.invalidateSize(), 200);
    }

    // Apply (or switch) the basemap. In "auto" this follows HA's dark mode, so
    // toggling the HA theme flips the map too, on the next refresh.
    this._applyTiles(L);

    // Cars move and refuel independently of station prices, so update them
    // every time — before the station-signature early-return below, or a car
    // that populates after prices settle would never get drawn.
    this._updateCars(L);

    // Only rebuild markers when the data changed, so we never disturb zoom/pan.
    const sig = stations
      .map((s) => `${s.name}|${s.lat}|${s.lon}|${s.price}|${s.updated || ""}`)
      .join(";");
    if (sig === this._mapSig) return;
    this._mapSig = sig;

    this._markerLayer.clearLayers();
    const points = [];
    for (const s of stations) {
      const mp = s.price;
      const cheap = mp != null && globalMin != null && mp === globalMin;
      const meta = companyMeta(s.company);
      const iconUrl = this._iconUrl(s.company);
      const haveIcon = iconUrl && _iconStatus[iconUrl] === "ok";
      const iconHtml = haveIcon
        ? `<img class="ff-mico" src="${iconUrl}" alt="">`
        : `<span class="ff-mcode" style="background:${meta.color}">${this._escape(meta.code)}</span>`;
      const label = mp != null ? this._price(mp) : "–";
      const icon = L.divIcon({
        className: "ff-pin-wrap",
        html: `<div class="ff-pin${cheap ? " cheap" : ""}${s.approx ? " approx" : ""}">${iconHtml}<span class="ff-mprice">${label}</span></div>`,
        iconSize: null,
      });
      const marker = L.marker([s.lat, s.lon], { icon });
      marker.options.ffPrice = mp;
      marker.options.ffCheap = cheap;
      marker.options.ffCompany = s.company;

      const priceLines = s.lines
        .map((p) => `${this._escape(p.label)}: <b>${this._price(p.price)}</b>`)
        .join("<br>");
      // `updated` is the chain's own "prices valid from" stamp, not the time we
      // polled — that is what actually tells you how stale a price is.
      const updated = s.updated
        ? `<div class="ff-popup-updated">Priser opdateret: ${this._escape(s.updated)}</div>`
        : "";
      // With a loyalty discount configured, the price shown is what you pay —
      // so show the pump price too, or the card and the forecourt sign
      // disagree and you have no way to tell which is wrong.
      const discount =
        s.discount && s.listPrice != null
          ? `<div class="ff-popup-disc">Pumpepris ${this._price(s.listPrice)} ·
             din rabat ${this._escape(s.discount)} øre</div>`
          : "";
      marker.bindPopup(
        `<b>${this._escape(s.name)}</b>${s.city ? "<br>" + this._escape(s.city) : ""}` +
          `<br>${priceLines}` +
          updated +
          discount +
          // _navHtml carries the "estimated position" notice for approximate
          // pins, so there is no separate line for it here.
          this._navHtml(s)
      );
      this._markerLayer.addLayer(marker);
      points.push([s.lat, s.lon]);
    }

    // Set the initial view once; afterwards the user's zoom/pan is preserved.
    if (!this._fitted) {
      if (this._config.coverage === "national") {
        this._initialView(); // centre on the user's location, not all of DK
      } else if (points.length) {
        this._map.fitBounds(points, { padding: [28, 28], maxZoom: 13 });
        this._fitted = true;
      } else {
        this._map.setView([56.0, 10.5], 6); // Denmark, until data arrives
      }
    }
  }

  // -- cars on the map -------------------------------------------------------
  // Every Tankpriser car sensor this dashboard may show. An explicit `cars:`
  // list wins; otherwise every car is picked up. Cars *without* a position are
  // included here (flagged) on purpose: the picker has to list them, or a car
  // hidden before its first GPS fix could never be brought back.
  _allCars() {
    if (!this._hass) return [];
    const states = this._hass.states;
    const ids = this._config.cars
      ? this._config.cars
      : Object.keys(states).filter(
          (id) => states[id] && states[id].attributes && states[id].attributes.is_car
        );
    const cars = [];
    for (const id of ids) {
      const st = states[id];
      if (!st) continue;
      const a = st.attributes || {};
      const lat = _coord(a.latitude);
      const lon = _coord(a.longitude);
      cars.push({
        id,
        state: st.state,
        a,
        lat,
        lon,
        positioned: lat !== null && lon !== null,
        name: a.car_name || a.friendly_name || id,
        hidden: !!(this._hidden && this._hidden.has(id)),
      });
    }
    cars.sort((x, y) => String(x.name).localeCompare(String(y.name)));
    return cars;
  }

  // What actually gets drawn: positioned, and not hidden on this device.
  _visibleCars(cars) {
    return (cars || this._allCars()).filter((c) => c.positioned && !c.hidden);
  }

  // Hide/show one car for this device only, and remember it.
  _setCarHidden(id, hidden) {
    if (!this._hidden) this._hidden = new Set();
    if (hidden) this._hidden.add(id);
    else this._hidden.delete(id);
    _saveHidden(this._hass, this._hidden);
    this._refreshCars();
    window.dispatchEvent(
      new CustomEvent(CARS_CHANGED, { detail: { key: _hiddenKey(this._hass) } })
    );
  }

  // Redraw the car markers and the picker after the filter changed.
  _refreshCars() {
    if (this._map && window.L) this._updateCars(window.L);
    else this._renderCarPicker();
  }

  // Fuel level → ring colour: 100% green, ~50% orange, 0% red.
  _carColor(pct) {
    if (pct === null || pct === undefined || isNaN(pct)) return "var(--disabled-text-color, #888)";
    const p = Math.max(0, Math.min(100, Number(pct)));
    const hue = (p / 100) * 120; // 0 = red, 120 = green
    return `hsl(${hue.toFixed(0)}, 75%, 45%)`;
  }

  _updateCars(L) {
    if (!this._map) return;
    if (!this._carLayer) {
      // Cars are grouped exactly like stations: when two share a spot you get
      // one marker showing both faces, and a tap spiderfies them apart. That
      // keeps every car on its *true* position — nudging a marker aside to make
      // room only moves the problem onto whatever it lands on.
      this._map.createPane(CAR_PANE).style.zIndex = 640; // above station pins
      this._carLayer = (
        L.markerClusterGroup
          ? L.markerClusterGroup({
              clusterPane: CAR_PANE,
              // Tighter than the stations' 48: cars should separate as soon as
              // they are genuinely in different places.
              maxClusterRadius: 26,
              showCoverageOnHover: false,
              // Two cars at the same zone centre have no bounds to zoom to, so
              // zooming would just re-cluster them. One tap always opens.
              zoomToBoundsOnClick: false,
              spiderfyOnEveryZoom: true,
              spiderfyOnMaxZoom: true,
              // Room for the 26 px discs plus their % badge.
              spiderfyDistanceMultiplier: 1.8,
              spiderLegPolylineOptions: { weight: 2, color: "#888", opacity: 0.8 },
              iconCreateFunction: (cluster) => this._carClusterIcon(L, cluster),
            })
          : L.layerGroup()
      ).addTo(this._map);
    }
    if (!this._config.show_cars) {
      this._carLayer.clearLayers();
      return;
    }

    const all = this._allCars();
    this._renderCarPicker(all);
    const cars = this._visibleCars(all);
    // Only rebuild when something changed, so we don't fight the user's pan.
    // Hidden cars simply drop out of this list, so the filter is part of it.
    const sig = cars
      .map((c) => `${c.id}|${c.lat.toFixed(5)}|${c.lon.toFixed(5)}|${c.a.current_level_percent}|${c.state}|${c.a.car_picture || ""}`)
      .join(";");
    if (sig === this._carSig) return;
    this._carSig = sig;

    this._carLayer.clearLayers();
    for (const car of cars) this._addCarMarker(L, car);
  }

  _addCarMarker(L, c) {
    const pct = c.a.current_level_percent;
    const color = this._carColor(pct);
    const pctLabel = pct === null || pct === undefined ? "?" : `${Math.round(pct)}%`;
    // Use the car's own picture if it has one, else a generic car glyph.
    const pic = _safeUrl(c.a.car_picture);
    const inner = pic
      ? `<img class="ff-car-img" src="${this._escape(pic)}" alt="" referrerpolicy="no-referrer">`
      : _carSvg(16);
    const icon = L.divIcon({
      className: "ff-car-wrap",
      html: `<div class="ff-car">
               <div class="ff-car-disc" style="border-color:${color}">${inner}</div>
               <span class="ff-car-pct" style="background:${color}">${this._escape(pctLabel)}</span>
             </div>`,
      iconSize: null,
    });
    const marker = L.marker([c.lat, c.lon], {
      icon,
      zIndexOffset: 1000,
      pane: CAR_PANE,
    });
    marker.options.ffCar = c; // the cluster icon reads the cars it holds

    const name = c.a.car_name || "Car";
    const hasDays = c.state !== "unknown" && c.state !== "unavailable";
    const days = !hasDays
      ? "Still learning your consumption"
      : c.a.status === "estimating"
        ? `~${this._escape(c.state)} days until refuel (early estimate)`
        : `${this._escape(c.state)} days until refuel`;
    const litres =
      c.a.current_level_l != null
        ? ` (${this._escape(c.a.current_level_l)} L)`
        : "";
    const cheapest = c.a.cheapest_station
      ? `<br>Cheapest ${this._escape((c.a.fuel_type || "").toLowerCase())}: ${this._escape(
          c.a.cheapest_station
        )}${c.a.cheapest_price != null ? ` · ${this._escape(c.a.cheapest_price)}` : ""}`
      : "";
    // Built as an element rather than a string so the "hide" action can carry
    // a real listener instead of an inline onclick.
    const popup = document.createElement("div");
    popup.innerHTML =
      `<b>${this._escape(name)}</b><br>Fuel: <b>${this._escape(pctLabel)}</b>${litres}<br>${days}${cheapest}`;
    if (this._config.car_picker) {
      const hide = document.createElement("a");
      hide.href = "#";
      hide.className = "ff-carhide";
      hide.textContent = "Skjul denne bil her";
      hide.title = "Kun på denne enhed — hentes frem igen med bil-knappen";
      hide.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (this._map) this._map.closePopup();
        this._setCarHidden(c.id, true);
      });
      popup.appendChild(hide);
    }
    marker.bindPopup(popup);
    this._carLayer.addLayer(marker);
  }

  // The marker shown in place of several cars at one spot: each car's face,
  // ringed by its fuel level, in a single pill. Tapping it spiderfies.
  _carClusterIcon(L, cluster) {
    const cars = cluster
      .getAllChildMarkers()
      .map((marker) => marker.options.ffCar)
      .filter(Boolean);
    const MAX = 3; // beyond that the faces get too small to tell apart
    const faces = cars
      .slice(0, MAX)
      .map((c) => {
        const color = this._carColor(c.a.current_level_percent);
        const pic = _safeUrl(c.a.car_picture);
        const inner = pic
          ? `<img class="ff-car-img" src="${this._escape(pic)}" alt="" referrerpolicy="no-referrer">`
          : _carSvg(13);
        return `<span class="ff-ccar" style="border-color:${color}">${inner}</span>`;
      })
      .join("");
    const extra = cars.length - Math.min(cars.length, MAX);
    return L.divIcon({
      className: "ff-car-wrap",
      html:
        `<div class="ff-ccars">${faces}` +
        `${extra > 0 ? `<span class="ff-cmore">+${extra}</span>` : ""}</div>`,
      iconSize: null,
    });
  }

  // -- car picker (car button) ------------------------------------------------
  // Tapping a car's "hide" needs a way back, and a silent filter is worse than
  // no filter: the button shows "visible/total" whenever something is hidden.
  _addCarControl(L) {
    if (!this._config.show_cars || !this._config.car_picker) return;
    const card = this;
    const Control = L.Control.extend({
      options: { position: "topright" }, // topleft is taken by ◎ / ➤ and zoom
      onAdd() {
        const wrap = L.DomUtil.create("div", "leaflet-bar ff-carctl");
        const button = L.DomUtil.create("a", "ff-carbtn", wrap);
        button.href = "#";
        button.setAttribute("role", "button");
        const panel = L.DomUtil.create("div", "ff-carpanel", wrap);
        card._carBtnEl = button;
        card._carPanelEl = panel;
        L.DomEvent.on(button, "click", (ev) => {
          L.DomEvent.stop(ev);
          panel.classList.toggle("open");
          button.setAttribute("aria-expanded", panel.classList.contains("open"));
        });
        // Without these, a tap inside the panel pans or zooms the map.
        L.DomEvent.disableClickPropagation(wrap);
        L.DomEvent.disableScrollPropagation(wrap);
        card._pickerSig = null;
        card._renderCarPicker();
        return wrap;
      },
    });
    this._carControl = new Control();
    this._map.addControl(this._carControl);
  }

  _renderCarPicker(cars) {
    const panel = this._carPanelEl;
    const button = this._carBtnEl;
    if (!panel || !button) return;
    const list = cars || this._allCars();

    // Rebuilding on every state change would fight the checkboxes, so only
    // when the list, the filter or a car's position actually changed.
    const sig = list
      .map((c) => `${c.id}|${c.hidden ? "h" : "v"}|${c.positioned ? "p" : "-"}|${c.name}`)
      .join(";");
    if (sig === this._pickerSig) return;
    this._pickerSig = sig;

    // One car (or none) is nothing to choose between — stay out of the way.
    if (list.length < 2) {
      button.style.display = "none";
      panel.classList.remove("open");
      panel.innerHTML = "";
      return;
    }
    button.style.display = "";

    const shown = list.filter((c) => !c.hidden).length;
    button.innerHTML =
      shown === list.length
        ? _carSvg(18)
        : `${_carSvg(18)}<span class="ff-carn">${shown}/${list.length}</span>`;
    const label = `Vælg biler (${shown} af ${list.length} vises)`;
    button.title = label;
    button.setAttribute("aria-label", label);

    panel.innerHTML = "";
    const head = document.createElement("div");
    head.className = "ff-carpanel-h";
    head.textContent = "Vis biler her";
    panel.appendChild(head);

    for (const car of list) {
      const row = document.createElement("label");
      row.className = "ff-carrow";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !car.hidden;
      box.addEventListener("change", () => this._setCarHidden(car.id, !box.checked));
      const text = document.createElement("span");
      text.textContent = car.positioned ? car.name : `${car.name} (ingen position)`;
      if (!car.positioned) row.classList.add("ff-carrow-nopos");
      row.appendChild(box);
      row.appendChild(text);
      panel.appendChild(row);
    }

    const note = document.createElement("div");
    note.className = "ff-carpanel-n";
    note.textContent = "Gælder kun denne enhed";
    panel.appendChild(note);
  }

  // "Navigate here" for a station popup, aimed at whatever this device runs —
  // or an honest explanation of why there is no such button.
  _navHtml(station) {
    // No exact position: the pin is the middle of a postnummer, or an address
    // DAWA could only match by correcting the chain's spelling. Sending that to
    // a navigator would look authoritative and be wrong, so say what we have
    // instead. The price is still the point of the popup.
    if (station.approx) {
      return (
        `<div class="ff-nav-est">≈ Placeringen er kun anslået, så der kan ikke ` +
        `navigeres præcist hertil.</div>`
      );
    }
    if (this._config.navigation === "off") return "";
    const url = _navUrl(station, this._config.navigation);
    if (!url) return "";
    // Not routed through _safeUrl: that only permits http(s) and would reject
    // the geo: scheme. The URL is built here from coordinates and an
    // encodeURIComponent'd name, never from config, so there is nothing to
    // smuggle a javascript: into.
    return (
      `<div class="ff-nav"><a href="${this._escape(url)}" target="_blank" ` +
      `rel="noopener">➤ Navigér hertil</a></div>`
    );
  }

  _addMapControls(L) {
    // Only called when show_my_location is on — both buttons are about your
    // position, so neither exists without it.
    //
    // Two separate buttons, because they are two different intentions:
    //   ◎  one-shot "where am I" — recentre now and leave the map alone after.
    //   ➤  follow-me toggle — keep recentring on every new fix. Off by default;
    //      it is the only mode that moves the map under your finger, so it is
    //      opt-in rather than something a stray tap on ◎ switches on.
    const card = this;
    const Controls = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const wrap = L.DomUtil.create("div", "leaflet-bar ff-mapctl");

        const recenter = L.DomUtil.create("a", "ff-recenter", wrap);
        recenter.href = "#";
        recenter.title = "Centrér på min position";
        recenter.setAttribute("role", "button");
        recenter.setAttribute("aria-label", "Centrér på min position");
        recenter.innerHTML = "◎";
        L.DomEvent.on(recenter, "click", (ev) => {
          L.DomEvent.stop(ev);
          card._recenter();
        });
        card._recenterEl = recenter;

        const follow = L.DomUtil.create("a", "ff-follow", wrap);
        follow.href = "#";
        follow.setAttribute("role", "button");
        follow.innerHTML = "➤";
        L.DomEvent.on(follow, "click", (ev) => {
          L.DomEvent.stop(ev);
          card._setFollow(!card._follow);
          if (card._follow) card._recenter();
        });
        card._followEl = follow;

        L.DomEvent.disableClickPropagation(wrap);
        card._syncFollowButton();
        return wrap;
      },
    });
    this._mapControls = new Controls();
    this._map.addControl(this._mapControls);
  }

  _recenter() {
    if (!this._map) return;
    // Recentring is a deliberate "put me back in view", so it clears the sticky
    // user-moved flag; it does NOT change follow mode either way.
    this._userMoved = false;
    const home = this._homeView();
    const el = this._recenterEl;

    // Already tracking? Jump straight there, no round trip.
    if (this._pos) {
      this._map.setView(this._pos, Math.max(this._map.getZoom(), 13));
      return;
    }

    if (!navigator.geolocation) {
      if (home) this._map.setView(home, Math.max(this._map.getZoom(), 12));
      return;
    }
    if (el) el.classList.add("busy");
    const done = () => el && el.classList.remove("busy");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        done();
        this._onPosition(pos);
      },
      () => {
        // Denied, or an insecure context (plain http over the LAN): the HA
        // home location is the best remaining answer. Follow mode cannot work
        // without fixes either, so drop it.
        done();
        this._setFollow(false);
        if (this._map && home) {
          this._map.setView(home, Math.max(this._map.getZoom(), 11));
        }
      },
      { timeout: 8000, maximumAge: 0, enableHighAccuracy: true }
    );
  }

  _setFollow(on) {
    if (this._follow === !!on) return;
    this._follow = !!on;
    this._syncFollowButton();
    this._restartWatchIfNeeded();
  }

  _syncFollowButton() {
    const el = this._followEl;
    if (!el) return;
    el.classList.toggle("active", this._follow);
    const label = this._follow ? "Følg mig: til" : "Følg mig: fra";
    el.title = label;
    el.setAttribute("aria-label", label);
    el.setAttribute("aria-pressed", this._follow ? "true" : "false");
  }

  // -- live position --------------------------------------------------------
  _startWatchingPosition() {
    // Continuous GPS: the dot moves as you do, and while "follow" is on the map
    // pans with it — the in-the-car case. Requires a secure context, so over
    // plain http on the LAN this silently does nothing and the dot never shows.
    //
    // The OS decides how often this fires (roughly per second while moving with
    // high accuracy). `maximumAge` is not a polling interval: it only says how
    // stale a *cached* fix may be when one is handed straight back, which in
    // practice affects the first callback.
    if (this._watchId != null || !navigator.geolocation) return;
    // High accuracy (i.e. the real GPS radio) only while actually following;
    // an idle dot does not need to keep the receiver warm.
    const highAccuracy = this._follow;
    this._watchAccuracy = highAccuracy;
    this._watchId = navigator.geolocation.watchPosition(
      (pos) => this._onPosition(pos),
      () => {},
      {
        enableHighAccuracy: highAccuracy,
        maximumAge: highAccuracy ? 2000 : 30000,
        timeout: 20000,
      }
    );
  }

  _restartWatchIfNeeded() {
    // Follow mode changes how aggressively we want fixes; the options are fixed
    // at watch creation, so swap the watch out when the mode changes.
    if (this._watchId == null || this._watchAccuracy === this._follow) return;
    this._stopWatchingPosition();
    this._startWatchingPosition();
  }

  _stopWatchingPosition() {
    if (this._watchId != null && navigator.geolocation) {
      navigator.geolocation.clearWatch(this._watchId);
    }
    this._watchId = null;
  }

  _onPosition(pos) {
    const here = [pos.coords.latitude, pos.coords.longitude];
    this._pos = here;
    this._posAccuracy = pos.coords.accuracy;
    this._drawMe(here, pos.coords.accuracy);
    if (!this._map) return;
    if (this._follow) {
      this._map.setView(here, Math.max(this._map.getZoom(), 13), { animate: true });
    } else if (!this._userMoved && !this._centredOnMe) {
      // First fix after opening: swap the Home-based view for the real one.
      this._centredOnMe = true;
      this._map.setView(here, 12);
    }
  }

  _drawMe(latlng, accuracy) {
    const L = window.L;
    if (!this._map || !L || !this._config.show_my_location) return;
    if (!this._meMarker) {
      // An accuracy halo plus a dot, drawn above the station markers.
      this._meAccuracy = L.circle(latlng, {
        radius: accuracy || 0,
        color: "#1f6feb", weight: 1, opacity: 0.4,
        fillColor: "#1f6feb", fillOpacity: 0.12,
        interactive: false,
      }).addTo(this._map);
      this._meMarker = L.marker(latlng, {
        icon: L.divIcon({ className: "ff-me-wrap", html: `<div class="ff-me"></div>`, iconSize: null }),
        zIndexOffset: 1000,
        interactive: false,
        keyboard: false,
      }).addTo(this._map);
    } else {
      this._meMarker.setLatLng(latlng);
      this._meAccuracy.setLatLng(latlng);
      this._meAccuracy.setRadius(accuracy || 0);
    }
  }

  _homeView() {
    const cfg = this._hass && this._hass.config;
    if (cfg && cfg.latitude != null && cfg.longitude != null) {
      return [cfg.latitude, cfg.longitude];
    }
    return null;
  }

  _initialView() {
    // National map opens centred on the user's location (their local area),
    // not fitted to the whole country.
    const home = this._homeView();
    if (home) {
      this._map.setView(home, 11);
    } else {
      this._map.setView([56.0, 10.5], 7);
    }
    this._fitted = true;
    // With the live dot on, the position watch does the initial recentring
    // (see _onPosition); a separate one-shot lookup would be a second GPS ask.
    if (!this._config.show_my_location) this._tryGeolocate();
  }

  _tryGeolocate() {
    // Upgrade to the device's real GPS if the browser allows it (needs a secure
    // context — over plain http on a LAN this is unavailable and simply skips).
    if (this._geoTried || !navigator.geolocation) return;
    this._geoTried = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (this._map && !this._userMoved) {
          this._map.setView([pos.coords.latitude, pos.coords.longitude], 12);
        }
      },
      () => {},
      { timeout: 6000, maximumAge: 600000 }
    );
  }

  _clusterIcon(L, cluster) {
    // Cluster shows the DISTINCT chains it groups (cheapest first, capped) plus
    // the LOWEST price. Green outline if it holds the overall-cheapest station.
    let min = null;
    let cheapCompany = null;
    let globalCheap = false;
    const seen = new Set();
    const companies = [];
    for (const m of cluster.getAllChildMarkers()) {
      const p = m.options.ffPrice;
      if (p != null && (min === null || p < min)) {
        min = p;
        cheapCompany = m.options.ffCompany;
      }
      if (m.options.ffCheap) globalCheap = true;
      const meta = companyMeta(m.options.ffCompany);
      if (!seen.has(meta.code)) {
        seen.add(meta.code);
        companies.push(meta);
      }
    }

    // Put the cheapest chain first, then cap how many icons we show.
    const cheapCode = companyMeta(cheapCompany).code;
    companies.sort((a, b) =>
      a.code === cheapCode ? -1 : b.code === cheapCode ? 1 : 0
    );
    const MAX = 4;
    const shown = companies.slice(0, MAX);
    const extra = companies.length - shown.length;
    const icons = shown
      .map((meta) => {
        const url = meta.icon ? `${ICON_BASE}${meta.icon}` : null;
        return url && _iconStatus[url] === "ok"
          ? `<img class="ff-cico" src="${url}" alt="">`
          : `<span class="ff-ccode" style="background:${meta.color}">${this._escape(meta.code)}</span>`;
      })
      .join("");
    const more = extra > 0 ? `<span class="ff-cmore">+${extra}</span>` : "";
    const label = min != null ? this._price(min) : "";
    const count = cluster.getChildCount();
    return L.divIcon({
      className: "ff-cluster-wrap",
      html: `<div class="ff-cluster${globalCheap ? " cheap" : ""}">${icons}${more}<span class="ff-cprice">${label}</span><span class="ff-cluster-n">·${count}</span></div>`,
      iconSize: null,
    });
  }

  // -- helpers --------------------------------------------------------------
  _price(v, unit) {
    if (v === null || v === undefined) return "–";
    return `${Number(v).toFixed(2).replace(".", ",")}${unit ? " " + unit : ""}`;
  }

  _notice(text) {
    return `<div class="ff-notice">${this._escape(text)}</div>`;
  }

  _escape(s) {
    return String(s === null || s === undefined ? "" : s).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }
}

_define("tankpriser-card", TankpriserCard);

// -- visual editor ----------------------------------------------------------
// Every field the editor can show, by name. Kept separate from the assembly
// below so the form can be composed per config without duplicating a selector.
const EDITOR_FIELDS = {
  title: { name: "title", selector: { text: {} } },
  entity: { name: "entity", selector: { entity: { integration: "tankpriser", domain: "sensor" } } },
  fuel: {
    name: "fuel",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "", label: "(use the entity's fuel)" },
          ...Object.entries(FUEL_LABELS).map(([value, label]) => ({ value, label })),
        ],
      },
    },
  },
  show_map: { name: "show_map", selector: { boolean: {} } },
  coverage: {
    name: "coverage",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "national", label: "National (all of Denmark, viewport)" },
          { value: "area", label: "Home area only" },
        ],
      },
    },
  },
  map_theme: {
    name: "map_theme",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "auto", label: "Auto (follow HA theme)" },
          { value: "light", label: "Light" },
          { value: "dark", label: "Dark" },
        ],
      },
    },
  },
  map_height: { name: "map_height", selector: { number: { min: 200, max: 1000, step: 20, mode: "slider", unit_of_measurement: "px" } } },
  cluster: { name: "cluster", selector: { boolean: {} } },
  show_my_location: { name: "show_my_location", selector: { boolean: {} } },
  follow_me: { name: "follow_me", selector: { boolean: {} } },
  show_cars: { name: "show_cars", selector: { boolean: {} } },
  car_picker: { name: "car_picker", selector: { boolean: {} } },
  navigation: {
    name: "navigation",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "auto", label: "Auto (device's own navigator)" },
          { value: "geo", label: "Always the app chooser (geo:, Android)" },
          { value: "apple", label: "Apple Maps" },
          { value: "google", label: "Google Maps" },
          { value: "osm", label: "OpenStreetMap" },
          { value: "off", label: "No navigate link" },
        ],
      },
    },
  },
  show_list: { name: "show_list", selector: { boolean: {} } },
};

// Which fields are worth showing for a given config. With the map off, most of
// these control nothing that exists: there are no markers to cluster, no popups
// to put a navigate link in, nowhere to draw a position dot or a car, and the
// price list is shown unconditionally. Offering them anyway invites someone to
// switch on "follow me" for a price table and conclude the card is broken.
// Same reasoning one level down: follow-me needs the position dot, and the
// per-device car picker needs cars.
function _editorFieldNames(config) {
  const names = ["title", "entity", "fuel", "show_map"];
  if (config.show_map !== true) return names;

  names.push("coverage", "map_theme", "map_height", "cluster", "show_my_location");
  if (config.show_my_location !== false) names.push("follow_me");
  names.push("show_cars");
  if (config.show_cars !== false) names.push("car_picker");
  names.push("navigation", "show_list");
  return names;
}

// One array per distinct form shape, reused. ha-form re-renders whenever its
// `schema` property changes, and handing it a freshly built array on every
// keystroke would drop focus out of the title field mid-word. There are eight
// possible shapes at most.
const _schemaCache = new Map();

function _editorSchema(config) {
  const names = _editorFieldNames(config || {});
  const key = names.join(",");
  if (!_schemaCache.has(key)) {
    _schemaCache.set(key, names.map((name) => EDITOR_FIELDS[name]));
  }
  return _schemaCache.get(key);
}

const EDITOR_LABELS = {
  title: "Title",
  entity: "Price sensor — sets the fuel, area and radius shown",
  fuel: "Fuel to plot (national map only)",
  show_map: "Show map",
  coverage: "Map coverage",
  map_theme: "Map theme",
  map_height: "Map height",
  cluster: "Group nearby stations",
  show_my_location: "Show my position on the map",
  follow_me: "Start with follow-me on",
  show_cars: "Show my cars (ringed by fuel level)",
  car_picker: "Let each device choose which cars to show",
  navigation: "Navigate link in station popups",
  show_list: "Show price list",
};

class TankpriserCardEditor extends HTMLElement {
  setConfig(config) {
    // The editor manages a single `entity`; map an existing `entities` list.
    this._config = { ...config };
    if (!this._config.entity && Array.isArray(this._config.entities) && this._config.entities.length) {
      this._config.entity = this._config.entities[0];
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => EDITOR_LABELS[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const next = { ...ev.detail.value };
        // Prefer a single `entity`; drop an old `entities` list if present.
        if (next.entity) delete next.entities;
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: next },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    // Recomputed per render: switching the map on or off changes which options
    // mean anything (see _editorFieldNames).
    this._form.schema = _editorSchema(this._config);
    this._form.data = this._config;
  }
}

_define("tankpriser-card-editor", TankpriserCardEditor);

/*
 * Tankpriser prediction card
 * Shows when a car will next need refuelling, learned from its fuel level.
 *
 * Config:
 *   type: custom:tankpriser-prediction-card
 *   entity: sensor.<car>_days_until_refuel
 *   title: "Passat"            # optional
 *   show_donate: true          # optional, default true
 *   donate_url: "..."          # optional; defaults to the sensor's link
 *
 * The prediction is free. It genuinely took work to build, so the card asks
 * for a donation — but never withholds anything and the ask can be hidden.
 */
class TankpriserPredictionCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Set 'entity' to a sensor.<car>_days_until_refuel");
    }
    this._config = {
      entity: config.entity,
      title: config.title || "",
      show_donate: config.show_donate !== false,
      donate_url: _safeUrl(config.donate_url) || "",
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  static getConfigElement() {
    return document.createElement("tankpriser-prediction-card-editor");
  }

  static getStubConfig(hass) {
    const match = hass
      ? Object.keys(hass.states).find((id) =>
          id.endsWith("_days_until_refuel")
        )
      : "";
    return { entity: match || "" };
  }

  _escape(s) {
    return String(s === null || s === undefined ? "" : s).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  _fmtDate(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const lang = (this._hass && this._hass.locale && this._hass.locale.language) || undefined;
    return d.toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "short" });
  }

  _render() {
    if (!this._hass || !this._config) return;
    const st = this._hass.states[this._config.entity];
    if (!st) {
      this.innerHTML = `<ha-card><div class="tp-pred-notice">Entity ${this._escape(
        this._config.entity
      )} not found.</div>${this._style()}</ha-card>`;
      return;
    }

    const a = st.attributes || {};
    const noValue = st.state === "unknown" || st.state === "unavailable";
    // Three states now: nothing to say yet, a provisional number from the tank
    // in progress, and a number backed by completed tanks. Hiding the middle one
    // meant weeks of "Estimating…" while a usable answer already existed.
    const learning = noValue || a.status === "learning" || !a.status;
    const early = !learning && a.status === "estimating";

    const pct = a.current_level_percent;
    const litres = a.current_level_l;
    const bar =
      pct === null || pct === undefined
        ? ""
        : `<div class="tp-pred-bar"><div class="tp-pred-fill" style="width:${Math.max(
            0,
            Math.min(100, Number(pct))
          )}%"></div></div>
           <div class="tp-pred-level">${this._escape(pct)} %${
            litres !== null && litres !== undefined
              ? ` · ${this._escape(litres)} L`
              : ""
          }</div>`;

    let head;
    if (learning) {
      head = `<div class="tp-pred-head tp-pred-learning">
          <div class="tp-pred-big">Learning…</div>
          <div class="tp-pred-sub">A day or two of driving is enough for a first estimate.</div>
        </div>`;
    } else {
      const days = Number(st.state);
      const shown = Number.isFinite(days)
        ? days >= 10
          ? Math.round(days)
          : days.toFixed(1)
        : st.state;
      const when = a.predicted_empty ? this._fmtDate(a.predicted_empty) : "";
      head = `<div class="tp-pred-head">
          <div><span class="tp-pred-big">${early ? "~" : ""}${this._escape(shown)}</span>
               <span class="tp-pred-unit">days</span></div>
          <div class="tp-pred-sub">until refuel${
            when ? ` · ≈ ${this._escape(when)}` : ""
          }</div>
          ${
            early
              ? `<div class="tp-pred-early">Early estimate from the tank you are
                   on now — it will settle as tanks complete.</div>`
              : ""
          }
        </div>`;
    }

    const rows = [];
    if (!learning) {
      if (a.avg_consumption !== null && a.avg_consumption !== undefined) {
        rows.push([
          "Consumption",
          `${this._escape(a.avg_consumption)} ${this._escape(a.consumption_unit || "")}`,
        ]);
      }
      if (a.confidence !== null && a.confidence !== undefined) {
        rows.push([
          "Confidence",
          `${Math.round(Number(a.confidence) * 100)} %${
            a.learned_tanks ? ` · ${this._escape(a.learned_tanks)} tanks` : ""
          }`,
        ]);
      }
      if (a.cheapest_station) {
        rows.push([
          `Cheapest ${this._escape((a.fuel_type || "").toLowerCase())}`.trim(),
          `${this._escape(a.cheapest_station)}${
            a.cheapest_price !== null && a.cheapest_price !== undefined
              ? ` · ${this._escape(a.cheapest_price)}`
              : ""
          }`,
        ]);
      }
    }
    const details = rows
      .map(
        ([k, v]) =>
          `<div class="tp-pred-row"><span>${this._escape(k)}</span><b>${v}</b></div>`
      )
      .join("");

    const donateUrl =
      this._config.donate_url || _safeUrl(a.donate_url) || DONATE_URL;
    const donate = this._config.show_donate
      ? `<div class="tp-pred-donate">
           This prediction took real work to build. If it's useful,
           <a href="${this._escape(donateUrl)}" target="_blank" rel="noopener">please consider a donation 💛</a>
         </div>`
      : "";

    this.innerHTML = `
      <ha-card ${
        this._config.title ? `header="${this._escape(this._config.title)}"` : ""
      }>
        <div class="tp-pred-body">
          ${head}
          ${bar}
          <div class="tp-pred-details">${details}</div>
          ${donate}
        </div>
        ${this._style()}
      </ha-card>`;
  }

  _style() {
    return `<style>
      .tp-pred-body { padding: 12px 16px 16px; }
      .tp-pred-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 10px; }
      .tp-pred-big { font-size: 2.4em; font-weight: 600; line-height: 1; color: var(--primary-text-color); }
      .tp-pred-unit { font-size: 1em; color: var(--secondary-text-color); margin-left: 4px; }
      .tp-pred-sub { color: var(--secondary-text-color); font-size: 0.9em; }
      .tp-pred-learning .tp-pred-big { font-size: 1.6em; color: var(--secondary-text-color); }
      .tp-pred-early { color: var(--warning-color, #b8860b); font-size: 0.85em; margin-top: 2px; }
      .tp-pred-bar { height: 8px; border-radius: 4px; background: var(--divider-color); overflow: hidden; margin: 6px 0 4px; }
      .tp-pred-fill { height: 100%; background: var(--primary-color); border-radius: 4px; }
      .tp-pred-level { font-size: 0.85em; color: var(--secondary-text-color); margin-bottom: 8px; }
      .tp-pred-details { display: flex; flex-direction: column; gap: 4px; }
      .tp-pred-row { display: flex; justify-content: space-between; gap: 12px; font-size: 0.95em; }
      .tp-pred-row span { color: var(--secondary-text-color); }
      .tp-pred-row b { color: var(--primary-text-color); text-align: right; }
      .tp-pred-donate { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color);
                        font-size: 0.9em; color: var(--secondary-text-color); }
      .tp-pred-donate a { color: var(--primary-color); text-decoration: none; }
      .tp-pred-notice { padding: 16px; color: var(--secondary-text-color); }
    </style>`;
  }
}

_define("tankpriser-prediction-card", TankpriserPredictionCard);

const PRED_EDITOR_FIELDS = {
  title: { name: "title", selector: { text: {} } },
  entity: { name: "entity", selector: { entity: { integration: "tankpriser", domain: "sensor" } } },
  show_donate: { name: "show_donate", selector: { boolean: {} } },
  donate_url: { name: "donate_url", selector: { text: {} } },
};

// A donation link is meaningless with the ask switched off. Cached for the same
// reason as the price card's schema: a fresh array on every render steals focus.
const _predSchemaCache = new Map();

function _predEditorSchema(config) {
  const names = ["title", "entity", "show_donate"];
  if ((config || {}).show_donate !== false) names.push("donate_url");
  const key = names.join(",");
  if (!_predSchemaCache.has(key)) {
    _predSchemaCache.set(key, names.map((name) => PRED_EDITOR_FIELDS[name]));
  }
  return _predSchemaCache.get(key);
}
const PRED_EDITOR_LABELS = {
  title: "Title",
  entity: "Prediction sensor (…_days_until_refuel)",
  show_donate: "Show the donation ask",
  donate_url: "Donation link (optional)",
};

class TankpriserPredictionCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => PRED_EDITOR_LABELS[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: { ...ev.detail.value } },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = _predEditorSchema(this._config);
    this._form.data = this._config;
  }
}

_define("tankpriser-prediction-card-editor", TankpriserPredictionCardEditor);

window.customCards = window.customCards || [];
// Same reason as _define: a duplicate load must not list the cards twice in the
// "add card" picker.
function _listCard(card) {
  if (!window.customCards.some((c) => c && c.type === card.type)) {
    window.customCards.push(card);
  }
}
_listCard({
  type: "tankpriser-card",
  name: "Tankpriser Prices",
  description: "Map of local fuel stations with company icons + prices (nearby stations grouped, cluster shows the lowest price), plus an optional price table.",
  preview: false,
});
_listCard({
  type: "tankpriser-prediction-card",
  name: "Tankpriser Prediction",
  description: "Predicts when a car will next need refuelling, learned from its fuel level, with the cheapest nearby price for its fuel.",
  preview: false,
});

console.info("%c TANKPRISER-CARD %c loaded ", "background:#0a7d3c;color:#fff", "");

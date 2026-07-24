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

// Where "Support the project" points unless a dashboard overrides it.
const DONATE_URL = "https://github.com/laithsaid/ha-tankpriser";

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
    };
    this._built = false;
    this._map = null;
    this._markerLayer = null;
    this._carLayer = null;
    this._carSig = null;
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
    this._subscribeUpdates();
    this._update();
  }

  connectedCallback() {
    this._subscribeUpdates();
    if (this._config && this._config.show_map && this._config.show_my_location) {
      this._startWatchingPosition();
    }
  }

  disconnectedCallback() {
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
        .ff-car {
          position: relative; transform: translate(-50%, -50%);
          width:34px; height:34px; border-radius:50%;
          background: var(--card-background-color, #fff);
          border:3px solid #888; box-shadow: 0 1px 4px rgba(0,0,0,.45);
          display:flex; align-items:center; justify-content:center;
        }
        .ff-car-glyph { font-size:17px; line-height:1; }
        .ff-car-img { width:100%; height:100%; object-fit:cover; border-radius:50%; display:block; }
        .ff-car-pct {
          position:absolute; bottom:-7px; left:50%; transform:translateX(-50%);
          font-size:9px; font-weight:700; color:#fff; line-height:1;
          padding:1px 4px; border-radius:8px; white-space:nowrap;
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
            return `
              <tr class="${isCheap ? "cheapest" : ""}">
                <td class="ff-name">${this._escape(s.name)}${approx}
                  ${s.updated ? `<div class="ff-updated">${this._escape(s.updated)}</div>` : ""}
                </td>
                <td class="price">${this._price(s.price, unit)}</td>
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
            updated: s.updated || null, pf: {},
          };
          byKey.set(k, rec);
        }
        if (!rec.updated && s.updated) rec.updated = s.updated;
        if (s.price != null) rec.pf[fuel] = s.price;
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
      L = this._config.cluster ? await loadCluster() : await loadLeaflet();
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
      this._addMapControls(L);
      if (this._config.show_my_location) {
        this._startWatchingPosition();
        // A fix may already have arrived before the map existed.
        if (this._pos) this._drawMe(this._pos, this._posAccuracy);
      }
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
      marker.bindPopup(
        `<b>${this._escape(s.name)}</b>${s.city ? "<br>" + this._escape(s.city) : ""}` +
          `<br>${priceLines}` +
          updated +
          (s.approx ? `<div><i>≈ approximate location</i></div>` : "")
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
  // Find the Tankpriser car sensors (sensor.*_days_until_refuel) that carry a
  // position. An explicit `cars:` list wins; otherwise every car is shown.
  _discoverCars() {
    if (!this._hass) return [];
    const states = this._hass.states;
    let ids;
    if (this._config.cars) {
      ids = this._config.cars;
    } else {
      ids = Object.keys(states).filter(
        (id) => states[id] && states[id].attributes && states[id].attributes.is_car
      );
    }
    const cars = [];
    for (const id of ids) {
      const st = states[id];
      if (!st) continue;
      const a = st.attributes || {};
      const lat = Number(a.latitude);
      const lon = Number(a.longitude);
      if (!isFinite(lat) || !isFinite(lon)) continue; // no position → skip
      cars.push({ id, state: st.state, a, lat, lon });
    }
    return cars;
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
      this._carLayer = L.layerGroup().addTo(this._map);
    }
    if (!this._config.show_cars) {
      this._carLayer.clearLayers();
      return;
    }

    const cars = this._discoverCars();
    // Only rebuild when something changed, so we don't fight the user's pan.
    const sig = cars
      .map((c) => `${c.id}|${c.lat.toFixed(5)}|${c.lon.toFixed(5)}|${c.a.current_level_percent}|${c.state}|${c.a.car_picture || ""}`)
      .join(";");
    if (sig === this._carSig) return;
    this._carSig = sig;

    this._carLayer.clearLayers();
    for (const c of cars) {
      const pct = c.a.current_level_percent;
      const color = this._carColor(pct);
      const pctLabel = pct === null || pct === undefined ? "?" : `${Math.round(pct)}%`;
      // Use the car's own picture if it has one, else a generic car glyph.
      const pic = _safeUrl(c.a.car_picture);
      const inner = pic
        ? `<img class="ff-car-img" src="${this._escape(pic)}" alt="" referrerpolicy="no-referrer">`
        : `<span class="ff-car-glyph">🚗</span>`;
      const icon = L.divIcon({
        className: "ff-car-wrap",
        html: `<div class="ff-car" style="border-color:${color}">
                 ${inner}
                 <span class="ff-car-pct" style="background:${color}">${this._escape(pctLabel)}</span>
               </div>`,
        iconSize: null,
      });
      const marker = L.marker([c.lat, c.lon], { icon, zIndexOffset: 1000 });

      const name = c.a.car_name || "Car";
      const days =
        c.a.status === "ready" && c.state !== "unknown" && c.state !== "unavailable"
          ? `${this._escape(c.state)} days until refuel`
          : "Still learning your consumption";
      const litres =
        c.a.current_level_l != null
          ? ` (${this._escape(c.a.current_level_l)} L)`
          : "";
      const cheapest = c.a.cheapest_station
        ? `<br>Cheapest ${this._escape((c.a.fuel_type || "").toLowerCase())}: ${this._escape(
            c.a.cheapest_station
          )}${c.a.cheapest_price != null ? ` · ${this._escape(c.a.cheapest_price)}` : ""}`
        : "";
      marker.bindPopup(
        `<b>${this._escape(name)}</b><br>Fuel: <b>${this._escape(pctLabel)}</b>${litres}<br>${days}${cheapest}`
      );
      this._carLayer.addLayer(marker);
    }
  }

  _addMapControls(L) {
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

        if (card._config.show_my_location) {
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
        }

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

customElements.define("tankpriser-card", TankpriserCard);

// -- visual editor ----------------------------------------------------------
const EDITOR_SCHEMA = [
  { name: "title", selector: { text: {} } },
  { name: "entity", selector: { entity: { integration: "tankpriser", domain: "sensor" } } },
  {
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
  { name: "show_map", selector: { boolean: {} } },
  {
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
  {
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
  { name: "map_height", selector: { number: { min: 200, max: 1000, step: 20, mode: "slider", unit_of_measurement: "px" } } },
  { name: "cluster", selector: { boolean: {} } },
  { name: "show_my_location", selector: { boolean: {} } },
  { name: "follow_me", selector: { boolean: {} } },
  { name: "show_cars", selector: { boolean: {} } },
  { name: "show_list", selector: { boolean: {} } },
];

const EDITOR_LABELS = {
  title: "Title",
  entity: "Sensor (picks the fuel & area)",
  fuel: "Fuel (national map)",
  show_map: "Show map",
  coverage: "Map coverage",
  map_theme: "Map theme",
  map_height: "Map height",
  cluster: "Group nearby stations",
  show_my_location: "Show my position on the map",
  follow_me: "Start with follow-me on",
  show_cars: "Show my cars (ringed by fuel level)",
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
    this._form.schema = EDITOR_SCHEMA;
    this._form.data = this._config;
  }
}

customElements.define("tankpriser-card-editor", TankpriserCardEditor);

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
    const learning =
      a.status !== "ready" ||
      st.state === "unknown" ||
      st.state === "unavailable";

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
          <div class="tp-pred-big">Estimating…</div>
          <div class="tp-pred-sub">Available after a few refuels — still learning your consumption.</div>
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
          <div><span class="tp-pred-big">${this._escape(shown)}</span>
               <span class="tp-pred-unit">days</span></div>
          <div class="tp-pred-sub">until refuel${
            when ? ` · ≈ ${this._escape(when)}` : ""
          }</div>
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

customElements.define("tankpriser-prediction-card", TankpriserPredictionCard);

const PRED_EDITOR_SCHEMA = [
  { name: "title", selector: { text: {} } },
  { name: "entity", selector: { entity: { integration: "tankpriser", domain: "sensor" } } },
  { name: "show_donate", selector: { boolean: {} } },
  { name: "donate_url", selector: { text: {} } },
];
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
    this._form.schema = PRED_EDITOR_SCHEMA;
    this._form.data = this._config;
  }
}

customElements.define(
  "tankpriser-prediction-card-editor",
  TankpriserPredictionCardEditor
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "tankpriser-card",
  name: "Tankpriser Prices",
  description: "Map of local fuel stations with company icons + prices (nearby stations grouped, cluster shows the lowest price), plus an optional price table.",
  preview: false,
});
window.customCards.push({
  type: "tankpriser-prediction-card",
  name: "Tankpriser Prediction",
  description: "Predicts when a car will next need refuelling, learned from its fuel level, with the cheapest nearby price for its fuel.",
  preview: false,
});

console.info("%c TANKPRISER-CARD %c loaded ", "background:#0a7d3c;color:#fff", "");

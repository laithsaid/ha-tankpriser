# Tankpriser — Architecture

Tankpriser is a Home Assistant custom integration with two loosely-coupled
subsystems that share a **single config entry** (`async_set_unique_id(DOMAIN)`,
so there is only ever one):

1. **Fuel-price aggregation** — pull the free Danish per-station price APIs,
   filter them to a Home-based area, and expose per-fuel sensors plus a
   map/list Lovelace card.
2. **Fuel-consumption prediction** — learn each car's consumption from a
   fuel-level entity you already have, and expose a *days-until-refuel* sensor,
   a prediction card, and a marker on the map.

Everything is configured from the UI; there is no `configuration.yaml`.

```
                              ┌──────────────────────────────────────────┐
                              │            config entry (single)          │
                              │  data: fuel_types, credentials            │
                              │  options: radius, notify…                 │
                              │  subentries: car "Passat", car "…"        │
                              └───────────────┬──────────────┬───────────┘
                                              │              │
              ┌───────────────────────────────┘              └───────────────────────┐
              ▼                                                                        ▼
   ── SUBSYSTEM 1: PRICES ──────────────────────────           ── SUBSYSTEM 2: PREDICTION ──────────────
   sources.py   ── nationwide chain APIs (OK/Q8/…)             config_flow.py  ── "Add car" subentry flow
      │  TTL cache, per-provider                                   │
      ▼                                                            ▼
   geo.py       ── DAWA: postnummer↔radius, centres          consumption.py  ── one tracker per car:
      │                                                          │   watches the fuel-level entity,
      ▼                                                          │   detects refuels, persists history
   coordinator.py ── per-entry DataUpdateCoordinator            ▼
      │  resolve area → fetch_all → filter → notify         prediction.py   ── PURE model + estimator
      ▼                                                          │   (no HA imports; unit-tested)
   sensor.py    ── one TankpriserSensor per fuel                ▼
   websocket.py ── tankpriser/stations (national map)       sensor.py       ── CarPredictionSensor
   notifications.py ── price-change notify + event              │
      │                                                          │
      └──────────────────────┬───────────────────────────────────┘
                             ▼
                  www/tankpriser-card.js
                  • tankpriser-card            (prices: list + Leaflet map + cars)
                  • tankpriser-prediction-card (per-car panel + donation ask)
```

## Module responsibilities

| File | Responsibility |
| --- | --- |
| `__init__.py` | Entry/component setup: registers the card (static path + frontend module + Lovelace resource — see below), the websocket command and services; creates the coordinator and the per-car trackers; migration and unload. |
| `const.py` | All constants: endpoints, fuel-type table, config keys, tuning values, `DONATE_URL`. Pure (no HA imports), so `prediction.py` can import from it. |
| `sources.py` | Fuel-price **providers**. Each chain is a `Provider` record; `fetch_all()` fetches the active ones concurrently through a shared TTL cache and normalises them into `Station` records. |
| `geo.py` | DAWA (Danish address API) helpers: resolve *postnummer + radius* → set of postnumre; look up postnummer centre coordinates. Process-life caches. |
| `coordinator.py` | One `TankpriserCoordinator` per entry: resolves the area, calls `fetch_all`, filters stations to the area, fills approximate coordinates, fires notifications/events. Holds the per-car trackers in `.cars`. |
| `sensor.py` | `TankpriserSensor` (cheapest price per fuel, full list in attributes) and `CarPredictionSensor` (days-until-refuel + prediction attributes + car position/picture). |
| `websocket.py` | `tankpriser/stations` command returning **all** national stations with coordinates — used by the card's `coverage: national` map instead of a huge sensor attribute. |
| `notifications.py` | Compares successive refreshes and calls a `notify.*` service per the chosen rule; also fires the `tankpriser_price_updated` event. |
| `config_flow.py` | Initial flow (fuel types), options flow (menu: settings / notifications / chain keys), and the **car subentry** flow (`ConfigSubentryFlow`). |
| `consumption.py` | HA glue for prediction: `ConsumptionTracker` watches the source entity, feeds the pure model, persists via `Store`, resolves the car's coordinates/picture, and notifies dependent sensors. |
| `prediction.py` | **Pure** (no `homeassistant` imports) learning core: refuel detection, the `ConsumptionModel`, and `predict()`. Fully unit-testable offline. |
| `services.py` / `services.yaml` | `seed_demo_history` (inject synthetic tanks for testing/demo) and `reset_history`. |
| `diagnostics.py` | Redacted config-entry diagnostics (credentials + area name + notify target redacted; postnummer kept). |
| `www/tankpriser-card.js` | Two custom cards: the price card (list + Leaflet map + car markers) and the prediction card. Leaflet is vendored under `www/vendor/`. |
| `brand/`, `translations/`, `strings.json` | Bundled brand icons, and the flow/selector translations (en + da). |

### How the card reaches the browser

`www/` is served under `/tankpriser/`, and the card JS is published to clients
**twice**, because the two routes reach different clients:

| Route | Reaches | Misses |
| --- | --- | --- |
| `frontend.add_extra_js_url` — a `<script type="module">` written into `index.html` as it is served | any client that fetches a *fresh* `index.html` | clients holding an older `index.html`: the companion apps keep that document for days (pull-to-refresh does not refetch it), and one fetched early in a restart has no tag at all |
| A **Lovelace resource** (storage-mode dashboards only) — fetched over the live websocket every time a dashboard opens | exactly those stale clients, plus new devices/users on their first open | YAML-mode dashboards, whose resource list belongs to `configuration.yaml` |

Both point at the same `…/tankpriser-card.js?v=<version>` URL, so the browser's
module map runs the file once. The `?v=` on the stored resource is kept in step
with the installed version on every setup, so an update is never masked by a
browser cache. A duplicate load (e.g. a hand-added resource left over from
before) is harmless: the file defines its elements defensively.

Symptom when this goes wrong: the card renders as Home Assistant's
"Configuration error" card — dark, with a red `!` — because the custom element
was never defined. The frontend hides that error for 2 s and rebuilds the card
by itself if the element defines late, so an error that *persists* means the
script never loaded on that client at all.

## Subsystem 1 — fuel prices (data flow)

1. **Fetch** (`sources.fetch_all`): each `Provider` is fetched concurrently.
   Results are cached per provider for `PROVIDER_CACHE_TTL` (10 min), keyed with
   a **credential fingerprint** so a changed key bypasses the cache. A failing
   provider serves its last good data until `MAX_STALE_AGE` (6 h), then drops
   out entirely (stale prices are worse than none). Every station is normalised
   into a `Station` (name, company, postnummer, coords, `prices{fuel_key: kr}`).
2. **Resolve area** (`geo`): the entry has no postnummer — the area is the HA
   **Home location**. `postnumre_within_point(lat, lon, radius)` asks DAWA which
   postnumre fall inside the circle. (Legacy entries created before v0.6 still
   resolve from a stored postnummer.)
3. **Filter** (`coordinator`): keep stations whose `postnummer` is in that set,
   fill approximate coordinates (postnummer centre) for coordinate-less chains,
   drop hidden stations, sort.
4. **Surface**: one `TankpriserSensor` per configured fuel — state = cheapest
   price, attributes = the full station list. The card renders the list and/or a
   Leaflet map. `coverage: national` instead calls the `tankpriser/stations`
   websocket for every DK station and uses the map viewport as the filter.

## Subsystem 2 — consumption prediction (data flow)

1. **Config**: each car is a **config subentry** (so any number can be added).
   It records the fuel-level entity, the attribute/units, tank capacity, an
   optional odometer, and the fuel to price against.
2. **Collect** (`ConsumptionTracker`): subscribes to the source entity. On each
   change it reads the level (via a dotted attribute path or the state),
   normalises to litres, and feeds the pure model. History is persisted to
   `.storage` (debounced).
3. **Learn** (`prediction.ConsumptionModel`): an upward jump ≥ 15 % of the tank
   is a *refuel*, which closes the current tank into a `Segment`
   (litres consumed, duration, odometer delta).
4. **Estimate** (`prediction.predict`): an exponentially-weighted average of the
   per-tank rates → days-until-empty (always a time rate), reported as L/100 km
   when an odometer is present, otherwise L/day, with a confidence score. Below
   two learned tanks it returns nothing (the sensor reads `unknown` — it never
   guesses).
5. **Surface**: `CarPredictionSensor` exposes days-until-refuel plus level,
   consumption, confidence, predicted-empty date, the cheapest nearby station
   for the car's fuel, and the car's live position/picture. The price card plots
   the car (photo in a fuel-coloured ring); the prediction card shows a panel.

## External services & privacy

| Service | Called by | Purpose | Privacy |
| --- | --- | --- | --- |
| Chain price APIs (OK, Q8/F24, Shell, OIL!) | HA server | Prices | Server-side; honest `User-Agent`, no auth. |
| DAWA `api.dataforsyningen.dk` | HA server | Area resolution + postnummer centres | Server-side, keyless. |
| OSM / CARTO map tiles | **browser** | Map background | Leaks IP + viewed area. Avoid with `show_map: false`. |
| A car's `entity_picture` URL | **browser** | Car photo on the marker | Only if the picture is an external URL; `no-referrer`. Use a `/local/…` image to avoid it. |

Leaflet and all chain icons are **vendored and served by HA**, so the map needs
no third-party code. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the SRI /
vendoring details.

## Caching layers

- **Provider responses** — `sources._CACHE`, 10 min TTL, shared across areas,
  credential-fingerprinted, with stale-serving up to 6 h.
- **DAWA postnummer centres** — `geo._CENTER_CACHE`, process-life (this data
  never changes); batched `nr=a|b|c` lookups (100 per request).
- **Area resolution** — per coordinator, keyed by radius.
- **National station list** — cached ~5 min in the card after a websocket fetch.
- **Last-known car position/picture** — persisted per car, so a parked car stays
  on the map through GPS dropouts.

## Design principles

- **Pure core, thin glue.** `prediction.py` and `const.py` import no HA, so the
  learning maths is unit-tested offline; `consumption.py`/`sensor.py` are the
  only HA-aware prediction code.
- **Providers are data.** Adding a chain is appending one `Provider` + a parser;
  the options dialog, guide text, credential storage, validation and diagnostics
  redaction are all driven from the `Provider` fields.
- **Single instance + subentries.** One entry (the area); cars are subentries so
  there's no artificial limit.
- **Free, donation-requested.** The prediction is not gated; the card asks for a
  donation (`DONATE_URL`) but withholds nothing. A public HACS repo can't
  meaningfully enforce a gate anyway.
- **Degrade, don't fail.** One slow/broken chain must not take the others down;
  missing coordinates fall back to a postnummer centre / zone / last-known.

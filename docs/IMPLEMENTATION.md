# Tankpriser — Implementation notes

Developer-facing detail, written to be read **next to the code**: every claim
names the file and function it lives in, so you can jump there. For the big
picture — which subsystems exist and why — read [ARCHITECTURE.md](ARCHITECTURE.md)
first. For installing and verifying a build on real hardware, see
[TESTING.md](TESTING.md).

If you are looking for one specific thing, skip to
[Where to look if…](#where-to-look-if) at the bottom.

- [Layout](#layout)
- [Entry points](#entry-points) — the only three ways code here starts running
- [Walkthrough 1: a price refresh](#walkthrough-1-a-price-refresh)
- [Walkthrough 2: a car's fuel level changes](#walkthrough-2-a-cars-fuel-level-changes)
- [Walkthrough 3: the card paints the map](#walkthrough-3-the-card-paints-the-map)
- [Contracts](#contracts) — the data shapes each layer hands the next
- [Module reference](#module-reference)
- [The prediction algorithm](#the-prediction-algorithm-predictionpy)
- [Storage schema](#storage-schema)
- [Where to look if…](#where-to-look-if)

## Layout

```
custom_components/tankpriser/
  __init__.py        setup / unload / migration, registers card + ws + services
  const.py           constants (pure — no HA imports)
  sources.py         fuel-price providers + normalisation + cache
  geo.py             DAWA area resolution + postnummer centres
  geocode.py         DAWA address -> coordinates for Q8/F24 (disk-cached)
  coordinator.py     per-entry DataUpdateCoordinator (+ .cars trackers)
  nearby.py          pure ranking of stations around a point (haversine)
  sensor.py          TankpriserSensor, NearbyStationsSensor, CarPredictionSensor
  websocket.py       tankpriser/stations command
  notifications.py   price-change notify + event
  config_flow.py     initial + options + car-subentry flows
  consumption.py     ConsumptionTracker (HA glue for prediction)
  prediction.py      pure learning model + estimator
  services.py/.yaml  seed_demo_history, reset_history
  diagnostics.py     redacted diagnostics
  www/               tankpriser-card.js + vendored Leaflet/icons
  brand/             bundled brand icons (HA 2026.3+)
  translations/      en.json, da.json  (strings.json is the source)
tests/
  card.test.js       card helpers (navigate URLs, car filter, cluster icon)
  map.test.js        the card + Leaflet + markercluster in a jsdom DOM
  test_prediction.py refuel detection + the two-tier estimator
  test_discounts.py  chain matching + per-chain loyalty discounts
  test_geocode.py    address parsing + geocode cache policy
  test_card_registration.py   the Lovelace resource registration
  test_spoken.py     the sentence the nearby sensor hands to Siri
  test_nearby.py     ranking stations around a position
package.json         jsdom, for the tests only — the integration ships no JS deps
scratchpad/          local-only experiments (git-ignored)
```

Roughly 3 500 lines of Python and 1 800 of JavaScript. `const.py` and
`prediction.py` import nothing from Home Assistant, which is what makes them
testable without it.

## Entry points

Nothing here runs on its own. There are exactly three ways in, and every stack
trace you will ever see starts in one of them:

| Trigger | Enters at | Does |
| --- | --- | --- |
| HA loads the component (once, because a config entry exists) | `__init__.async_setup` | registers the websocket command, the two services, and publishes the card |
| The config entry is set up (also on reload, and after options change) | `__init__.async_setup_entry` | builds the coordinator, does the **first refresh**, starts one `ConsumptionTracker` per car, forwards to the `sensor` platform |
| A browser opens a dashboard | `www/tankpriser-card.js` (module top level) | defines `tankpriser-card` + `tankpriser-prediction-card`; HA instantiates one per card, calling `setConfig()` then `hass` on every state change |

After that, everything is a callback:

- `DataUpdateCoordinator` timer → `coordinator._async_update_data` (every
  `scan_interval`, default 30 min)
- HA state change on a car's source entity → `consumption._handle_event`
- the card's websocket subscription to `tankpriser_price_updated` →
  `_ensureNational()` refetches the national list
- a service call → `services._seed` / `services._reset`

Two things worth knowing about setup order. `async_setup` publishes the card
*before* any entry is set up, because a client that loads the frontend while the
entry is still starting would otherwise get a page with no card (see
[ARCHITECTURE.md](ARCHITECTURE.md#how-the-card-reaches-the-browser)). And
`async_setup_entry` calls `async_config_entry_first_refresh()`, so a provider
outage at startup makes HA retry the whole entry rather than create empty
sensors.

## Walkthrough 1: a price refresh

The main loop. Read these five functions in this order and you understand the
price side of the integration.

```
coordinator._async_update_data()                     ← every scan_interval
├── _resolve_area()                    -> set[str] of postnumre
│   └── geo.postnumre_within_point(lat, lon, radius_m)      DAWA "cirkel="
│       (cached per radius in self._area_cache; legacy entries with a stored
│        postnummer go through geo.postnumre_within instead)
├── sources.fetch_all(session, credentials)          -> list[Station]  (all DK)
│   └── per provider, concurrently: _fetch_provider()
│       ├── TTL cache hit (PROVIDER_CACHE_TTL, 10 min, keyed by a credential
│       │   fingerprint) -> return it
│       ├── Provider.fetch() -> _fetch_json() -> parse_ok / parse_q8 /
│       │   parse_shell / fetch_oil    (one parser per chain, each returning
│       │   normalised Station records)
│       └── on failure: serve the last good payload until MAX_STALE_AGE (6 h),
│           then drop that provider entirely — stale prices are worse than none
├── filter: keep s.postnummer in area                -> the area's stations
├── _fill_coordinates(stations)                       fills lat/lon in place
│   ├── geocode.async_get(hass).apply(missing)        cache only, no network
│   │   -> returns the ones still unplaced *and* the ones due a re-check
│   ├── geocoder.async_schedule(...)                  background DAWA lookups,
│   │   then coordinator.async_request_refresh()      (never awaited here)
│   └── geo.centers_for(pending)                      postnummer centre, and
│                                                     coord_approx = True
├── filter: drop excluded_stations (case-insensitive name match)
├── sort by name, wrap in TankpriserData
├── notifications.evaluate_and_notify(previous, current)   only if we had a
│                                                          previous snapshot
└── hass.bus.async_fire("tankpriser_price_updated", {...}) ← the card listens
```

`TankpriserData` is deliberately thin: a list of `Station` plus
`stations_for(fuel_key)` (matching stations, cheapest first) and
`cheapest(fuel_key)`. Every consumer — both sensors, the notifications, the
prediction tie-in — goes through those two methods rather than filtering the
list itself.

Then, per fuel type, `TankpriserSensor.native_value` reads
`coordinator.data.cheapest(fuel_key)` and `extra_state_attributes` builds the
station list the card renders. Sensors are pure views over `coordinator.data` —
they hold no state of their own, which is why a coordinator refresh is all it
takes to update everything.

## Walkthrough 2: a car's fuel level changes

The prediction side. One `ConsumptionTracker` per car subentry, created in
`__init__._async_setup_cars` and kept in `coordinator.cars[subentry_id]`.

```
HA state change on the car's source entity
└── consumption._handle_event(event)
    ├── _ingest_current()
    │   ├── _read(source_entity, level_attribute)     state or nested attribute
    │   │   (prediction.dig walks dotted paths into nested dicts, and
    │   │    returns None for a missing key rather than raising)
    │   ├── prediction.to_litres(value, unit, capacity_l)   %/L/gal -> litres
    │   ├── skip if the level moved less than _EPSILON_L and the odometer did
    │   │   not change  (collapses the repeats a level sensor emits)
    │   └── model.add_reading(ts, litres, odo)          -> True if this closed
    │       │                                              a tank (a refuel)
    │       └── _close_segment() when the level jumps up by at least
    │           REFUEL_MIN_JUMP_FRACTION (0.15) of the tank:
    │           the completed Segment is what the model actually learns from
    ├── Store.async_delay_save(...)   delay 0 on a refuel, else 300 s
    └── _notify()  -> every registered sensor callback -> async_write_ha_state()
```

`CarPredictionSensor` subscribes with `tracker.async_add_listener` in
`async_added_to_hass`, so it repaints on **any** source change — including a
position-only update with no fuel movement, which is what keeps the car's marker
current on the map.

The state itself comes from `tracker.predict()` → `prediction.predict(model,
current_litres)`, which returns `None` until enough tanks are learned. `None`
becomes `unknown` rather than a guess; the card shows "Estimating…" for that.

## Walkthrough 3: the card paints the map

`www/tankpriser-card.js`, one file, two custom elements. HA calls `setConfig()`
once and assigns `hass` on every state change, so `set hass` is the hot path —
everything it triggers is guarded by a change signature.

```
set hass  ->  _update()
├── _build()          once: innerHTML, the <style>, the Leaflet CSS <link>s
├── the list          _section(entityId) per entity, from sensor attributes
└── _updateMap()      only when show_map
    ├── await loadCluster() / loadLeaflet()    vendored first, CDN fallback
    ├── stations:
    │   ├── coverage "area"     -> _areaStations()      from sensor attributes
    │   └── coverage "national" -> _ensureNational()    websocket
    │                              "tankpriser/stations", then
    │                              _nationalStations() filters to the fuel key
    ├── preloadIcon(...) for the distinct chain icons
    ├── first call only: L.map(), the marker cluster group, the ◎/➤ controls,
    │   the car control, the GPS watch
    ├── _applyTiles(L)          light/dark basemap, follows the HA theme
    ├── _updateCars(L)          ALWAYS, before the early-return below
    │   ├── _allCars() -> every is_car sensor (positioned or not, hidden or not)
    │   ├── _renderCarPicker(all)      the car button + its checkbox list
    │   ├── _visibleCars(all)          positioned, and not hidden here
    │   └── per car: _addCarMarker(L, car); grouped ones get _carClusterIcon
    └── signature check on the station list -> rebuild markers, or return
```

Two details that look odd until you know why. `_updateCars` runs *before* the
station-signature early-return, because a car moving does not change the station
signature and would otherwise never be redrawn. And the map is only fitted once
(`_fitted`), so a refresh never fights the user's pan or zoom.

## Contracts

The seams between layers. Change one of these and something else breaks
silently, so they are worth knowing before editing.

### `sources.Station` — every provider normalises to this

```python
Station(
    name, company, postnummer, updated,   # updated = the chain's own stamp
    city="", address="",                  # address is what geocode.py resolves
    latitude=None, longitude=None,        # None until _fill_coordinates runs
    coord_approx=False,                   # True = a postnummer centre or a
                                          #        fuzzy-matched address
    prices={},                            # normalised fuel key -> kr, float
)
```

`Station.key` (`company|name|postnummer`, lowercased) is the identity used for
de-duplication and change detection. Adding a provider means writing a parser
that returns these — nothing downstream knows which chain a station came from
except through `company`.

### `sensor.TankpriserSensor` — one per configured fuel

State is the cheapest price. Attributes are the card's entire input in
`coverage: area` mode:

| Attribute | Used by |
| --- | --- |
| `fuel_type`, `fuel_key` | the card's headings, and the national map's fuel filter |
| `area`, `radius`, `station_count` | the list header |
| `cheapest_station`, `cheapest_price`, `average_price` | templates, automations |
| `stations[]` | `{name, company, postnummer, city, address, price, updated, latitude, longitude, coord_approx}` — the list and the area map |

### `sensor.CarPredictionSensor` — one per car subentry

State is days until refuel, or `unknown` while learning. The card finds these
sensors **by the `is_car` attribute**, not by entity_id pattern — that is the
contract that lets `_allCars()` discover cars with no configuration:

| Attribute | Notes |
| --- | --- |
| `is_car: True` | the marker for "this is a Tankpriser car" |
| `car_name`, `tank_capacity_l`, `fuel_type` | display |
| `current_level_l`, `current_level_percent` | the marker's ring colour and badge |
| `latitude`, `longitude` | **only present when known** — the card skips cars without them |
| `car_picture` | only when the source entity has one |
| `status` | `learning` or `ready`; the card branches on this, not on the state |
| `avg_consumption`, `consumption_unit`, `learned_tanks`, `confidence`, `method`, `predicted_empty` | only when `ready` |
| `cheapest_station`, `cheapest_price` | the price tie-in, for this car's fuel |
| `source_entity`, `level_attribute`, `odometer_entity` | diagnostics: trace a missing pin without guessing the config |

### `tankpriser/stations` — the websocket command

`websocket.ws_stations` returns `{stations: [{name, company, postnummer, city,
latitude, longitude, coord_approx, updated, prices{}}]}` — every station in the
country, with `prices` as the whole fuel dict rather than one price. It exists
because ~1 200 stations in a sensor attribute would bloat the state machine.
Note it does **not** take the area into account; the map viewport is the filter.

### Card config

`setConfig` normalises and defaults everything, so the rest of the card can
trust `this._config`. Full list in the file's header comment; the defaults that
surprise people are `coverage: "national"`, `show_map: false`, and `show_list`
following "shown only when the map is off".

## Module reference

What each file is for, and which function to open first.

| Module | Open first | Notes / gotchas |
| --- | --- | --- |
| `__init__.py` | `async_setup_entry`, `_async_register_card` | Card publishing is three independently-latched steps; a failed one retries on the next entry setup and on `homeassistant_started`. `async_migrate_entry` still upgrades pre-0.6 (v1) entries. |
| `const.py` | `FUEL_TYPES`, `radius_to_metres` | Pure. `FUEL_TYPES[key] = (display, unit)` is the single source of truth for which fuels exist; `strings.json` mirrors it for the UI. |
| `sources.py` | `fetch_all`, then `apply_discounts` and the `PROVIDERS` dict | One `Provider(key, label, fetch)` per chain. `_one_shot(url, parser)` covers the simple ones; OIL! needs `fetch_oil` because it is priced one fuel type per request. The cache is keyed by provider **and** a credential fingerprint, so changing a key bypasses it. |
| `geo.py` | `postnumre_within_point`, `centers_for` | DAWA takes `cirkel=lon,lat,radius` — the reversed order is a 400. `visueltcenter` is `[lon, lat]`. Batches up to 100 postnumre per request; caches for process life. |
| `geocode.py` | `apply`, then `_async_lookup` | Three passes, most precise first, each verified against the postnummer. `apply()` is cache-only and safe on every refresh; `async_schedule()` is the only thing that touches the network. |
| `coordinator.py` | `_async_update_data` | The whole price pipeline in ~40 lines. `_resolve_area` is cached per radius; `self.cars` is populated by `__init__` *after* the first refresh. |
| `nearby.py` | `rank_nearby` | Pure, no HA imports. A bounding box rejects most of the country before any trigonometry, because this runs over every station on every GPS fix. |
| `sensor.py` | `extra_state_attributes` (all three classes) | Sensors are stateless views. `CarPredictionSensor` and `NearbyStationsSensor` also subscribe outside the coordinator's cycle — to their tracker and to the nominated device respectively. `NearbyStationsSensor` exists only because no car platform will render a map: it puts the cheapest nearby station's coordinates on itself, which is what Android Auto navigates to. It ranks over `data.nationwide`, **not** the area list — the device it follows drives out of the area, and ranking inside it answered "cheapest near you" with the stations near *home*. See [IN_THE_CAR.md](IN_THE_CAR.md). |
| `websocket.py` | `ws_stations` | Not admin-only, deliberately: any user's dashboard needs it. |
| `notifications.py` | `_evaluate_fuel` | Four rules: `threshold` (fires on crossing, not while below), `decrease`, `cheapest` (any change to the cheapest), `any` (any station's price for that fuel). Compares two `TankpriserData` snapshots. |
| `config_flow.py` | `async_step_user`, then `TankpriserOptionsFlow.async_step_init` | Single entry (`async_set_unique_id(DOMAIN)`). Options is a menu: settings / notifications / chains → provider. Cars are **subentries** (`CarSubentryFlowHandler`), which is why one entry can hold several cars. |
| `consumption.py` | `_ingest_current`, `location` | The only HA-aware half of the prediction. `location` walks four fallbacks (see below); the registry walk is lazy and normally never runs. |
| `prediction.py` | `ConsumptionModel.add_reading`, `predict` | Pure, no HA imports — unit-tested directly. |
| `services.py` | `_seed`, `_reset` | Iterate `hass.data[DOMAIN][*].cars`; `seed_demo_history` fabricates tanks so the prediction can be demoed without waiting weeks. |
| `diagnostics.py` | `async_get_config_entry_diagnostics` | Redacts credentials, area name and notify target; keeps the postnummer. |
| `www/tankpriser-card.js` | `setConfig`, `_update`, `_updateMap` | See [Walkthrough 3](#walkthrough-3-the-card-paints-the-map). |

## The prediction algorithm (`prediction.py`)

Pure and HA-free, so it is unit-tested directly (see *Testing*).

### Value extraction
- `dig(obj, "a.b.c")` — follow a dotted path into nested dicts; a missing key
  returns `None` (a temporarily-absent attribute is "no reading", not a crash).
- `to_litres(value, unit, capacity)` — `percent` → `value/100 * capacity`
  (clamped 0–100 %); `litres` → the value. Non-numeric → `None`.
- `to_float(value)` — tolerant float (handles `"171191 km"`, commas, signs).

### Model (`ConsumptionModel`)
Fed normalised litre readings in time order via `add_reading(ts, litres, odo)`:
- Reading clamped to `[0, capacity]`.
- An **upward jump ≥ `REFUEL_MIN_JUMP_FRACTION` (15 %) of the tank** is a
  refuel: it closes the current tank (`_segment_start` → previous sample) into a
  `Segment` and starts a new one. Returns `True` on a refuel.
- A tank with no net consumption (a small top-up below the threshold left the
  end level ≥ the start) is discarded.
- Samples and segments are bounded (`MAX_RAW_SAMPLES`, `MAX_SEGMENTS`).

A `Segment` exposes `consumed_litres`, `duration_days`, and `distance_km`
(from the odometer deltas, if any).

### Estimator (`predict(model, current_litres)`)

Everything the estimator learns from is an `_Observation`
(`consumed_litres`, `days`, `distance_km`). Completed tanks become one each; so
does **the tank in progress**, via `_open_observation(model)`. Unifying them is
the whole trick: waiting for two refuels meant weeks of `unknown` while the data
for a rough answer was already in the model, and because the open tank is
appended *last* it carries the most EWMA weight — so the estimate keeps
calibrating between refuels, not only at them.

- Completed tanks need `duration_days ≥ MIN_SEGMENT_DAYS` (~72 min); guards
  divide-by-tiny.
- The **open tank** counts only once it has both run `EARLY_MIN_DAYS` (3 days)
  and burnt `EARLY_MIN_CONSUMED_FRACTION` (5 %) of the tank. Both gates are
  load-bearing: without the first, one long trip an hour after a fill-up is
  projected as a daily habit; without the second, a car parked for three days
  reports ~0 L/day, i.e. "empty in nine years".
- **How much the open tank counts** is decided by `_blend_daily_rate`, and this
  is what makes irregular driving safe. It is *not* appended to the EWMA — that
  gave a one-day window the same weight as a completed tank, and a single 20 L
  Saturday took a settled 12-day prediction to 3.2 days, then back to 10 once
  the car sat still for a week. Instead it is blended in proportion to the time
  it covers against a typical tank's duration:
  `rate = (1 − w)·ewma(completed) + w·open`, `w = min(1, open.days / typical)`.
  A short window nudges; a nearly-finished tank dominates, which is the point at
  which it has earned that.
- **Two tiers**, reported as `Prediction.basis` and surfaced by the sensor as
  `status`:

  | Completed tanks | `basis` | `status` | Confidence |
  | --- | --- | --- | --- |
  | ≥ `MIN_SEGMENTS_FOR_PREDICTION` (2) | `tanks` | `ready` | earned from the completed tanks |
  | fewer, open tank qualifies | `current tank` | `estimating` | capped at `EARLY_CONFIDENCE_CAP` (0.3) |
  | one, open tank does not qualify | `one tank` | `estimating` | capped |
  | none, open tank does not qualify | — | `learning` | `None` returned; state `unknown` |

- **Daily rate** = `_ewma([o.litres_per_day for o in observations])` —
  exponentially weighted, `EWMA_ALPHA = 0.5`, newest heaviest.
  `days_until_empty = current_litres / daily_rate`. Days always uses this time
  rate (the only thing that projects a calendar date).
- **Reported efficiency**: if *every* observation has an odometer distance →
  `L/100 km` (method `odometer`); else the daily rate as `L/day` (method
  `time`). `Segment.distance_km` returns `None` for a non-positive delta, so a
  stalled odometer drops the whole car back to the time method rather than
  dividing by zero.
- **Confidence** (0–1) = `min(1, n / CONFIDENCE_TARGET_SEGMENTS) × 1/(1+CV)`
  where CV is the coefficient of variation — computed over **completed tanks
  only**, so an estimate resting on one partial tank cannot look as trustworthy
  as one resting on six.

`seed_demo(now_ts, tanks, litres_per_day, days_per_tank)` fabricates segments so
a prediction appears immediately (used by the `seed_demo_history` service).

## Storage schema

### `.storage/tankpriser_consumption_<subentry_id>`

One `Store` per car: `.storage/tankpriser_consumption_<subentry_id>`, version
`STORAGE_VERSION`. Payload is the model dict plus two optional keys:

```jsonc
{
  "capacity_l": 66.0,
  "samples":  [{"ts": 1690000000.0, "litres": 23.1, "odo": 171191.0}, …],
  "segments": [{"start_ts":…, "end_ts":…, "start_litres":…, "end_litres":…,
                "odo_start":…, "odo_end":…}, …],
  "segment_start": {"ts":…, "litres":…, "odo":…} | null,
  "last_location": [56.18, 9.51],          // remembered through GPS dropouts
  "last_picture":  "https://…/passat.png"
}
```

`ConsumptionModel.from_dict` ignores unknown keys, so `last_location` /
`last_picture` are backward-compatible. Writes are debounced
(`async_delay_save`, `SAVE_DELAY_SECONDS = 300`; `0` on a refuel; a full flush on
unload). `capacity_l` comes from the live config on load, so changing the tank
size takes effect without wiping history.

### `.storage/tankpriser.geocode`

One store for the whole install (`geocode.py`), mapping a normalised
`"<address> <postnummer>"` to what DAWA said about it:

```jsonc
{
  "dronningemaen 34 svendborg 5700": {
    "lat": 55.06348, "lon": 10.60640,
    "approx": false,        // true only for a fuzzy-corrected street name
    "ts": "2026-07-25"      // last lookup; re-verified after 180 days
  },
  "motorvejen nord roskilde 4000": { "failed": "2026-07-25" }  // retried after 30
}
```

Written once per batch with `async_delay_save`. Two rules worth keeping: a
*stale* entry keeps serving its coordinates while it is re-checked, and a
re-check that cannot reach DAWA keeps the old position rather than replacing it
with a failure — otherwise one outage would demote every Q8/F24 pin back to a
postnummer centre. Unknown keys are ignored, so the format can grow.

## Coordinate resolution (`consumption.py`)

The car's position for the map is resolved in order (each falls through on miss):

1. The **source entity's** own `latitude`/`longitude` attributes.
2. Any **sibling entity on the same device** (via the entity/device registry) —
   so pointing the fuel level at a plain sensor still finds the device's
   `device_tracker`.
3. The **zone** named by the source entity's state (a parked car's tracker reads
   `home`) → that `zone.*` entity's coordinates.
4. The **last known** position (cached + persisted).

`picture` uses the same source-or-sibling `entity_picture` lookup. The registry
walk is lazy (a generator) and short-circuits, so in the normal case (source has
its own coords) it never touches the registry. The `CarPredictionSensor`
refreshes on **any** source change (not just fuel-level changes), so position
updates are picked up promptly.

## Extending

### Add a fuel chain
1. Write a parser `parse_x(payload) -> list[Station]` in `sources.py`, mapping
   the chain's product identifiers to normalised fuel keys.
2. Append one `Provider(...)` to `PROVIDERS`. For open chains that is all. For a
   keyed chain set `auth=AUTH_KEY`, the `auth_header`/`auth_template`,
   `signup_url` and a numbered `guide`; the options dialog, credential storage,
   live validation and diagnostics redaction are all driven from those fields —
   no UI or translation edits.
3. If the chain sells a fuel not yet modelled, add it to `FUEL_TYPES` first.

Credentials live in the entry **data** (`CONF_CREDENTIALS`), never the URL
(URLs are debug-logged), are redacted from diagnostics, and change the cache
fingerprint so a corrected key takes effect immediately.

### Add a fuel type
Add `"<key>": ("Display name", "kr./L")` to `FUEL_TYPES` in `const.py`, then map
each provider's product name/id onto that key in the relevant `_*_PRODUCT_MAP`
(or parser) in `sources.py`. Sensors, the options picker and the card pick it up
automatically.

### The Lovelace card (`www/tankpriser-card.js`)
Two custom elements in one file:
- `tankpriser-card` — price list + Leaflet map. Markers are HTML `divIcon`s.
  Stations and cars each get their **own marker-cluster group**: two cars at one
  spot (identical zone coordinates, the usual case at home) would otherwise hide
  each other completely, and offsetting a marker to make room just relocates the
  collision onto whatever it lands on. The car group differs from the station one
  in three options — `zoomToBoundsOnClick: false` with
  `spiderfyOnEveryZoom: true` (identical coordinates never separate by zooming,
  so one tap has to open the group at any zoom) and a tighter
  `maxClusterRadius`. Cars also live in their own map pane above the station
  pins. `_updateCars` runs *before* the station-signature early-return so
  position-only changes still refresh. Car photo is clipped to a circle by
  `.ff-car-disc { overflow:hidden; border-radius:50% }`; a car without a photo
  gets the inlined `mdi:car` path.
- `tankpriser-prediction-card` — one compact panel per car (`entities: [...]`,
  or the original single `entity:`), each rendered by `_carSection()`. The
  per-section car name only appears when there are several, because with one car
  the `ha-card` header already carries it; the donation ask is emitted once for
  the whole card by `_donate()`, unconditionally — neither card has a
  `show_donate` option any more, and an old config carrying one is ignored. Its editor picks cars with a `multiple: true`
  entity selector filtered to `device_class: duration` — the only Tankpriser
  sensors that have one.

Client-side state, deliberately not in the card config (a dashboard is shared by
everyone who can see it): the set of cars hidden on this device lives in
`localStorage` under `tankpriser.hidden_cars.<hass.user.id>`, so two accounts on
one tablet do not inherit each other's filter. A `tankpriser-cars-changed`
window event keeps two cards on one view in step.

Rules: remote strings go through `_escape`; URLs through `_safeUrl` (http/https
only); numeric config coerced with `Number(...)`. **One deliberate exception**:
the navigate links bypass `_safeUrl` because it rejects the `geo:` scheme — they
are built from coordinates and an `encodeURIComponent`'d name, never from config.
A station whose position is only estimated gets no navigate link at all, in any
mode. Leaflet + icons are vendored under `www/vendor/` with SRI on the CDN
fallback — never introduce an unpinned third-party `<script>`/`<img>` that runs
in HA's origin.

## Services

`seed_demo_history` (optional `car`, `tanks`, `litres_per_day`,
`days_per_tank`) and `reset_history` (optional `car`) iterate
`hass.data[DOMAIN][*].cars`. Schemas in `services.py`, UI form in
`services.yaml`.

## Testing & CI

Everything below runs on every push, as the **Tests** job in
`.github/workflows/validate.yml`, alongside hassfest and the HACS action. (The
`brands` check is ignored until the domain is in `home-assistant/brands`, which
only matters for the HACS default store.)

```bash
npm install                             # once: jsdom, for the card tests
npm test                                # tests/card.test.js + tests/map.test.js
python tests/test_prediction.py         # refuel detection + the estimator
python tests/test_discounts.py          # chain matching + loyalty discounts
python tests/test_geocode.py
python tests/test_card_registration.py
python tests/test_spoken.py             # the sentence Siri reads out
python tests/test_nearby.py             # ranking around a position
```

- **`tests/map.test.js`** is the one that earns its keep. It loads the real card
  with the vendored Leaflet and markercluster into a jsdom document and asserts
  what reaches the *map*: two cars apart draw two markers, two cars on identical
  coordinates draw one grouped marker, a real `MouseEvent` on that group reveals
  both cars with their legs, a lone car is drawn, hiding a car removes it.
  This exists because v0.10.0b3 shipped calling a function the same change had
  deleted: `node --check` passed and no car was ever drawn.
  Two gotchas encoded in it — `group.fire("clusterclick")` does *not* reach
  Leaflet's own handler (use a real DOM event), and jsdom has no SVG renderer, so
  spider legs exist as objects but never get their DOM class.
- **`tests/card.test.js`** covers the pure helpers: navigate URLs per platform,
  an estimated position never offered as a destination in any mode, the
  per-device car filter (including corrupt and throwing `localStorage`), the
  grouped-marker icon, and that loading the file twice is harmless.
- **Python tests need no Home Assistant.** They lift the pure functions out of
  the modules with `ast` and run them against stubs (each file's docstring
  explains the trade-off): a renamed function fails loudly rather than passing
  quietly, and CI stays at seconds instead of minutes.
- **Offline model tests**: `scratchpad/test_prediction.py` loads `const` +
  `prediction` as a synthetic package and asserts extraction, refuel detection,
  the estimator, confidence, serialisation and `seed_demo`.
  Run: `TP_BASE="$(pwd)/custom_components/tankpriser" python scratchpad/test_prediction.py`
  (`scratchpad/` is git-ignored — worth folding into `tests/` next time it is
  touched).
- **Syntax only**, when in a hurry: `python -m compileall -q custom_components/tankpriser`
  and `node --check custom_components/tankpriser/www/tankpriser-card.js`. Note
  what this cannot catch — see `map.test.js` above.

## Versioning & releases

**Every functional change bumps `manifest.json` `version`.** HACS resolves
versions from GitHub **releases**, and the card's cache-buster reads the manifest
version — a bare `main` push with no bump means HACS keeps serving the old
release and the browser keeps the old card. Workflow: bump the manifest → commit
→ push → `gh release create vX.Y.Z`. (Learned the hard way: a feature once
shipped without a bump and HACS silently reinstalled the previous release.)

`gh release create` needs the **full** commit SHA for `--target`; a short one
fails with "Release.target\_commitish is invalid".

**Betas.** Anything that can only be judged on a real phone ships as a
pre-release first: keep `manifest.json` equal to the tag (`0.11.0b1`), then
`gh release create v0.11.0b1 --prerelease`. GitHub hides it, and HACS only offers
it to a device that has *Show beta versions* enabled for this repository (the
per-repo toggle in the Redownload dialog). A stable `0.11.0` supersedes
`0.11.0b5` for beta users too, so the toggle can stay on. When the stable ships,
delete the whole beta line — `gh release delete vX.Y.ZbN --yes --cleanup-tag` —
so nobody can install a superseded (or broken) beta from the version dropdown;
the commits stay on `main` either way.

**Changes with no user-visible effect** (dead-code removal, tests, docs) are
committed to `main` **untagged**. HACS installs from release tags, so they reach
users with the next real release instead of prompting an update for nothing.

## Where to look if…

| Symptom | Look at |
| --- | --- |
| A chain's prices are missing entirely | `sources.PROVIDERS` → that chain's parser (`parse_ok` / `parse_q8` / `parse_shell` / `fetch_oil`). Check the endpoint by hand first — a large JSON body means the chain is fine and the parser drifted |
| All prices are missing | `coordinator._async_update_data` raises `UpdateFailed` when every provider returned nothing; `sources._fetch_provider` decides when a provider is dropped for staleness |
| A station is in the wrong place | `geocode._async_lookup` (the three passes) then `geo.centers_for` (the postnummer-centre fallback). `coord_approx` tells you which one placed it |
| A station has no navigate button | By design when `coord_approx` is true — `_navHtml` in the card |
| The area contains the wrong postnumre | `coordinator._resolve_area` → `geo.postnumre_within_point`. DAWA wants `cirkel=lon,lat,radius`; reversed is a 400 |
| A car does not appear on the map | In order: does the sensor have `latitude`/`longitude`? (`consumption.location` and its four fallbacks) → is it hidden on this device? (`localStorage`, `tankpriser.hidden_cars.<user id>`) → is it grouped with another car? (`_carClusterIcon`) |
| The days-until-refuel sensor stays `unknown` | `prediction.predict` returns `None` until enough tanks are learned; `attrs["status"]` says `learning`. `services.seed_demo_history` fabricates tanks to test the rest |
| A refuel was missed or invented | `ConsumptionModel.add_reading` / `_close_segment`, and `REFUEL_MIN_JUMP_FRACTION` in `const.py` |
| Consumption looks wrong | `prediction._ewma` (weighting) and `_confidence`; `Segment.consumed_litres` / `duration_days` / `distance_km` are the raw inputs |
| Notifications fire too often / never | `notifications._evaluate_fuel` — one branch per rule |
| The card renders "Configuration error" | The card script never loaded on that client: `__init__._async_register_card`, and [ARCHITECTURE.md](ARCHITECTURE.md#how-the-card-reaches-the-browser) |
| The card shows stale prices | `_mapSig` / `_carSig` early-returns in `_updateMap` / `_updateCars`, and the `tankpriser_price_updated` subscription in `_subscribeUpdates` |
| The map is empty but the list is fine | Leaflet did not load: `loadLeaflet` / `loadCluster` (vendored first, CDN with SRI as fallback), or no station has coordinates |
| A new config option is ignored | `config_flow` writes it, but `coordinator`/`sensor` read through the properties on `TankpriserCoordinator` — add it there too, and remember options changes trigger a reload via `_async_reload_entry` |
| HACS shows an old version | `manifest.json` version was not bumped — see [Versioning & releases](#versioning--releases) |

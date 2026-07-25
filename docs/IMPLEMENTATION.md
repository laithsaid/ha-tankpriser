# Tankpriser — Implementation notes

Developer-facing detail. For the big picture read
[ARCHITECTURE.md](ARCHITECTURE.md) first.

## Layout

```
custom_components/tankpriser/
  __init__.py        setup / unload / migration, registers card + ws + services
  const.py           constants (pure — no HA imports)
  sources.py         fuel-price providers + normalisation + cache
  geo.py             DAWA area resolution + postnummer centres
  geocode.py         DAWA address -> coordinates for Q8/F24 (disk-cached)
  coordinator.py     per-entry DataUpdateCoordinator (+ .cars trackers)
  sensor.py          TankpriserSensor, CarPredictionSensor
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
  test_geocode.py    address parsing + geocode cache policy
  test_card_registration.py   the Lovelace resource registration
package.json         jsdom, for the tests only — the integration ships no JS deps
scratchpad/          local-only experiments (git-ignored)
```

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
- Uses only segments with `duration_days ≥ MIN_SEGMENT_DAYS` (~72 min); guards
  divide-by-tiny.
- `< MIN_SEGMENTS_FOR_PREDICTION` (2) usable tanks → returns `None`
  (cold start; the sensor is `unknown`).
- **Daily rate** = `_ewma(consumed / duration_days)` — exponentially weighted,
  `EWMA_ALPHA = 0.5`, recent tanks heavier. `days_until_empty =
  current_litres / daily_rate`. Days always uses this time rate (the only thing
  that projects a calendar date).
- **Reported efficiency**: if *every* usable tank has an odometer distance →
  `L/100 km` (method `odometer`); else the daily rate as `L/day` (method
  `time`).
- **Confidence** (0–1) = `min(1, n / CONFIDENCE_TARGET_SEGMENTS) × 1/(1+CV)`
  where CV is the coefficient of variation of the rates — more tanks and more
  consistent tanks raise it.

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
- `tankpriser-prediction-card` — a compact per-car panel with the donation ask.

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
python tests/test_geocode.py
python tests/test_card_registration.py
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

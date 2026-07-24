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
tests/dev:           scratchpad/test_prediction.py  (offline, ignored by git)
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
  Stations use a marker-cluster group; **cars use a separate un-clustered
  layer** (`_updateCars`) drawn *before* the station-signature early-return so
  position-only changes still refresh. Car photo is clipped to a circle by
  `.ff-car-disc { overflow:hidden; border-radius:50% }`.
- `tankpriser-prediction-card` — a compact per-car panel with the donation ask.

Rules: remote strings go through `_escape`; URLs through `_safeUrl` (http/https
only); numeric config coerced with `Number(...)`. Leaflet + icons are vendored
under `www/vendor/` with SRI on the CDN fallback — never introduce an
unpinned third-party `<script>`/`<img>` that runs in HA's origin.

## Services

`seed_demo_history` (optional `car`, `tanks`, `litres_per_day`,
`days_per_tank`) and `reset_history` (optional `car`) iterate
`hass.data[DOMAIN][*].cars`. Schemas in `services.py`, UI form in
`services.yaml`.

## Testing & CI

- **Offline model tests**: `scratchpad/test_prediction.py` loads `const` +
  `prediction` as a synthetic package (no HA needed) and asserts extraction,
  refuel detection, the estimator, confidence, serialisation and `seed_demo`.
  Run: `TP_BASE="$(pwd)/custom_components/tankpriser" python scratchpad/test_prediction.py`
  (`scratchpad/` is git-ignored).
- **Syntax**: `python -m py_compile custom_components/tankpriser/*.py` and
  `node --check custom_components/tankpriser/www/tankpriser-card.js`.
- **CI** (`.github/workflows/validate.yml`): hassfest + HACS action on every
  push. The `brands` check is ignored until the domain is in
  `home-assistant/brands` (only required for the HACS default store).

## Versioning & releases

**Every functional change bumps `manifest.json` `version`.** HACS resolves
versions from GitHub **releases**, and the card's cache-buster reads the manifest
version — a bare `main` push with no bump means HACS keeps serving the old
release and the browser keeps the old card. Workflow: bump the manifest → commit
→ push → `gh release create vX.Y.Z`. (Learned the hard way: a feature once
shipped without a bump and HACS silently reinstalled the previous release.)

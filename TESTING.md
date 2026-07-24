# Testing Tankpriser

A practical checklist for verifying the integration on Home Assistant OS /
Supervised. Work top to bottom — each step builds on the previous one.

---

## 0. Where the prices come from

As of **v0.2.0** the integration no longer scrapes fuelfinder.dk (whose radius
search went dead). It aggregates the **free, open, per-station price APIs** that
Danish fuel chains are required by law to publish since 2026-01-01:

| Provider | Endpoint | Auth | Notes |
| --- | --- | --- | --- |
| OK | `mobility-prices.ok.dk/api/v1/fuel-prices` | none | ~690 stations; ships exact lat/long |
| Q8 + F24 | `beta.q8.dk/Station/GetStationPrices?page=1&pageSize=2000` | none | ~240 stations; address carries the postnummer, no coordinates |
| Shell | `shellpumpepriser.geoapp.me/v1/prices` | none | ~210 stations; ships exact lat/long |
| OIL! | `apim-fuel-prices-prod.azure-api.net/Oil-FuelPrices/prices?fuelType=…` | none | ~70 stations; ships lat/long; one request per fuel type, merged by station |

Geography is done locally: the configured **postnummer + radius** is resolved
into a set of postnumre via **DAWA** (`api.dataforsyningen.dk`, free, no key),
and stations are filtered by `postnummer ∈ set`. Stations without provider
coordinates (Q8/F24) are placed at their postnummer's centre for the map and
flagged as approximate (`coord_approx: true`, shown as `≈`).

There is **no WAF, throttle, or rate-limit to worry about** anymore, and no
browser geolocation. You can sanity-check a provider by opening its URL above in
a browser — a large JSON body means it's healthy.

Three more chains exist but need a personal credential you must obtain yourself,
so they are **not wired in yet** (see the "Outstanding" section at the bottom):
Go'on (apply for an API key), Circle K/INGO (email for access) and Uno-X (token).
Adding each is just another parser in `sources.py` once the credential exists.

**Health check:** just install and add an area (section 2). If the sensor's
`stations` attribute fills, the whole pipeline works.

---

## 1. Install the integration for testing (Home Assistant on Proxmox)

### 1a. First identify which HA flavour you're running

The install path depends on it. In HA go to **Settings → About** (or
**Developer Tools → Template** and render `{{ states('sensor.x') }}`… easier:
**Settings → System → Repairs → ⋮ → System information**).

Read the **Installation type** row:

| Installation type | What you have | Use section |
| --- | --- | --- |
| `Home Assistant OS` | HAOS in a Proxmox **VM** (the usual community-script install) | **1b** |
| `Home Assistant Container` | Docker in a Proxmox **LXC** — no Add-on store | **1c** |
| `Home Assistant Supervised` | Debian VM/LXC + Supervised | **1d** |

Quick cross-check from the **Proxmox host shell**: `qm list` shows VMs (HAOS
usually appears as `haos` / `homeassistant`), `pct list` shows LXC containers.
If HA shows up under `pct list`, you're on 1c or 1d.

---

### 1b. HAOS in a Proxmox VM

You have the Add-on store, so you never touch the Proxmox host for this.

**Option A — manual copy (fastest for iterating)**

1. **Settings → Add-ons → Add-on Store** → install **Studio Code Server**
   (or **Samba share** if you'd rather drag files from Windows Explorer).
2. Create the folder `/config/custom_components/` if it doesn't exist, then copy
   this repo's `custom_components/tankpriser/` into it so you end up with
   `/config/custom_components/tankpriser/manifest.json` etc.
   - With Samba: the share appears as `\\<ha-ip>\config` — paste the
     `tankpriser` folder into `config\custom_components\`.
   - With Studio Code Server: right-click in the explorer pane → **Upload…**
3. **Settings → System → ⋮ → Restart Home Assistant**.

**Option B — via HACS (the real install path)**

1. Push this repo to `https://github.com/laithsaid/ha-tankpriser` (public).
2. HACS → **⋮ → Custom repositories** → add that URL, category **Integration**.
3. Install **Tankpriser** → **Restart Home Assistant**.

> Take a **Proxmox snapshot** of the HA VM before the first install
> (select the VM → **Snapshots → Take Snapshot**). If a custom component wedges
> HA at boot, rolling back is a 10-second fix.

---

### 1c. HA Container (Docker) in a Proxmox LXC

No Add-on store, so no Samba/Studio Code Server — copy files in from the LXC
shell instead. HACS also has to be installed manually here.

1. From the Proxmox web UI, open the LXC's **Console** (or
   `pct enter <ctid>` from the host shell).
2. Find where the config volume lives on the host side:

   ```bash
   docker inspect homeassistant --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
   ```

   The line ending in `-> /config` is your config directory (commonly
   `/opt/homeassistant/config` or `/root/config`). Call it `$CFG`.
3. Get the files in. Easiest is straight from GitHub once the repo is pushed:

   ```bash
   CFG=/opt/homeassistant/config          # ← replace with what step 2 printed
   mkdir -p "$CFG/custom_components"
   cd /tmp
   git clone https://github.com/laithsaid/ha-tankpriser
   cp -r ha-tankpriser/custom_components/tankpriser "$CFG/custom_components/"
   ```

   Not pushed yet? From your Windows machine instead:

   ```bash
   scp -r custom_components/tankpriser root@<lxc-ip>:/opt/homeassistant/config/custom_components/
   ```

4. Restart the container:

   ```bash
   docker restart homeassistant
   ```

> HACS on HA Container needs the one-line installer run **inside** the HA
> container (`docker exec -it homeassistant bash` → the wget installer from
> hacs.xyz), then a restart. For testing Tankpriser the manual copy above is
> enough — HACS is only worth setting up if you want the update path.

---

### 1d. HA Supervised in an LXC/VM

You have the Add-on store, so **section 1b applies as-is** — install Studio Code
Server or Samba and copy into `/config/custom_components/`.

If you'd rather work from the host shell, the same directory is at
`/usr/share/hassio/homeassistant/custom_components/`:

```bash
mkdir -p /usr/share/hassio/homeassistant/custom_components
cp -r custom_components/tankpriser /usr/share/hassio/homeassistant/custom_components/
ha core restart
```

---

### 1e. Sanity-check the copy landed

Whichever route you took, confirm before restarting that the folder contains at
minimum:

```
custom_components/tankpriser/
  __init__.py  manifest.json  const.py  config_flow.py  coordinator.py
  geo.py  sources.py  notifications.py  sensor.py  strings.json
  translations/  www/
```

Don't copy `__pycache__/` if it came along — harmless, but stale.

A common failure is nesting it one level too deep
(`custom_components/tankpriser/tankpriser/…`) — HA will silently not find it.

> After any change to `.py` files you must **restart HA**. Changes to *options*
> (radius, filters, notifications) only need an entry **Reload**, not a restart.

---

## 2. Add an area and check it loads

1. **Settings → Devices & Services → Add Integration → Tankpriser**.
2. Enter postnummer `8600`, radius `10 km`, tick **Blyfri 95 (E10)** and
   **Diesel (B7)** → **Submit**.
3. You should get a **Tankpriser 8600** device with sensors.

For 8600 Silkeborg at 10 km this yields ~21 stations (OK, Q8, F24, Shell and
OIL!). If setup fails with "not ready", a provider or DAWA was briefly
unreachable — Reload the entry and check the logs (step 6).

---

## 3. Verify the sensor data

**Developer Tools → States**, filter `tankpriser` (or `sensor.8600`):

- State = the cheapest price, e.g. `16.79`.
- Expand **Attributes** — you should see:
  - `stations`: a list of `{name, company, postnummer, city, address, price,
    updated, latitude, longitude, coord_approx}`
  - `cheapest_station`, `cheapest_price`, `average_price`, `station_count`,
    `postnummer`, `radius`.

If `stations` is populated, the whole pipeline (providers → DAWA filter →
normalize) works end-to-end. 🎉

**Force a refresh without waiting 30 min:** Settings → the Tankpriser integration
→ **⋮ → Reload**. (This re-fetches once.)

---

## 4. Add the dashboard card

1. Edit a dashboard → **+ Add Card** → search **“Tankpriser Prices”**.
   - If it doesn't appear, hard-refresh the browser (Ctrl+F5) to clear the old
     JS, then retry. The card is auto-registered by the integration.
2. Configure it:

```yaml
type: custom:tankpriser-card
title: Fuel near 8600
show_map: true          # optional; plots the stations on a map
map_height: 320         # optional px
entities:
  - sensor.8600_blyfri_95_e10
  - sensor.8600_diesel_b7
```

You should see a per-station table with the cheapest row highlighted, and a
donate line in the footer. (Entity ids may differ — copy the real ones from
Developer Tools → States.)

**Map (`show_map: true`):** stations are drawn as coloured dots — green =
cheapest, blue = normal, amber/semi-transparent = approximate location (a
Q8/F24 station shown at its postnummer centre). Click a dot for prices. The map
uses Leaflet + OpenStreetMap loaded from a CDN, so it needs internet from the
browser viewing the dashboard; the price table works offline regardless.

---

## 5. Test notifications

1. Integration → **Configure**.
2. Set **Send notifications** on, pick a **Notify service** (e.g.
   `notify.mobile_app_<yourphone>` or `notify.persistent_notification` for a
   quick test), choose rule **“Any price change in the area”**, Submit.
3. Trigger it: the simplest reliable test is rule **any change** — wait for the
   next interval when the source updates, or temporarily set a **threshold**
   just above the current cheapest price with the **below threshold** rule and
   Reload; on the next fetch you should get a notification.
4. For automations, watch **Developer Tools → Events**, listen to
   `tankpriser_price_updated`, and Reload the entry — you should see the event
   fire with `station_count` etc.

> Tip: `notify.persistent_notification` shows the message in the HA sidebar bell,
> which is the fastest way to confirm the wiring without a phone.

---

## 5b. Test the fuel-consumption prediction (per car)

1. Integration → **Add car** (a config *subentry* — you can add as many cars as
   you want). Point **Fuel-level entity** at anything that reports a level; for
   a quick wiring test, create an `input_number` helper (0–100) and use it with
   **Level unit = Percent** and a **Tank capacity** (e.g. 50 L). A real test
   uses your car's own fuel-level entity/attribute (e.g. a `device_tracker`
   attribute `fuel_level`).
2. A **`sensor.<car>_days_until_refuel`** should appear (its own device named
   after the car). Immediately after adding, its state is `unknown` with
   attribute **`status: learning`** — this is correct: it never guesses before
   it has learned a couple of tanks.
3. **Verify the live wiring now** (no waiting needed): in **Developer Tools →
   States**, change the source entity and confirm the sensor's
   `current_level_percent` / `current_level_l` attributes track it, and that
   `cheapest_station` / `cheapest_price` show the cheapest nearby price for the
   car's fuel. Restart HA and confirm the car + sensor survive (history is
   stored in `.storage/tankpriser_consumption_<id>`).
4. **The numeric prediction needs real time.** Each learned "tank" is measured
   from one refuel to the next, and tanks shorter than ~1 hour are ignored to
   avoid nonsense — so a few rapid manual changes will *not* produce a number;
   real car usage over days will. The prediction maths itself is covered by the
   offline test suite (`scratchpad/test_prediction.py`, 39 assertions). To force
   a quick end-to-end number for testing only, temporarily lower
   `MIN_SEGMENT_DAYS` and `MIN_SEGMENTS_FOR_PREDICTION` in `const.py`, then
   simulate: set the level high, wait, lower it (consumption), then raise it by
   >15% of the tank (a refuel) — twice — and the sensor should switch to
   `status: ready` with `days_until_empty`, `avg_consumption`, `confidence`.
5. **Prediction card:** add a **Tankpriser Prediction** card from the picker (or
   `type: custom:tankpriser-prediction-card`, `entity:
   sensor.<car>_days_until_refuel`). It shows the tank gauge, the headline, and
   a dismissible donation ask (`show_donate: false` to hide it).

> With an **odometer** entity configured, consumption is reported in L/100 km;
> without one, it falls back to a time-based estimate. Either way the
> days-until-refuel projection needs real elapsed time to learn.

---

## 6. Reading logs / debugging

Add to `configuration.yaml` (then restart):

```yaml
logger:
  default: warning
  logs:
    custom_components.tankpriser: debug
```

Then **Settings → System → Logs** (or `/config/home-assistant.log`). Look for
lines from `custom_components.tankpriser`. Common messages:

| Symptom | Meaning / fix |
| --- | --- |
| `No data returned from any fuel-price provider` | Both providers unreachable at once (transient); it retries next interval |
| `Provider q8/shell failed: …` | One provider was down/changed — the other still populates; report if persistent |
| `DAWA radius lookup failed … using postnummer only` | DAWA was unreachable; falls back to just your postnummer until it recovers |
| `Area 8600 10 km -> N postnumre` | Normal debug line confirming the radius resolved |
| No sensors created | No fuel types selected |
| Card missing from picker | Browser cache — hard refresh; check the resource loaded (Dev console: “TANKPRISER-CARD loaded”) |
| Map empty / “Map unavailable” | Browser has no internet to load Leaflet, or no stations have coordinates |

---

## 7. (Optional) Validate the repo like CI does

If you have Docker, you can run the same checks GitHub Actions runs:

```bash
# hassfest (structure/manifest validation) and HACS validation run automatically
# on push via .github/workflows/validate.yml — just check the Actions tab is green.
```

Locally you can at least compile-check:

```bash
python -m py_compile custom_components/tankpriser/*.py
```

---

## Outstanding (not done yet)

Tracked here and in the project notes so nothing is lost while testing:

- [ ] **Live verification in your HA** — nothing has run in a real Home Assistant
      yet; this test pass is that step.
- [ ] **Not committed to git** — the working tree is untracked; commit once the
      live test passes.
- [ ] **More chains (need your credential):**
  - Go'on — apply for an API key on **goon.nu** (auto-issued by email). Then I
    add an optional "Go'on API key" field + parser.
  - Circle K / INGO — email **fueldkapi@circlekeurope.com** for access; they
    suggest waiting for their modernized API.
  - Uno-X — needs a bearer token and endpoint discovery.
- [ ] **Real donate link** — the card/README still use a placeholder (the repo
      URL); replace with a real Ko-fi/MobilePay handle when you have one.
- [ ] **Optional: exact geocoding for Q8/F24** — they ship no coordinates, so
      they're pinned at their postnummer centre (flagged `≈`). Could geocode
      their addresses via DAWA for exact map pins.
- [ ] **Optional: rename the GitHub repo** — code says `Tankpriser`, but the repo
      is still `ha-tankpriser`; if you rename it, tell me to update the URLs.

### Backlog (feature ideas)

- [ ] **EV charging stations + prices** — check which chains expose charging
      (Q8 already returns an `HPC` kWh product we currently skip); decide how to
      model kr./kWh alongside kr./L, likely as a separate sensor/group.
- [ ] **OIL! station icon** — confirm the OIL! favicon renders well on the map;
      if not, source a better official OIL! icon (applies to any chain whose
      favicon looks poor).
- [ ] **Filter stations off the map** — extend the existing "Hide these
      stations" option so hidden stations are also removed from the map,
      including the national (websocket) view, not just the price list.

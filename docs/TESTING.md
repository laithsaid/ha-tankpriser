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
coordinates (Q8/F24) have their **street address geocoded** against the same
DAWA — all 241 resolved when last measured (183 on the exact house number, 45 at
street level, 13 via a fuzzy pass) — so they sit on the real forecourt. The
lookups run once per install, in the background, and are cached in
`.storage/tankpriser.geocode` for good. Whatever DAWA cannot match still falls
back to the postnummer centre, flagged approximate (`coord_approx: true`, shown
as `≈` and a dashed pin).

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
  geo.py  geocode.py  sources.py  notifications.py  sensor.py  strings.json
  consumption.py  prediction.py  services.py  services.yaml  diagnostics.py
  websocket.py  translations/  www/
```

Don't copy `__pycache__/` if it came along — harmless, but stale.

A common failure is nesting it one level too deep
(`custom_components/tankpriser/tankpriser/…`) — HA will silently not find it.

> After any change to `.py` files you must **restart HA**. Changes to *options*
> (radius, filters, notifications) only need an entry **Reload**, not a restart.

---

## 2. Add the integration and check it loads

The area is your **HA Home location** — the setup form no longer asks for a
postnummer. Set the Home location first (**Settings → System → General**),
otherwise there is no area to search and no stations will be found.

1. **Settings → Devices & Services → Add Integration → Tankpriser**.
2. Leave the name blank (or give it one — it becomes the device name and the
   notification title), tick **Blyfri 95 (E10)** and **Diesel (B7)** →
   **Submit**.
3. You should get a **Tankpriser** device with one sensor per fuel.
4. **Configure** → **Area & fuel types** to set the radius; it defaults to 10 km.

For a Home location in Silkeborg at 10 km this yields ~21 stations (OK, Q8, F24,
Shell and OIL!). If setup fails with "not ready", a provider or DAWA was briefly
unreachable — Reload the entry and check the logs (step 6).

> Entries created before v0.11 still carry their stored postnummer and keep using
> it; only new entries are Home-based. Both are supported on purpose.

---

## 3. Verify the sensor data

**Developer Tools → States**, filter `tankpriser`:

- State = the cheapest price, e.g. `16.79`.
- Expand **Attributes** — you should see:
  - `stations`: a list of `{name, company, postnummer, city, address, price,
    list_price, discount_ore, updated, latitude, longitude, coord_approx}`,
    **sorted cheapest first**
  - `cheapest_station`, `cheapest_price`, `average_price`, `station_count`,
    `discounted`, `area`, `radius`, `fuel_type`, `fuel_key`.

If `stations` is populated, the whole pipeline (providers → DAWA filter →
normalize) works end-to-end. 🎉

Two things worth checking here, because everything downstream trusts them:

- **`coord_approx`** should be `true` on some Q8/F24 stations and `false` on the
  rest. All-true means geocoding never ran; all-false is suspicious.
- **Ordering.** The card renders `stations` in the order given, so if the first
  entry is not the cheapest, the bug is here and not in the card.

The `…_cheapest_nearby` sensors only exist once you nominate a device under
**Configure → Area & fuel types**; testing those is
the [README's in-car section](../README.md#11-setting-up-the-in-car-sensors).
Three checks belong here, though, because they
are what makes an in-car answer right or wrong:

- **The pool is national.** These rank against every station in Denmark, not the
  area the price sensors cover. Drive (or move the tracked device) well outside
  your radius and the sensor must name stations *there*. If it still lists the
  ones at home, read the next check before assuming the ranking is broken.
- **`origin_latitude` / `origin_longitude` / `origin_source`** say where the
  ranking was measured from. Compare them with the tracked device's own
  `latitude`/`longitude`: they should match, and `origin_source` should read
  `tracker`. `zone:home` means the device reported a zone name instead of
  coordinates; `none` means it had no position at all, and the sensor then has
  no stations.
- **`position_updated`** is when that device last told Home Assistant anything.
  A phone whose companion app has stopped reporting answers confidently about
  wherever it last checked in — this attribute is the only way to see that from
  the outside.

**Force a refresh without waiting 30 min:** Settings → the Tankpriser integration
→ **⋮ → Reload**. (This re-fetches once.)

---

## 4. The dashboard cards

Two cards ship **inside** the integration — no HACS frontend repository, no
`resources:` entry to add by hand:

| Card | Type | What it is |
| --- | --- | --- |
| **Tankpriser Prices** | `custom:tankpriser-card` | The price table, the map, or both |
| **Tankpriser Prediction** | `custom:tankpriser-prediction-card` | One car's refuel forecast |

The price card is what people look at every day, so it gets the most attention
here. Work 4a → 4j in order the first time; afterwards each part stands alone.
Every option mentioned is listed in the README's card-options table if you want
the one-line version.

> **Test on at least two clients** — a desktop browser *and* the mobile app. They
> receive the card by two different routes (a `<script>` tag injected into
> `index.html`, and a Lovelace resource fetched over the websocket), and only one
> of them fails at a time. A card that works on your laptop can still be a red
> "Configuration error" box on your phone.

---

### 4a. Get it on screen

1. Edit a dashboard → **+ Add Card** → stay on the **By card** tab → type `tank`.

   The dialog also has a **By entity** tab; custom cards do not appear there.
   Searching "tank" on that tab finds your `sensor.tankpriser_*` entities and
   builds a generic Entities card instead, which is not what you are testing.

   **Pass:** both cards are listed, **each exactly once**. Listed twice means a
   leftover hand-added resource pointing at a second copy of the file — check
   **Settings → Dashboards → ⋮ → Resources**.

2. Pick **Tankpriser Prices**. It must open a **visual editor** (a form with
   labelled fields), not a raw YAML box, and it should have pre-filled an entity
   it found by itself.

3. Open the browser console (F12).

   **Pass:** exactly one `TANKPRISER-CARD loaded` line. Two means the file is
   being loaded twice — harmless by design, but worth fixing.

Not in the picker at all? Hard-refresh (Ctrl+F5); on the mobile app, fully close
and reopen it (pull-to-refresh does *not* refetch `index.html`). Then check the
Resources list holds `/tankpriser/tankpriser-card.js?v=<version>` with a version
matching `manifest.json`.

---

### 4b. The price list, without the map

This is the offline half: with `show_map: false` the card makes **no external
request at all**. Testing it separately is also how you prove that the map's
OpenStreetMap tiles are the only thing that ever leaves your network.

```yaml
type: custom:tankpriser-card
title: Fuel near home
show_map: false
entities:
  - sensor.tankpriser_blyfri_95_e10
  - sensor.tankpriser_diesel_b7
```

(Entity ids differ if you named the integration something other than
"Tankpriser" — copy the real ones from Developer Tools → States.)

| Check | Expect |
| --- | --- |
| Sections | One block per entity, in config order |
| Block header | Left: the fuel's display name, e.g. `Blyfri 95 (E10)`. Right: `Home · 10 km · 21 st.` — area, radius, and how many stations sell *that* fuel |
| Row order | **Cheapest first.** The sensor's `stations` attribute is already sorted by price; the card does not re-sort, so a wrong order means the sensor is wrong, not the card |
| Cheapest row | Visibly highlighted |
| Price format | Two decimals plus the unit, e.g. `16,79 kr./L` |
| Row subtext | `8600 Silkeborg · 2026-07-26` under the station name — postnummer and town, then the chain's own "prices last changed" date where it supplies one. A station with no date shows the town alone, with no dangling `·` |
| Distance | Above each price: `3,2 km`, or metres below a kilometre (`450 m`). A row whose position is only estimated keeps its single `≈` beside the name — the distance is not marked a second time |
| Where it is measured from | The block header ends with `· from home` on a card with no map. Sanity-check one row against Google Maps from your house — it is a straight-line distance, so expect it to be *shorter* than the drive |
| `≈` after a name | Hover → "Approximate location (postnummer centre)". Expect these on some Q8/F24 rows and nowhere else |
| Footer | "Enjoying this card? **Support the project ♥**" → `paypal.me/tankpriser` |

Then vary one thing at a time:

| Change | Expect |
| --- | --- |
| `highlight_cheapest: false` | Highlight gone, order unchanged |
| `max_stations: 5` | Exactly 5 rows — the **5 cheapest**, not the first 5 by name |
| `sort: distance` | **Nearest first**, price still shown and the cheapest still highlighted — so the highlight is now somewhere down the list. With `max_stations` set, the cap is applied *after* sorting: the 5 nearest, not the 5 cheapest |
| `show_distance: false` | No distances, and the header stops saying `from home`. `sort: distance` then does nothing — nothing is measured — and the editor stops offering it |
| `title` removed | No card header; the per-fuel block headers remain |
| `show_donate: false` | **Nothing** — the footer stays. The ask is not a setting, and an old config carrying this must not remove it |
| `donate_url: https://example.com` | **Nothing** — the footer still points at `paypal.me/tankpriser`. The destination is not a dashboard setting either |
| A bad entity id | `Unknown entity: sensor.nope` |
| Every station hidden (Configure → Hide these stations) | `No prices available.` |

**Cross-check the card against the sensor.** In Developer Tools → States for the
same entity: the card's first row must match `cheapest_station` /
`cheapest_price`, and the header count must equal `station_count`. A mismatch
means the card is rendering a stale state.

---

### 4c. Loyalty discounts in the list

Discounts are applied in the backend, so the card needs no special handling —
this is how you prove that.

1. Configure → **Loyalty discounts** → set **OK** to `20` → Submit. The entry
   reloads itself.
2. Watch any OK station's row.

| Check | Expect |
| --- | --- |
| The price | Lower by exactly 0,20 kr |
| A `−20` badge | Immediately before the price, on discounted rows only |
| Badge tooltip | `Pumpepris 16,99 · rabat 20 øre` |
| Non-OK rows | Untouched |
| The cheapest row | **May move** — the cheapest-of is computed on what *you* pay. That is the whole point of the feature, not a bug |
| Sensor attributes | `discounted: true`; the discounted stations carry `list_price` and `discount_ore` |

Set it back to `0` afterwards unless you really hold that card — every other
test in this document reads prices.

---

### 4d. The map, `coverage: area`

```yaml
type: custom:tankpriser-card
show_map: true
coverage: area
show_list: true
map_height: 320
entities:
  - sensor.tankpriser_blyfri_95_e10
```

| Check | Expect |
| --- | --- |
| First paint | The map fits all its markers. It must **not** open on the whole world or on 0,0 |
| Marker count | Matches the list, minus any station that could not be placed at all |
| Marker content | The chain's logo plus the price. A chain with no bundled icon falls back to a coloured letter code — both are correct |
| Cheapest marker | Green border, green price |
| Approximate markers | **Dashed** border |
| Clusters | Nearby markers merge into one showing the **lowest** price inside it, a few chain icons, `+N` when there are more, and the count |
| Zoom in | Clusters break down to individual forecourts |
| `cluster: false` | Every station is its own marker, overlaps included |
| `map_height: 320` | 320 px tall — and changing it takes effect without a browser reload |
| `map_theme: dark` / `light` | Tiles switch (CARTO dark vs OSM standard) |
| `map_theme: auto` | Follows the HA theme — switch your theme and confirm |
| `show_list: false` | Map only, no table |
| Prices update | Reload the entry (⋮ → Reload). The map repaints **without** a browser refresh — it listens for `tankpriser_price_updated` |

**Prove the map is served locally:** with the card open, DevTools → Network,
filter `leaflet`. Every hit must come from your own HA origin under
`/tankpriser/vendor/`. Anything from `unpkg`, `cdn…` or similar is a bug.

---

### 4e. The map, `coverage: national`

The default. A different data path entirely: the station list arrives over the
websocket instead of from a sensor attribute.

```yaml
type: custom:tankpriser-card
show_map: true
coverage: national
map_theme: dark
entities:
  - sensor.tankpriser_blyfri_95_e10   # the fuel to show nationwide
```

Use a **Panel** view so it gets full width.

| Check | Expect |
| --- | --- |
| Station count | Far more than your area — around a thousand nationwide, depending on which chains sell that fuel |
| Which fuel | `fuel:` if set, otherwise the first entity's `fuel_key` attribute |
| Zoomed out over Denmark | Clusters across the country, each labelled with its own lowest price |
| Pan to another region | Its stations are there. **Your radius does not apply here** — this is the single most misunderstood behaviour in the integration |
| Network tab | **No** HTTP request for the station list; it arrives over the existing `/api/websocket` connection |
| Quick reload | Near-instant — the payload is memoised for 60 s server-side |
| The table underneath | Still **your area only**. The table comes from the sensor, the map from the national payload. Not a bug |
| Map with no table at all | `fuel: blyfri95`, `show_map: true`, no `entities:` — the map paints on its own. Add `show_list: true` to that and you correctly get `No Tankpriser sensor found.` |

---

### 4f. Station popups and navigation

Tap any marker.

| Check | Expect |
| --- | --- |
| Header | Station name, then its city on the next line |
| Fuels | **Every** fuel that station sells with its price — not only the fuel the map is showing |
| Timestamp | `Priser opdateret: …` where the provider supplies one |
| Discount line | `Pumpepris 16,99 · din rabat 20 øre` when a discount applies |
| `➤ Navigér hertil` | Present on exactly-placed stations. Tap it: Android → the system app chooser; iPhone/iPad → Apple Maps; desktop → Google Maps in a new tab |
| A dashed `≈` marker | **No navigate button.** Instead: `≈ Placeringen er kun anslået, så der kan ikke navigeres præcist hertil.` |
| `navigation: google` | Google Maps on every platform, including phones |
| `navigation: apple` / `osm` / `geo` | The forced target, likewise |
| `navigation: off` | No button anywhere — but the `≈` explanation still appears |

**Verify the coordinates are actually right,** not just present: pick two
stations you know personally — ideally one Q8 or F24, since those are geocoded
rather than provider-supplied — and confirm the navigator lands within a street
of the real forecourt.

---

### 4g. Your position, ◎ and ➤

**Requires HTTPS** (or `localhost`). Browsers disable geolocation on plain
`http`, and no amount of card configuration works around it.

| Step | Expect |
| --- | --- |
| Load the card with the map on | The browser asks for location permission, once per origin |
| Allow | A blue dot appears at your position and follows you as you move |
| Tap **◎** | Recentres on you once. Panning afterwards is *not* fought |
| Tap **➤** | Turns solid blue; the map now recentres on every new fix |
| Pan by hand while ➤ is armed | ➤ disarms itself — this is the escape hatch |
| `follow_me: true` | Armed already on load |
| Deny permission (or use `http`) | No dot, ever. **◎ falls back to your HA Home location** rather than doing nothing |
| `show_my_location: false` | No dot, **no ◎, no ➤** — the whole control bar goes — and no GPS watch. Nothing on the card can ask the browser for your position, so no permission prompt ever appears. Covered by a regression test in `tests/map.test.js` |
| Navigate to another dashboard view | The GPS watch stops. This is the one genuinely battery-hungry thing the card does, so it must not survive leaving the view |

With the position dot on and the list shown (`show_list: true`), the list joins
in: its header switches from `· from home` to `· from you` on the first fix, and
the distances are then measured from where you stand. They redraw once you have
moved 100 m, not on every fix — a table rebuilt several times a second would
throw away your scroll position mid-scroll.

The remaining check needs a car: with ➤ armed, the map should keep pace while
driving. Nothing on a desk reproduces that.

---

### 4h. Cars on the map, and the 🚗 picker

Needs at least one car from section 5b whose source entity reports coordinates
(a `device_tracker`, typically). Two cars make the picker testable.

| Check | Expect |
| --- | --- |
| Car marker | Drawn **above** station pins, ringed by fuel level — green when full, red when empty — with the percentage on it |
| A car with no photo | Material Design's `mdi:car` in the theme's own text colour, not an emoji |
| A car with `entity_picture` | The photo fills the disc |
| Two cars on identical coordinates | **One** marker showing both faces. Tap it → they spread apart on legs; tap either for its own popup |
| 🚗 button | Appears only with **two or more** cars. Title: `Vælg biler (2 af 3 vises)` |
| The panel | Header `Vis biler her`, one checkbox per car, footer `Gælder kun denne enhed`. A car with no position reads `<name> (ingen position)` |
| Untick a car | It disappears, and the button label reads `2/3` — a filter must never be silent |
| Reload the page | Still hidden. The choice lives in this device's `localStorage`, keyed by the logged-in HA user |
| Another browser, or another HA user | Unaffected by your choice |
| A car's own popup | `Skjul denne bil her` hides just that one |
| A second Tankpriser card on the same view | Shares the filter immediately, without a reload |
| `show_cars: false` | No cars and no 🚗 button |
| `car_picker: false` | Cars shown, no 🚗 button |
| `cars: [sensor.passat_days_until_refuel]` | Only that car, for everyone — this is dashboard config, not per-device |

---

### 4i. The prediction card

```yaml
type: custom:tankpriser-prediction-card
entities:                                 # as many cars as you like
  - sensor.passat_days_until_refuel
title: Passat            # optional; defaults to the car's name
```

The cars are chosen in the editor's *"Which cars"* field. Check that picker lists
**only your cars**: it is filtered to the duration device class, which no other
Tankpriser sensor has, so a price sensor appearing there is a bug. Added from the
card picker it starts with every car you have.

| Config | Expect |
| --- | --- |
| One car, no `title` | The card header is the **car's own name**, e.g. `Passat`, and there is no second name inside the body |
| One car, `title: Min bil` | That, instead |
| One car, `title: ""` | No header at all — the escape hatch for a card inside a section that already names the car |
| Two cars | **One block per car**, each headed by its own name, separated by a rule. No card header unless you set `title` |
| Two cars | The donation footer appears **once**, at the bottom — not once per car |
| A car sensor that does not exist | That block alone says `Entity … not found.`; the other cars still render |
| Old `entity:` (single) config | Still works, unchanged. Opening it in the editor shows it in the list, and saving converts it to `entities:` |

It renders three different ways depending on the sensor's `status`. Use
`tankpriser.seed_demo_history` and `tankpriser.reset_history` (Developer Tools →
Actions) to move between them on demand instead of waiting days.

| Sensor state | Expect on the card |
| --- | --- |
| `status: learning` (state `unknown`) | Big **`Learning…`** and "A day or two of driving is enough for a first estimate." No number invented, no consumption rows |
| `status: estimating` | The number prefixed with `~`, e.g. **`~9.2 days`**, plus "Early estimate from the tank you are on now — it will settle as tanks complete." |
| `status: ready` | The number with no tilde. Below 10 days it shows one decimal (`7.4`), from 10 up it is rounded (`12`) |
| Any state with a level | A fuel gauge bar at the current percentage, labelled `45 % · 27 L` |
| `ready` / `estimating` | Detail rows: **Consumption** (with its unit — `L/100 km` only if you configured an odometer, otherwise a time-based rate), **Confidence** as a percentage with the tank count (`30 % · 2 tanks`), and **Cheapest \<fuel\>** naming the station and price |
| Entity id that does not exist | `Entity sensor.nope not found.` |
| Footer | "This prediction took real work to build… **please consider a donation 💛**", always present and once per card, pointing at `paypal.me/tankpriser`. Old `show_donate: false` / `donate_url:` entries in a config are ignored, not honoured |

Two things worth confirming deliberately:

- **Confidence never exceeds 30 % while `estimating`.** That cap is what stops
  one partial tank looking like a measurement.
- **The gauge tracks the source entity live.** Change the fuel-level entity in
  Developer Tools → States and the bar and percentage must follow without a
  page reload.

---

### 4j. The visual editors

Both cards have one, and a card that only works from YAML is half-broken.

1. Add each card from the picker and configure it **entirely through the form** —
   never touching YAML.
2. Switch to "Show code editor" and back.

| Check | Expect |
| --- | --- |
| Every field has a readable label | e.g. "Map coverage", "Show my position on the map", "Navigate link in station popups" — not raw keys like `show_my_location` |
| Changing a field | The preview updates immediately |
| Round-tripping through the code editor | No option is silently dropped or reordered into nonsense |
| An `entities:` list made by hand | The editor adopts the first entry as its single `entity` rather than wiping your config |

**The form is conditional** — it only offers options that do something:

| With | Expect |
| --- | --- |
| **Show map** off | Five fields only: title, price sensor, show map, and the list's own two (distances, order). Nothing about clustering, position, cars or navigation, because none of that exists without a map |
| **Show map** on | The rest appear. The list's two options go away with it, and come back when **Show price list** is ticked |
| **Show how far away** off | "Order the price list by" disappears — with nothing measured there is no distance to sort on |
| **Map coverage: Home area only** | "Fuel on the map" disappears. In area coverage the map plots whatever the configured sensors carry, so the picker would change nothing |
| **Map coverage: National** | It reappears — the nationwide payload holds every fuel, and the map draws one. Left empty it follows the price sensor's own fuel |
| **Show my position** off | "Start with follow-me on" disappears — it cannot work without the position dot |
| **Show my cars** off | "Let each device choose which cars to show" disappears |
| Prediction card, **donation ask** off | The donation-link field disappears |
| Typing in the title | Focus stays in the field, one character at a time. The schema is cached per shape precisely so `ha-form` does not rebuild mid-word |

Turning the map back on restores your previous map settings — they stay in the
config while hidden rather than being wiped. Covered by tests in
`tests/card.test.js`.

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
   offline test suite (`tests/test_prediction.py`). To see a full
   end-to-end number **today**, call the service **Developer Tools → Actions →
   `tankpriser.seed_demo_history`** (optionally set `tanks`, `litres_per_day`,
   `days_per_tank`): it injects synthetic tanks so the sensor flips to
   `status: ready` with `days_until_empty`, `avg_consumption` and `confidence`
   at once. Undo it with **`tankpriser.reset_history`** to return to real
   learning.
5. **Prediction card:** covered in [4i](#4i-the-prediction-card) — including what
   each of the three states must look like on screen, and how to reach them on
   demand with the two services.

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
| `Area Home (10 km) -> N postnumre` | Normal debug line confirming the radius resolved. `N = 0` means the Home location is unset or outside Denmark |
| No sensors created | No fuel types selected |
| A car's sensor stays `unknown` (`status: learning`) | It has not seen real driving yet: it needs ≥3 days *and* ≥5 % of the tank consumed since the last refuel. A parked car never qualifies — that guard is what stops "empty in nine years" |
| The number moves after a long trip | Expected, and deliberately damped: the tank in progress is weighted by the time it covers, so a busy day nudges the estimate rather than halving it |
| A car shows `~N days` / `status: estimating` | Working as intended: an early estimate from the tank in progress, confidence capped at 0.3. It becomes `ready` after two completed tanks |
| Want a prediction now, for testing | `tankpriser.seed_demo_history` fabricates completed tanks; `tankpriser.reset_history` clears them |
| Card missing from picker | Browser cache — hard refresh; check the resource loaded (Dev console: “TANKPRISER-CARD loaded”) |
| Card shows “Configuration error” (dark card, red `!`) on one device | That client never loaded the card JS. Check **Settings → Dashboards → ⋮ → Resources** contains `/tankpriser/tankpriser-card.js?v=<version>`; the integration adds it on setup. Open `<your-ha-url>/tankpriser/tankpriser-card.js` on the device — JavaScript means the server side is fine and the client needs the resource entry (or an app cache clear) |
| `Kortet kunne ikke indlæses. Prøver igen…` | Leaflet itself failed to load. It is served from your own HA (`/tankpriser/vendor/`), so this is a local problem — the file did not deploy, or the browser is holding a broken cached copy. Not an internet issue |
| Map paints but is empty | No station in view has coordinates. In `area` coverage, check `stations` actually has lat/long; in `national`, check the websocket returned something (DevTools → Network → WS) |
| Map shows stations far outside the radius | `coverage: national`, the default — the viewport is the filter, not your radius. Set `coverage: area` to pin it |
| Map tiles grey, markers fine | The browser cannot reach OpenStreetMap/CARTO (offline, DNS filtering, ad blocker). Prices are unaffected; `show_map: false` removes the dependency entirely |
| `Tankpriser geocoding pass done: N of M addresses new or changed` | Normal info line: Q8/F24 street addresses resolved against DAWA. Once per install, then a re-verification pass every 180 days. The map refreshes itself if anything changed |
| `Tankpriser: <address> moved to lat,lon` | A re-verification found a different position for a station that had one — worth a look, but it just works |
| A Q8/F24 pin has a dashed border and **no** navigate button | Its position is only estimated (DAWA could not match the address exactly, e.g. a motorway plaza). The popup says so; navigating to an estimate would take you confidently to the wrong place |
| One marker showing two car faces | Working as intended: those cars share a position (usually the same zone centre), so they are grouped like nearby stations. Tap it to spread them apart, tap a car for its popup |
| 🚗 button missing | Fewer than two cars exist, `show_cars: false`, or `car_picker: false` |
| Hidden car came back | The filter lives in this device's `localStorage` under the logged-in user — clearing site data, a different browser profile, or a different HA user each start fresh |

---

## 6b. Automated tests

The repo tests itself. These run on every push (the **Tests** job in
`.github/workflows/validate.yml`) and take a few seconds locally:

```bash
npm install          # once — pulls jsdom, used only by the tests
npm test             # the card: helpers + a real DOM

python tests/test_prediction.py         # refuel detection + the estimator
python tests/test_discounts.py          # chain matching + loyalty discounts
python tests/test_geocode.py            # address parsing + geocode cache policy
python tests/test_card_registration.py  # the Lovelace resource registration
python tests/test_spoken.py             # the sentence Siri reads out
python tests/test_nearby.py             # ranking stations around a position
```

(`npm run test:py` runs all six.)

`tests/map.test.js` is the one worth knowing about: it loads the *real* card
together with the vendored Leaflet and markercluster into a jsdom document and
asserts what actually reaches the map — two cars apart draw two markers, two cars
on identical coordinates draw one grouped marker, a real click on that group
reveals both cars with their legs, a lone car is drawn, and hiding a car removes
it. A syntax check cannot catch a call to a function that no longer exists, which
is how v0.10.0b3 shipped with no cars at all.

The Python tests need **no** Home Assistant installed: the pure logic is lifted
out of the modules with `ast` (each file's docstring explains the trade-off).

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

- [x] **The in-car test** — done 2026-07-26. The full CarPlay route works:
      asking by name, three stations read out, choosing by number, Google Maps
      starting on the car screen. Three build details it exposed (force-quit
      app, unset **Ask for Input** prompt, Siri cut off by the map) are fixed in
      the [README's in-car section](../README.md#11-setting-up-the-in-car-sensors).
- [ ] **Card verification on a second client** — the desktop browser is covered;
      the mobile app takes the card by a different route (section 4a) and is the
      one that historically broke.
- [ ] **More chains (need your credential):**
  - Go'on — apply for an API key on **goon.nu** (auto-issued by email). Then I
    add an optional "Go'on API key" field + parser.
  - Circle K / INGO — email **fueldkapi@circlekeurope.com** for access; they
    suggest waiting for their modernized API.
  - Uno-X — needs a bearer token and endpoint discovery.
- [x] **Real donate link** — `https://paypal.me/tankpriser`, in `const.py` and
      `www/tankpriser-card.js` (two copies, keep them in step). Ko-fi and the
      other tip platforms were ruled out: none of them price in DKK.
- [x] **Exact geocoding for Q8/F24** — done: their street addresses are resolved
      against DAWA and cached (section 0). Only what DAWA cannot match still
      falls back to a postnummer centre, flagged `≈`.
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

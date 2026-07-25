# Tankpriser for Home Assistant

Show **local fuel prices** (Blyfri 95/98, Diesel, HVO100) on your Home
Assistant dashboard, around **your Home location**, sourced from the free,
official **per-station price APIs** that Danish fuel chains publish (OK, Q8, F24,
Shell and OIL! today; more chains easy to add). Geographic filtering uses the free
[DAWA](https://dawadocs.dataforsyningen.dk/) postal-code API.

- 🧭 **Nothing to look up** — the area is your Home location; just pick a
  **radius** (default 10 km) and the fuel types you care about.
- 📋 Each widget lists **every station** in the area with its price, cheapest
  highlighted.
- 🗺️ Optional **map** showing where the stations are, with **➤ Navigér hertil**
  in every station popup. Chains that ship no coordinates (Q8/F24) get their
  street address geocoded against DAWA, so they sit on the real forecourt rather
  than a postnummer centre (all 241 of them, when last measured), re-verified
  every 180 days.
- 🙈 **Hide** stations you don't care about.
- 🔔 Optional **price-change notifications** with a rule you choose
  (any change / cheapest changes / below a threshold / decreases only).
- 🃏 Bundled dashboard **card** — no YAML or resource setup, it appears in the
  card picker automatically.

> ℹ️ Electricity/EV prices are not included. Only chains that publish an open
> price API are covered; the list grows as more chains comply with the 2026
> Danish price-transparency law. There is no rate-limit to worry about — the
> integration polls gently (default every 30 min) and caches provider data.

---

## Installation (via HACS)

1. In HACS → **⋮** → **Custom repositories**, add
   `https://github.com/laithsaid/ha-tankpriser` as category **Integration**.
2. Install **Tankpriser**, then **restart Home Assistant**.
3. Go to **Settings → Devices & Services → Add Integration → Tankpriser**.
4. Pick the **fuel types** to track. That's the whole setup — the area comes
   from your Home location.

> ⚠️ Set your **Home location** first (Settings → System → General), otherwise
> there is no area to search around and no stations will be found.

Afterwards, **Configure** opens a menu where you can change the **radius**,
hide stations you don't care about, and set up price notifications.

## Add the card

1. Edit a dashboard → **Add card** → search **“Tankpriser Prices”**.
2. Point it at one of the created sensors, e.g.:

```yaml
type: custom:tankpriser-card
title: Fuel near home
show_map: true          # optional map of the stations
entities:
  - sensor.tankpriser_blyfri_95_e10
  - sensor.tankpriser_diesel_b7
```

Each map marker shows the chain's icon + its price; nearby stations group into a
cluster showing the lowest price and which chains it contains. Tap a station to
see all its fuels and when the chain last changed those prices.

A blue dot shows **your live position**, and it keeps moving as you do. Two
buttons sit below the zoom controls:

- **◎** — recentre on you once, then leave the map alone.
- **➤** — *follow me*: keep recentring on every new fix, so the map keeps pace
  with you in the car. **Off by default**; it turns solid blue when armed, and
  panning the map by hand switches it back off. Set `follow_me: true` to have
  it armed from the start.

If location access is unavailable — denied, or HA served over plain `http`,
where browsers disable geolocation — the dot never appears and ◎ falls back to
your Home Assistant home location. Set `show_my_location: false` to turn the dot
and its GPS watch off entirely. The watch only runs while the card is on screen,
and only uses high-accuracy GPS while follow-me is on.

Tap a station and the popup offers **➤ Navigér hertil**, which hands the position
to whatever navigator the device has: on Android that is the system chooser
(Google Maps, Waze, whatever you installed), on iPhone/iPad it is Apple Maps, and
on a computer it opens Google Maps in a tab. Pin one of them with
`navigation: google` (or `geo` / `apple` / `osm`), or remove the link with
`navigation: off`.

A handful of stations cannot be placed exactly — a motorway plaza with no street
address, typically. Those keep the dashed `≈` pin and get **no** navigate button;
the popup says the position is only estimated. Sending an estimate to a navigator
would look authoritative and put you in the wrong place, which is worse than not
offering it.

Two cars parked in the same place — the normal case at home, where both fall
back to the same zone coordinates and end up on *identical* positions — are
**grouped into one marker showing both cars' faces**, exactly like nearby
stations are. Tap it and they spread apart on their legs so you can pick one.
Cars are drawn above the station pins, so a car is never buried under a
forecourt marker.

A car with no photo of its own is drawn with Material Design's `mdi:car`,
inlined into the card — the same icon Home Assistant would draw, in the theme's
own text colour, rather than an emoji that renders as a different cartoon on
every platform.

**Hiding a car, just for you.** A dashboard is shared by everyone who can see
it, so the card config cannot hold a per-person choice. Instead the 🚗 button
lists every car with a checkbox, and a car's own popup has *"Skjul denne bil
her"*. That choice is remembered on **that device, for that Home Assistant
user** — your phone can show only your car while your partner's phone shows
only theirs, from one shared dashboard. The button reads `2/3` whenever
something is hidden, so a filter is never silent. `car_picker: false` removes it.

Prices refresh when the integration has new data (it pushes an event after each
poll), not on a separate timer in the card.

The map library and all chain icons are served by Home Assistant itself, so the
map works on a LAN with no internet and nothing about your dashboard is
disclosed to third parties — **except the background map tiles**, which your
browser fetches from OpenStreetMap (or CARTO in dark mode). Those requests
reveal your IP and roughly which area you are looking at. Set `show_map: false`
if you would rather not make them; the price list works without any external
request at all.

### Loyalty discounts

If you have a fuel card, set the discount per chain in **øre per litre** under
**Configure → Loyalty discounts** — the unit the cards themselves advertise. From
then on every price in the integration is **what you actually pay**: the
cheapest-of, the notifications, the sensors and the map all agree, and none of
them has to know discounts exist. The card still shows the pump price beside it
(a `−20` badge in the list, "Pumpepris 16,99 · din rabat 20 øre" in the popup) so
you can see why the forecourt sign says something else.

### In the car

Neither CarPlay nor Android Auto will let Home Assistant draw a map, so the card
cannot appear there. Nominate a device under **Configure → Area & fuel types →
"Rank stations near this device"** and you get a `…_cheapest_nearby` sensor per
fuel: Android Auto shows its price while driving and can **navigate straight to
the cheapest station**, and a Siri Shortcut can read out the three cheapest and
route you to the one you say out loud. Setup and the shortcut are in
[`docs/IN_THE_CAR.md`](docs/IN_THE_CAR.md).

**Card options:**

| Option | Default | Description |
| --- | --- | --- |
| `show_map` | `false` | Show the map above the list |
| `map_height` | `420` | Map height in px |
| `map_theme` | `auto` | `auto` (follows HA theme) / `light` / `dark` |
| `coverage` | `national` | `area` = your Home location + the configured radius; `national` = **all** DK stations, the map viewport is the filter (zoom out to aggregate) |
| `cluster` | `true` | Group nearby stations |
| `show_my_location` | `true` | Live GPS dot + the ◎ / ➤ buttons |
| `follow_me` | `false` | Start with follow-me armed |
| `show_cars` | `true` | Plot your configured cars on the map, ringed by fuel level (see prediction, below) |
| `car_picker` | `true` | The 🚗 button: hide/show cars **on this device only** |
| `navigation` | `auto` | "Navigér hertil" in a station popup. `auto` = the device's own navigator, or force `geo` / `apple` / `google` / `osm`, or `off` |
| `show_list` | shown only when map is off | Set `true` to show the price table too |
| `highlight_cheapest`, `max_stations`, `show_donate`, `donate_url` | | as before |

**Full national map** (all Danish stations for one fuel; use Panel view for full width):

```yaml
type: custom:tankpriser-card
show_map: true
coverage: national
map_theme: dark
entities:
  - sensor.tankpriser_blyfri_95_e10   # the fuel to show nationwide
```

## Options

Open the integration → **Configure**, then pick **Area & fuel types** or
**Price notifications**:

| Option | Description |
| --- | --- |
| Radius | 5 / 10 / 15 / 25 / 50 km around your Home location |
| Fuel types | Which fuels to create sensors for |
| Hide stations | Stations to exclude from the list & cheapest calc |
| Update interval | Minutes between refreshes (min 15) |
| Notifications | Enable, pick a `notify.*` service and a firing rule |

## Notifications

When enabled, after each refresh the integration compares prices and calls your
chosen `notify.*` service per the rule. It also fires a `tankpriser_price_updated`
event you can use in automations.

## Fuel-consumption prediction (per car)

Tankpriser can also predict **when each of your cars will next need refuelling**,
learned entirely from a fuel-level entity you already have in Home Assistant.
It's **free** — if you find it useful, a donation is genuinely appreciated (it
took real work), but nothing is ever withheld.

**Add a car:** open the integration → **Add car** (you can add as many as you
like). Point it at any entity that reports the fuel level:

| Field | Notes |
| --- | --- |
| Fuel-level entity | A sensor, or e.g. a car `device_tracker` |
| Level attribute | Leave empty to use the state; otherwise the attribute, e.g. `fuel_level`. Nested paths use dots: `data.fuel.level` |
| Level unit | Percent of tank, or litres |
| Tank capacity | Litres — turns a percentage into litres and estimates range |
| Odometer (optional) | With one we predict in **L/100 km**; without it, a time-based estimate |
| Fuel type | Used to show the cheapest nearby price for that fuel |

Each car gets a **`sensor.<car>_days_until_refuel`**, with attributes for
consumption, confidence, the predicted empty date, and the cheapest nearby
station for its fuel. It answers in three stages, so you are not waiting weeks
for the first number:

| `status` | State | When |
| --- | --- | --- |
| `learning` | `unknown` | Right after adding the car. It needs to see real driving — at least **3 days**, and at least 5 % of the tank gone — before it will guess. A car sitting on the drive tells it nothing. |
| `estimating` | a number, shown as `~9 days` | From the tank you are on *now*, or from a single completed tank. Rough, confidence capped at 0.3, and it moves as it learns. |
| `ready` | a number | Two or more completed tanks (i.e. two refuels) back it up. |

**If you drive irregularly** — hard some days, not at all on others — this still
works, and the arithmetic is built around it. A completed tank already averages
your busy and quiet days together, which is why two of them count as `ready`. The
tank in progress is folded in continuously so the estimate tracks a change in
your habits within days rather than waiting for your next fill-up, but it is
weighted by *how much time it covers*: one busy Saturday nudges the number, it
does not take it over. Only when the open tank approaches a full tank's worth of
driving does it carry a full tank's worth of influence.

**Prediction card:** add a **Tankpriser Prediction** card (`type:
custom:tankpriser-prediction-card`, `entity: sensor.<car>_days_until_refuel`)
for a compact panel with a tank gauge and the details.

**On the map:** if a car's source entity reports coordinates (e.g. a
`device_tracker`), it's also plotted on the main map card, ringed by fuel level
(**green when full → red when empty**) with the percentage on the marker. On by
default when the map is shown; turn it off with `show_cars: false`.

## Data & attributes

Each price sensor's state is the **cheapest** price for its fuel; the full list
is in attributes (`stations`, `cheapest_station`, `average_price`,
`station_count`, …).

## Support / Donate

Tankpriser is free and open source. If it's useful to you, you can support
development — see the link in the card footer.

## For developers

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the pieces fit together:
  the price-aggregation and consumption-prediction subsystems, data flow,
  caching and external dependencies.
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — a guide to reading the
  code: entry points, three end-to-end walkthroughs (a price refresh, a car's
  level changing, the card painting the map), the data contracts between
  layers, a per-module reference, the prediction algorithm, storage schemas,
  how to extend it, and a "where to look if…" table.
- [`docs/IN_THE_CAR.md`](docs/IN_THE_CAR.md) — CarPlay, Siri and Android Auto:
  what each platform allows, the "cheapest nearby" sensor, and a Siri Shortcut
  that reads out the cheapest stations and navigates to the one you name.
- [`docs/TESTING.md`](docs/TESTING.md) — installing a build in a real HA,
  verifying it, running the test suite, and a troubleshooting table.

## Disclaimer

Prices come directly from each fuel chain's public price API and may be delayed
or inaccurate. This project is not affiliated with any fuel chain. Use
responsibly and keep the polling interval reasonable.

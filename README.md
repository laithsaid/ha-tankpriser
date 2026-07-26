# Tankpriser for Home Assistant

[![HACS: custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Danish fuel prices — **Blyfri 95/98, Diesel, HVO100 and more** — on your Home
Assistant dashboard, on a map, in your car and in Siri. Prices come straight
from the **official per-station price APIs** that Danish fuel chains are
required to publish (OK, Q8, F24, Shell and OIL! today), with no scraping, no
account and no API key. Geographic filtering uses the free
[DAWA](https://dawadocs.dataforsyningen.dk/) address API.

Two different scopes, worth knowing up front:

- The **sensors** — cheapest price, station list, notifications — cover **your
  Home Assistant Home location plus a radius you choose**. Nothing to look up,
  nothing to paste in.
- The **map card** is not tied to that radius. Out of the box it plots **every
  station in Denmark** and the *viewport* is the filter: pan or zoom out and you
  see the rest of the country, zoom in and clusters break apart. Set
  `coverage: area` if you want the map to stay inside your radius too.

  ![Tankpriser card](docs/images/hero-card.png)

> ℹ️ **Petrol and diesel only.** Electricity/EV charging prices are not
> included — no Danish charge-point operator publishes an open price feed that I know of (If you know where I can get the data from then let me know and and I will try to inclide it).
> Chains appear here as they start complying with the 2026 price-transparency
> law; adding one is a small change ([how to add a chain](docs/IMPLEMENTATION.md#add-a-fuel-chain)).

**Contents**

1. [Features](#features)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [First-time setup](#first-time-setup)
5. [Configuration](#configuration) — one section per feature
6. [Entities and attributes](#entities-and-attributes)
7. [Services, events and diagnostics](#services-events-and-diagnostics)
8. [Troubleshooting](#troubleshooting)
9. [Privacy and data sources](#privacy-and-data-sources)
10. [Screenshots to capture](#screenshots-to-capture)
11. [For developers](#for-developers)

---

## Features

What each feature *is*. How to switch it on is in
[Configuration](#configuration), one section per feature, in the same order.

| # | Feature | In short |
| --- | --- | --- |
| 1 | [Local price sensors](#1-local-price-sensors) | Cheapest price per fuel around Home, full station list in attributes |
| 2 | [Hidden stations](#2-hidden-stations) | Exclude forecourts you would never use |
| 3 | [Loyalty discounts](#3-loyalty-discounts) | Every price becomes *what you actually pay* |
| 4 | [Price-change notifications](#4-price-change-notifications) | Four rules, to any `notify.*` service |
| 5 | [The price card](#5-the-price-card) | Bundled Lovelace card, no YAML or resource setup |
| 6 | [The map](#6-the-map) | Every station with chain icon and price — all of Denmark by default, the viewport is the filter |
| 7 | [Live position and follow-me](#7-live-position-and-follow-me) | A blue dot that keeps up with you while driving |
| 8 | [Navigate here](#8-navigate-here) | Hand a forecourt to the phone's own navigator |
| 9 | [Exact forecourt positions](#9-exact-forecourt-positions) | Street addresses geocoded, estimates marked as estimates |
| 10 | [Cars on the map](#10-cars-on-the-map) | Your cars plotted, ringed by fuel level, hideable per device |
| 11 | [In the car: CarPlay, Siri, Android Auto](#11-in-the-car-carplay-siri-and-android-auto) | A "cheapest nearby" sensor built for voice and car screens |
| 12 | [Fuel-consumption prediction](#12-fuel-consumption-prediction) | When each car next needs refuelling, learned from its fuel level |
| 13 | [Chains that need an API key](#13-chains-that-need-an-api-key) | A guided page for chains that are not open (none today) |

### 1. Local price sensors

One sensor per fuel type you select. Its **state is the cheapest price** in your
area; its attributes carry **every station** in range with price, address,
coordinates and when the chain last changed that price, plus `average_price`,
`station_count` and `cheapest_station`.

Seven fuels are modelled: **Blyfri 95 (E10)**, **Blyfri 98**, **Blyfri 95 Extra
(E5)**, **Oktan 100**, **Diesel (B7)**, **Diesel Extra** and **HVO100**. Which
ones exist near you depends on the chains around you — OK sells Oktan 100, Q8
and F24 sell HVO100, and so on.

The area is your **Home location** and a radius of 5–50 km. Prices refresh every
30 minutes by default (minimum 15). A chain that stops answering keeps serving
its last good response for at most 6 hours and then **drops out of the list
entirely** — a stale price with nothing on screen saying so is worse than a
missing one.

### 2. Hidden stations

Some forecourts you will never use — the one behind a motorway junction you
cannot reach, or a chain you refuse on principle. Hidden stations disappear from
the list, the map and the cheapest-of calculation, so the number on your
dashboard is a price you would actually drive to.

### 3. Loyalty discounts

Set your fuel card's discount per chain in **øre per litre** — the unit the
cards themselves advertise — and from then on **every** price in the integration
is what you pay. The cheapest-of, the notifications, the sensors, the map and
the in-car sensors all agree, and none of them has to know discounts exist. The
card still shows the pump price beside it (a `−20` badge in the list, *"Pumpepris
16,99 · din rabat 20 øre"* in the popup) so you can see why the forecourt sign
says something else.

### 4. Price-change notifications

After each refresh the integration compares the new prices with the previous
snapshot and can send a notification to any `notify.*` service. Four rules:

| Rule                                              | Fires when                                                    |
| --------------------------------------------------| ------------------------------------------------------------- |
| Any price change in the area                      | *any* station's price for a tracked fuel moved                |
| When the cheapest price changes                   | the cheapest price moved, up or down                          |
| When the cheapest price drops below the threshold | it crosses your threshold downwards (once, not every refresh) |
| Only when the cheapest price decreases            | good news only                                                |

Every successful refresh also fires a `tankpriser_price_updated` event you can
build your own automations on.

### 5. The price card

A Lovelace card ships **inside the integration**: no HACS frontend repository,
no `resources:` entry, no YAML. It registers itself and appears in the card
picker as **"Tankpriser Prices"**, with a **visual editor** for every option.

Without the map it is a compact price table — station, price, cheapest
highlighted — that makes **no external network request at all**. It repaints
when the integration says there is new data, not on a timer of its own.

### 6. The map

Turn the map on and every station becomes a marker showing **its chain's icon
and its price**. Nearby stations group into a cluster labelled with the **lowest
price inside it** and which chains it holds; tap a station for all its fuels and
when the chain last changed them.

**The map has its own coverage, independent of the sensor radius.** Two settings:

- **`national`** — the default. *Every* Danish station for one fuel, with the
  **map viewport as the filter**: pan to another part of the country and its
  stations are there, zoom out and they aggregate into clusters. Your configured
  radius does not apply here at all. The list is fetched on demand over a
  websocket rather than stuffed into a sensor attribute, so ~1200 stations never
  touch the state machine.
- **`area`** — restrict the map to the same Home location + radius the sensors
  use, so the map and the price table below it always agree.

Leaflet and all chain icons are served by Home Assistant itself, so the map
works on a LAN with no internet. The one exception is the **background tiles**,
which your browser fetches from OpenStreetMap (or CARTO in dark mode) — see
[Privacy](#privacy-and-data-sources).

### 7. Live position and follow-me

A blue dot shows **where you are**, and keeps moving as you do. Two buttons sit
under the zoom controls:

- **◎** — recentre on you once, then leave the map alone.
- **➤** — *follow me*: recentre on every new fix, so the map keeps pace with you
  in the car. **Off by default**; it turns solid blue when armed, and panning by
  hand switches it back off.

The GPS watch only runs while the card is on screen, and only asks for
high-accuracy fixes while follow-me is armed. If location is unavailable —
denied, or Home Assistant served over plain `http`, where browsers disable
geolocation — the dot never appears and ◎ falls back to your Home location.

### 8. Navigate here

Every station popup offers **➤ Navigér hertil**, which hands the position to
whatever navigator the device has: on Android the system chooser (Google Maps,
Waze, whatever is installed), on iPhone/iPad Apple Maps, on a computer Google
Maps in a new tab. You can pin one, or remove the link entirely.

### 9. Exact forecourt positions

Chains that ship no coordinates (Q8 and F24) get their **street address geocoded
against DAWA**, so their markers sit on the real forecourt rather than a
postnummer centre — all 241 of them when last measured. Results are cached and
re-verified every 180 days.

A handful of stations still cannot be placed exactly — a motorway plaza with no
street address, typically. Those keep a dashed `≈` pin, say in the popup that
the position is only estimated, and get **no navigate button**. Sending an
estimate to a navigator looks authoritative and puts you in the wrong place,
which is worse than not offering it.

### 10. Cars on the map

If a car you configured for [prediction](#12-fuel-consumption-prediction)
reports coordinates (a `device_tracker`, typically), it is plotted on the map
too, **ringed by fuel level — green when full, red when empty** — with the
percentage on the marker. Cars draw above station pins, so a car is never buried
under a forecourt. Two cars parked in the same place group into one marker
showing both faces; tap it and they spread apart. A car with no photo gets
Material Design's `mdi:car`, inlined, in the theme's own text colour.

**Hiding a car, just for you.** A dashboard is shared by everyone who can see
it, so the card config cannot hold a per-person choice. The car button lists every
car with a checkbox, and a car's own popup has *"Skjul denne bil her"*. That
choice is remembered on **that device, for that Home Assistant user** — your
phone can show only your car while your partner's phone shows only theirs, from
one shared dashboard. The button reads `2/3` whenever something is hidden, so a
filter is never silent.

### 11. In the car: CarPlay, Siri and Android Auto

Neither CarPlay nor Android Auto lets Home Assistant draw a map, so the card
cannot appear there. Instead, nominate a device (your phone, or the car itself)
and you get a **`…_cheapest_nearby` sensor per fuel**: stations within a radius
of *that device*, cheapest first, each with its distance.

- **Android Auto** shows the sensor's price while driving, and — because the
  sensor carries the cheapest station's latitude/longitude — can **navigate
  straight to it**.
- **Siri** can read out the three cheapest and navigate to the one you name. Ask
  *"billigste benzin"*, hear *"Nummer 1: Q8 Virum, 16,79 kroner, 1,2
  kilometer…"*, say *"nummer to"*, and Google Maps starts the route on the
  CarPlay screen. The sentence is pre-built in the `spoken` attribute, in Home
  Assistant's own language, so the Shortcut is one action instead of a Jinja
  loop.

The sensors re-rank when the device *moves*, not only on the price poll — and
only write state when the ranking actually changed, so a driving phone does not
flood the recorder.

### 12. Fuel-consumption prediction

Tankpriser predicts **when each of your cars will next need refuelling**,
learned entirely from a fuel-level entity Home Assistant already has. It is
**free** — a donation is genuinely appreciated, but nothing is ever withheld.

Each car gets a **`sensor.<car>_days_until_refuel`** with attributes for
consumption, confidence, the predicted empty date, current level, and the
cheapest nearby station for its fuel. It answers in three stages, so you are not
waiting weeks for the first number:

| `status`     | State                        | When |
| ------------ | ---------------------------- | ---- |
| `learning`   | `unknown`                    | Right after adding the car. It needs real driving — at least **3 days** and at least **5 % of the tank** gone — before it will guess. A car sitting on the drive tells it nothing. |
| `estimating` | a number, shown as `~9 days` | From the tank you are on now, or one completed tank. Rough; confidence capped at 0.3, and it moves as it learns. |
| `ready`      | a number                     | Two or more completed tanks (i.e. two refuels) back it up. |

**If you drive irregularly** — hard some days, not at all on others — this still
works, and the arithmetic is built around it. A completed tank already averages
your busy and quiet days together, which is why two of them count as `ready`.
The tank in progress is folded in continuously, so the estimate tracks a change
in your habits within days rather than waiting for your next fill-up, but it is
weighted by *how much time it covers*: one busy Saturday nudges the number, it
does not take it over.

With an **odometer** the prediction is in **L/100 km**; without one it falls
back to a time-based rate. A second bundled card, **"Tankpriser Prediction"**,
shows it as a tank gauge with the details.

### 13. Chains that need an API key

Most Danish chains publish openly and need no setup. If a chain is added that
only answers with a personal key, it appears under **Chains & API keys** with a
step-by-step guide to requesting one; the key is validated immediately, stored
in the config entry, sent only to that chain and redacted from diagnostics.
**Today no supported chain needs a key, so this menu entry is hidden.**

### Also included

- **Danish and English UI** — every dialog, error and option is translated, and
  the spoken sentence for Siri follows Home Assistant's own language.
- **Redacted diagnostics** and two maintenance services — see
  [Services, events and diagnostics](#services-events-and-diagnostics).

---

## Requirements

- **Home Assistant 2025.2.0** or newer.
- Your **Home location** set under *Settings → System → General* — it is the
  centre of the sensors' search area. Without it the sensors stay empty (the
  national map still works, since it does not use the radius).
- Internet access from Home Assistant (the chains' APIs and DAWA). No account,
  no API key, no `configuration.yaml` entry.
- Optional, for [prediction](#12-fuel-consumption-prediction): an entity that
  reports a car's fuel level.

## Installation

**Via HACS (recommended)**

1. HACS → **⋮** → **Custom repositories** → add
   `https://github.com/laithsaid/ha-tankpriser` as category **Integration**.
2. Install **Tankpriser**, then **restart Home Assistant**.

**Manually**

1. Copy `custom_components/tankpriser/` into your `config/custom_components/`.
2. Restart Home Assistant.

The dashboard cards are installed with the integration — there is nothing to add
to your Lovelace resources.

## First-time setup

1. **Settings → Devices & Services → Add Integration → Tankpriser**.

   ![Add Integration Tankpriser](docs/images/add-integration.png)

2. Optionally give it a **name** (it becomes the device name and the notification
   title), and pick the **fuel types** to track. Blyfri 95 and Diesel are
   preselected.

   ![Set name and fuel types](docs/images/setup-fuel-types.png)

3. Submit. That is the whole setup — the area comes from your Home location and
   the radius defaults to 10 km. Sensors appear within a few seconds.

Everything else is under **Configure** on the integration card, or on the card
in your dashboard. Read on.

---

## Configuration

The numbered sections below match the numbered features above, one for one.
Where the settings live:

| Where | What you can change there |
| --- | --- |
| **Settings → Devices & Services → Tankpriser → Configure** | Radius, fuel types, hidden stations, update interval, nearby device, discounts, notifications, chain API keys |
| **… → Tankpriser → Add car** | A car for consumption prediction (as many as you like) |
| **Your dashboard → Add card** | The price card and the prediction card, with visual editors |

<!-- 📸 SCREENSHOT S04 options-menu.png — the Configure menu showing "Area & fuel types", "Loyalty discounts" and "Price notifications" -->

### 1. Choosing your area, fuels and refresh rate

*([what this feature does](#1-local-price-sensors))*

**Configure → Area & fuel types.**

| Field | Notes |
| --- | --- |
| **Radius around Home** | 5 / 10 / 15 / 25 / 50 km. Bigger areas cost nothing extra — the chains are fetched nationwide either way and filtered locally. |
| **Fuel types** | One sensor is created per fuel. Removing a fuel removes its sensor. |
| **Update interval (minutes)** | Default 30, minimum 15. Chains change prices roughly daily, so there is little to gain below that. |

<!-- 📸 SCREENSHOT S05 options-settings.png — the "Area & fuel types" form, all fields visible -->

> If no stations appear, check your Home location first — see
> [Troubleshooting](#troubleshooting).

### 2. Hiding stations

*([what this feature does](#2-hidden-stations))*

**Configure → Area & fuel types → Hide these stations.** The dropdown lists the
stations currently discovered in your area; pick any number of them. Names you
type by hand are accepted too, which is how you pre-hide a station that is
temporarily missing from the feed. Hidden stations leave the list, the map and
the cheapest-of calculation at once.

### 3. Setting your loyalty discounts

*([what this feature does](#3-loyalty-discounts))*

**Configure → Loyalty discounts.** One field per chain — OIL!, F24, Q8, Shell,
Circle K / INGO, Go'on, Uno-X, OK — in **øre per litre**. Leave a chain at `0`
if you have no card for it. Maximum 200 øre/L; anything larger is a kroner value
typed into an øre field and would invent a negative price.

<!-- 📸 SCREENSHOT S06 options-discounts.png — the "Loyalty discounts" form with one or two chains filled in -->

The change applies on the next refresh, everywhere at once.

### 4. Turning on price notifications

*([what this feature does](#4-price-change-notifications))*

**Configure → Price notifications.**

| Field | Notes |
| --- | --- |
| **Send notifications on price changes** | The master switch. |
| **Notify service** | A dropdown of your `notify.*` services, e.g. `notify.mobile_app_pixel`. Only `notify.*` is ever called. |
| **When to notify** | One of the four rules in [feature 4](#4-price-change-notifications). |
| **Price threshold** | Only used by the "below threshold" rule, in kr./L, e.g. `16.50`. |

<!-- 📸 SCREENSHOT S07 options-notifications.png — the "Price notifications" form with a rule selected -->
<!-- 📸 SCREENSHOT S08 notification-phone.png — the resulting notification on a phone (optional but nice) -->

The message is one line per fuel that fired, e.g.
`Diesel (B7): cheapest 15,79 → 15,49 ↓`, titled with the integration's name.

### 5. Adding the price card

*([what this feature does](#5-the-price-card))*

Edit a dashboard → **Add card** → search **"Tankpriser Prices"**. The visual
editor covers every option; the YAML below is only for reference.

<!-- 📸 SCREENSHOT S09 card-picker.png — the card picker with "Tankpriser" searched, both cards visible -->
<!-- 📸 SCREENSHOT S10 card-editor.png — the visual editor for the price card -->

A plain price list:

```yaml
type: custom:tankpriser-card
title: Fuel near home
entities:
  - sensor.tankpriser_blyfri_95_e10
  - sensor.tankpriser_diesel_b7
```

<!-- 📸 SCREENSHOT S11 card-list.png — the card with show_map off: the price table, cheapest highlighted, a discount badge if you have one -->

**All card options:**

| Option | Default | Description |
| --- | --- | --- |
| `entity` / `entities` | — | One or more Tankpriser price sensors. Required unless `fuel` is set |
| `fuel` | from the entity | Which fuel the map shows (`blyfri95`, `diesel`, `hvo100`, …) |
| `title` | none | Card header |
| `show_map` | `false` | Show the map above the list |
| `map_height` | `420` | Map height in px |
| `map_theme` | `auto` | `auto` (follows the HA theme) / `light` / `dark` |
| `coverage` | `national` | `area` = Home location + configured radius; `national` = all DK stations, viewport is the filter |
| `cluster` | `true` | Group nearby stations into clusters |
| `show_list` | shown only when the map is off | Set `true` to show map *and* table |
| `highlight_cheapest` | `true` | Emphasise the cheapest row |
| `max_stations` | `0` (all) | Cap the number of rows in the table |
| `show_my_location` | `true` | Live GPS dot and the ◎ / ➤ buttons |
| `follow_me` | `false` | Start with follow-me armed |
| `show_cars` | `true` | Plot your configured cars on the map |
| `cars` | auto-detect | Explicit list of `…_days_until_refuel` entities to plot |
| `car_picker` | `true` | The 🚗 button: hide/show cars **on this device only** |
| `navigation` | `auto` | `auto` / `geo` / `apple` / `google` / `osm` / `off` |
| `show_donate` | `true` | The "Support the project ♥" footer link |
| `donate_url` | project link | Your own donation URL |

### 6. Turning on the map

*([what this feature does](#6-the-map))*

Set `show_map: true` (or tick it in the editor). Coverage defaults to
`national` — all of Denmark, filtered by what you have panned to.

Area map — pinned to the same radius as the sensors, with the table underneath:

```yaml
type: custom:tankpriser-card
show_map: true
coverage: area
show_list: true
entities:
  - sensor.tankpriser_blyfri_95_e10
```

<!-- 📸 SCREENSHOT S12 card-map-area.png — area map, a few chain-icon markers with prices, one cluster -->

Full national map — all Danish stations for one fuel. Use a **Panel** view so it
gets the full width:

```yaml
type: custom:tankpriser-card
show_map: true
coverage: national
map_theme: dark
entities:
  - sensor.tankpriser_blyfri_95_e10   # the fuel to show nationwide
```

<!-- 📸 SCREENSHOT S13 card-map-national.png — zoomed out over Denmark, clusters showing the lowest price in each -->
<!-- 📸 SCREENSHOT S14 station-popup.png — one station popup open: all its fuels, the "last changed" line, the ➤ Navigér hertil button, and a discount line if configured -->

To keep every request local, set `show_map: false` — the price table needs no
external request at all.

### 7. Position and follow-me

*([what this feature does](#7-live-position-and-follow-me))*

Nothing to configure: the dot and the ◎ / ➤ buttons are on whenever the map is.

- Start with follow-me armed: `follow_me: true`.
- Turn the dot and its GPS watch off entirely: `show_my_location: false`.

**Home Assistant must be served over HTTPS** (or `localhost`) for this to work —
browsers refuse geolocation on plain `http`.

<!-- 📸 SCREENSHOT S15 map-controls.png — close-up of the ◎ and ➤ buttons with the blue position dot, follow-me armed (solid blue) -->

### 8. Choosing a navigator

*([what this feature does](#8-navigate-here))*

On by default. To force one navigator for everyone, set `navigation` to `geo`
(Android chooser), `apple`, `google` or `osm`; `navigation: off` removes the
button. Stations with an estimated position never get one, whatever you set.

### 9. Forecourt positions

*([what this feature does](#9-exact-forecourt-positions))*

Nothing to configure — geocoding runs by itself and caches its results. If a
station you know is showing a dashed `≈` pin, its chain publishes no coordinates
*and* DAWA could not match its address; please
[open an issue](https://github.com/laithsaid/ha-tankpriser/issues) with the
station name.

### 10. Showing your cars on the map

*([what this feature does](#10-cars-on-the-map))*

Add a car first (see [§12 below](#12-adding-a-car-for-prediction)); if its
source entity reports coordinates, it appears on the map automatically.

- Turn cars off for this card: `show_cars: false`.
- Plot only specific cars for everyone: `cars: [sensor.passat_days_until_refuel]`.
- Remove the per-device 🚗 filter button: `car_picker: false`.

<!-- 📸 SCREENSHOT S16 car-marker.png — a car marker ringed by fuel level with the 🚗 picker open showing the checkboxes -->

### 11. Setting up the in-car sensors

*([what this feature does](#11-in-the-car-carplay-siri-and-android-auto))*

**Configure → Area & fuel types**, two fields:

| Field | Notes |
| --- | --- |
| **Rank stations near this device (phone or car)** | A `device_tracker`, `person` or `sensor` that carries latitude/longitude. Leave empty and the nearby sensors are not created at all. |
| **How far to look for nearby stations** | 1–100 km, default 15. |

You then get one `sensor.…_cheapest_nearby` per fuel. From there:

- **Android Auto** — parked, open the companion app → **Settings → Companion
  app → Android Auto favorites** and add the sensor. Nothing else to do.
- **Siri / CarPlay** — Apple does not let an integration install a Shortcut for
  you, so that part is built by hand, once.
  **[`docs/IN_THE_CAR.md`](docs/IN_THE_CAR.md) walks through it action by
  action**, including the version that lets you say *"nummer to"*.

<!-- 📸 SCREENSHOT S17 nearby-sensor.png — Developer Tools → States showing a _cheapest_nearby sensor with its `spoken` and `stations` attributes -->
<!-- 📸 SCREENSHOT S18 (optional) siri-shortcut.png — the finished Shortcut, or a photo of the CarPlay screen. Place it in docs/IN_THE_CAR.md, not here. -->

### 12. Adding a car for prediction

*([what this feature does](#12-fuel-consumption-prediction))*

**Settings → Devices & Services → Tankpriser → Add car.** Add as many as you
like.

| Field | Notes |
| --- | --- |
| **Car name** | Becomes the device name and the entity id. |
| **Fuel-level entity** | Any entity that reports the level — a sensor, or e.g. a car `device_tracker`. |
| **Level attribute** (optional) | Leave empty to use the entity's state. Otherwise the attribute, e.g. `fuel_level`. Nested paths use dots: `data.fuel.level`. |
| **Level is measured in** | Percent of tank, or litres. |
| **Tank capacity (litres)** | Turns a percentage into litres and estimates range. |
| **Odometer entity** (optional) | With one, the prediction is in L/100 km; without one, time-based. |
| **Odometer attribute** (optional) | As above — empty means the odometer entity's state. |
| **Fuel type** | Which fuel this car uses, so it can show the cheapest nearby price for it. |

<!-- 📸 SCREENSHOT S19 add-car.png — the "Add a car" form filled in -->

Editing a car later: the car appears as a sub-item on the Tankpriser device
page with its own **Edit** button. Changing the tank size is a good moment to
run [`tankpriser.reset_history`](#services-events-and-diagnostics).

**The prediction card:** Add card → **"Tankpriser Prediction"**, or:

```yaml
type: custom:tankpriser-prediction-card
entity: sensor.passat_days_until_refuel
title: Passat            # optional
show_donate: true        # optional
donate_url: ""           # optional; defaults to the sensor's link
```

<!-- 📸 SCREENSHOT S20 prediction-card.png — the prediction card with the tank gauge, ideally in "ready" state -->

### 13. Entering a chain API key

*([what this feature does](#13-chains-that-need-an-api-key))*

**Configure → Chains & API keys** — the menu entry only appears when a supported
chain actually needs one, which today none does. When it does, pick the chain,
follow the guide shown in the dialog, paste the key (it is checked immediately)
and save. Clearing the field removes the key and stops using that chain.

---

## Entities and attributes

Entity ids below assume the default name "Tankpriser"; if you named the
integration something else, that name is used instead.

### `sensor.tankpriser_<fuel>` — one per tracked fuel

**State:** the cheapest price in your area, in kr./L.

| Attribute | Meaning |
| --- | --- |
| `stations` | Every station in range: `name`, `company`, `address`, `city`, `postnummer`, `price`, `list_price`, `discount_ore`, `updated`, `latitude`, `longitude`, `coord_approx` |
| `cheapest_station`, `cheapest_price` | The winner |
| `average_price` | Mean across the area |
| `station_count` | How many stations sell this fuel here |
| `discounted` | `true` if any price here has one of your loyalty discounts applied |
| `area`, `radius`, `fuel_type`, `fuel_key` | What this sensor covers |

### `sensor.tankpriser_<fuel>_cheapest_nearby` — only when a device is nominated

**State:** the cheapest price within the nearby radius of that device.

| Attribute | Meaning |
| --- | --- |
| `stations` | Up to 8, cheapest first, each with `distance_km` |
| `spoken` | The three cheapest as a ready-to-speak sentence, in HA's language |
| `cheapest_station`, `cheapest_price`, `distance_km` | The winner |
| `latitude`, `longitude` | The winner's position — **this is what Android Auto navigates to**. Omitted when the position is only estimated |
| `station_count` / `listed_count` | How many are in range / how many are listed above |
| `tracked_entity`, `radius_km` | What "nearby" means here |

### `sensor.<car>_days_until_refuel` — one per car

**State:** days until the tank is empty, or `unknown` while learning.

| Attribute | Meaning |
| --- | --- |
| `status` | `learning` / `estimating` / `ready` |
| `current_level_l`, `current_level_percent`, `tank_capacity_l` | Where the tank is now |
| `avg_consumption`, `consumption_unit`, `method`, `basis` | What it learned and how |
| `learned_tanks`, `confidence` | How much it has to go on (0–1) |
| `predicted_empty` | ISO timestamp |
| `cheapest_station`, `cheapest_price` | For this car's fuel, in your area |
| `latitude`, `longitude`, `car_picture` | Present when the source entity supplies them — this is what puts the car on the map |
| `source_entity`, `level_attribute`, `odometer_entity` | Exactly which config is in use, for debugging |

<!-- 📸 SCREENSHOT S21 device-page.png — the Tankpriser device page listing the sensors and the car sub-entries -->

## Services, events and diagnostics

| Service | What it does |
| --- | --- |
| `tankpriser.seed_demo_history` | Injects synthetic tanks into a car so the prediction shows a number immediately. For testing and demos — **it overwrites learned history**. Fields: `car` (blank = all), `tanks`, `litres_per_day`, `days_per_tank` |
| `tankpriser.reset_history` | Clears a car's learned history, returning it to `learning`. Use after changing the tank size, or to undo a demo seed. Field: `car` (blank = all) |

| Event | Payload |
| --- | --- |
| `tankpriser_price_updated` | `entry_id`, `area`, `radius`, `station_count` — fired after every successful refresh |

**Diagnostics:** the integration's ⋮ → *Download diagnostics* gives the entry
config, the resolved area and the current station data. API keys, the
integration's name and your notify service are redacted; attach it to a bug
report as-is.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Sensors have no stations | **Home location not set** (*Settings → System → General*), or your radius is small and rural. The log says `No HA Home location set` in that case. The map is unaffected in `national` coverage — it does not use the radius. |
| The map shows stations far outside my radius | That is `coverage: national`, the default: the viewport is the filter, not your radius. Set `coverage: area` to pin it. |
| A chain you expect is missing | Only OK, Q8, F24, Shell and OIL! publish open APIs today. A chain that fails for more than 6 hours also drops out on purpose rather than showing stale prices. |
| The card says "Configuration error" | A browser holding an old `index.html`. Fully close and reopen the HA app, or hard-refresh the browser. The card is also registered as a Lovelace resource, which fixes this on the next dashboard open. |
| The map is blank/grey but markers show | The background tiles are blocked (no internet, or a DNS/ad blocker). The prices are unaffected; `show_map: false` removes the dependency. |
| No blue position dot | HA served over plain `http`, or location permission denied for the site. Browsers disable geolocation on `http`. |
| A station has a dashed `≈` pin and no navigate button | Its position is only estimated — deliberately not handed to a navigator. |
| No `…_cheapest_nearby` sensors | No device nominated under *Configure → Area & fuel types*, or the entity you chose carries no latitude/longitude. |
| Prediction stuck on `learning` | It needs ≥ 3 days *and* ≥ 5 % of the tank consumed. A parked car never leaves this state. `tankpriser.seed_demo_history` shows what the card looks like meanwhile. |
| Prediction looks wrong after changing tank size | Run `tankpriser.reset_history` for that car. |
| Notifications never arrive | Check the notify service exists (*Developer Tools → Actions*), and remember the "below threshold" rule fires on the *crossing*, not on every refresh. |

More depth — logs, installing a build by hand, running the test suite — is in
[`docs/TESTING.md`](docs/TESTING.md).

## Privacy and data sources

- **Prices** come from each chain's own public API: OK, Q8/F24, Shell and OIL!.
  Each is fetched nationwide, cached for 10 minutes and shared by everything in
  the integration, so a shorter poll interval does not multiply requests. The
  User-Agent identifies this integration honestly, with a link, rather than
  impersonating a browser.
- **Geography** comes from [DAWA](https://dawadocs.dataforsyningen.dk/) — free,
  keyless, run by the Danish state — for postnummer/radius resolution and for
  geocoding station addresses. Geocodes are cached for 180 days.
- **Nothing about you is sent anywhere.** No account, no telemetry, no
  third-party analytics. Your position never leaves the browser: the blue dot is
  drawn locally.
- **The one external request your browser makes** is for map tiles, from
  OpenStreetMap (or CARTO in dark mode). Those reveal your IP and roughly which
  area you are looking at. `show_map: false` removes them; everything else,
  including the chain icons and Leaflet itself, is served by Home Assistant.

## Screenshots to capture

S01–S03 are done. For the rest, put the file in **`docs/images/`** under the name
below; each remaining spot in this README carries a matching
`<!-- 📸 SCREENSHOT Sxx … -->` comment — replace that comment with an
`![alt](docs/images/<file>.png)` line.

| # | File | Capture | Goes in |
| --- | --- | --- | --- |
| ✅ S01 | `hero-card.png` | The price card with the map on, a few stations, cheapest highlighted | Top of README |
| ✅ S02 | `add-integration.png` | "Add integration" dialog, "Tankpriser" searched | First-time setup, step 1 |
| ✅ S03 | `setup-fuel-types.png` | The setup dialog: name field + fuel-type list | First-time setup, step 2 |
| S04 | `options-menu.png` | The Configure menu and its entries | Configuration, intro |
| S05 | `options-settings.png` | "Area & fuel types" form, all fields | Configuration §1 |
| S06 | `options-discounts.png` | "Loyalty discounts" form, a chain or two filled in | Configuration §3 |
| S07 | `options-notifications.png` | "Price notifications" form with a rule chosen | Configuration §4 |
| S08 | `notification-phone.png` | The resulting phone notification *(optional)* | Configuration §4 |
| S09 | `card-picker.png` | Card picker with "Tankpriser" searched, both cards | Configuration §5 |
| S10 | `card-editor.png` | The price card's visual editor | Configuration §5 |
| S11 | `card-list.png` | The card with the map off: price table + discount badge | Configuration §5 |
| S12 | `card-map-area.png` | Area map: chain-icon markers with prices, one cluster | Configuration §6 |
| S13 | `card-map-national.png` | Zoomed out over Denmark, clusters showing lowest prices | Configuration §6 |
| S14 | `station-popup.png` | A station popup: fuels, last-changed, ➤ Navigér hertil | Configuration §6 |
| S15 | `map-controls.png` | Close-up: blue dot, ◎ and ➤ (armed, solid blue) | Configuration §7 |
| S16 | `car-marker.png` | Car marker ringed by fuel level, 🚗 picker open | Configuration §10 |
| S17 | `nearby-sensor.png` | Developer Tools → States, a `_cheapest_nearby` sensor with `spoken` | Configuration §11 |
| S18 | `siri-shortcut.png` | The finished Shortcut, or the CarPlay screen *(optional)* | `docs/IN_THE_CAR.md` |
| S19 | `add-car.png` | The "Add a car" form, filled in | Configuration §12 |
| S20 | `prediction-card.png` | Prediction card with the tank gauge, ideally `ready` | Configuration §12 |
| S21 | `device-page.png` | The Tankpriser device page: sensors + car sub-entries | Entities and attributes |

Tips: use one theme throughout (light reads best in a README), capture at a
**desktop width of ~1000 px** — dialogs are narrow anyway — and before
committing, check S05, S17 and S21 for a house number, a plate or a device name
you would rather not publish.

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
  what each platform allows, the "cheapest nearby" sensor, and the Siri Shortcut
  build, action by action.
- [`docs/TESTING.md`](docs/TESTING.md) — installing a build in a real Home
  Assistant, verifying it, running the test suite, and a troubleshooting table.

Bugs and chain requests: [issues](https://github.com/laithsaid/ha-tankpriser/issues).

## Support

Tankpriser is free and open source, prediction included. If it saves you money,
a donation is genuinely appreciated: **[paypal.me/tankpriser](https://paypal.me/tankpriser)**.

The same link sits in both cards' footers. `show_donate: false` hides it for
good, and `donate_url:` points it somewhere else if you fork this.

## Disclaimer

Prices come directly from each fuel chain's public price API and may be delayed
or inaccurate; the forecourt sign wins. This project is not affiliated with any
fuel chain. Use responsibly and keep the polling interval reasonable.

Licensed under the [MIT License](LICENSE).

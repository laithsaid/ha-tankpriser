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
- 🗺️ Optional **map** showing where the stations are (exact where the chain
  publishes coordinates, otherwise the postnummer centre).
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

Prices refresh when the integration has new data (it pushes an event after each
poll), not on a separate timer in the card.

The map library is served by Home Assistant itself, so the map works on a LAN
with no internet — only the background tiles come from OpenStreetMap/CARTO.

**Card options:**

| Option | Default | Description |
| --- | --- | --- |
| `show_map` | `false` | Show the map above the list |
| `map_height` | `420` | Map height in px |
| `map_theme` | `auto` | `auto` (follows HA theme) / `light` / `dark` |
| `coverage` | `area` | `area` = your Home location + the configured radius; `national` = **all** DK stations, the map viewport is the filter (zoom out to aggregate) |
| `cluster` | `true` | Group nearby stations |
| `show_my_location` | `true` | Live GPS dot + the ◎ / ➤ buttons |
| `follow_me` | `false` | Start with follow-me armed |
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

## Data & attributes

Each sensor's state is the **cheapest** price for its fuel; the full list is in
attributes (`stations`, `cheapest_station`, `average_price`, `station_count`, …).

## Support / Donate

Tankpriser is free and open source. If it's useful to you, you can support
development — see the link in the card footer.

## Disclaimer

Prices come directly from each fuel chain's public price API and may be delayed
or inaccurate. This project is not affiliated with any fuel chain. Use
responsibly and keep the polling interval reasonable.

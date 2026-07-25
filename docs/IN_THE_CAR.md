# Tankpriser in the car — CarPlay, Siri and Android Auto

The Lovelace card cannot appear on a car screen. Neither Apple nor Google lets
Home Assistant draw a map there: CarPlay's map templates are gated behind
navigation-app entitlements the companion app does not have, and Android Auto is
the same. So everything below works by turning the prices into **entities**,
which both platforms do render.

The one entity that makes it work is created for you once you nominate a device
to measure from:

**Settings → Devices & services → Tankpriser → Configure → Area & fuel types →
"Rank stations near this device"** — pick your phone's `device_tracker`, or a car
tracker if you have one, and set the radius (default 15 km).

You then get, per fuel type:

```
sensor.tankpriser_blyfri_95_e10_cheapest_nearby
  state: 16.79                    ← what you pay, discounts included
  attributes:
    cheapest_station: "Q8 Hummeltoftevej 45"
    distance_km: 1.2
    latitude / longitude          ← only when the position is exact
    stations: [ {name, company, city, price, list_price, discount_ore,
                 distance_km, latitude, longitude, coord_approx}, … ]
```

It re-ranks whenever the tracked device moves, not only when prices refresh —
otherwise the distances would describe where you were half an hour ago.

---

## Android Auto

Works with no extra setup beyond the above.

1. **Show the price while driving.** Parked, open the companion app →
   **Settings → Companion app → Android Auto favorites** and add
   `…_cheapest_nearby`. Android Auto renders `sensor` states in its driving list.
2. **Navigate to it.** Android Auto offers navigation to any entity carrying a
   location, and this sensor puts the *cheapest nearby station's* coordinates on
   itself — so "navigate to Blyfri 95 cheapest nearby" routes you to that
   forecourt. Stations we could only place approximately are deliberately left
   without coordinates rather than sending you to a postnummer centre.
3. Your car's `…_days_until_refuel` sensor works as a favourite too.

## CarPlay

CarPlay shows only actionable domains — `button`, `cover`, `input_boolean`,
`input_button`, `light`, `lock`, `scene`, `script`, `switch`. **No sensors.** So
prices cannot be *displayed*; the way in is **voice**, via a Siri Shortcut.

### The shortcut: ask, choose, navigate

Build this once in the Shortcuts app. Name it something you can say cleanly —
"Billigste benzin".

| # | Action | Configure |
| --- | --- | --- |
| 1 | Home Assistant · **Update location** | a fresh fix before measuring distance |
| 2 | Home Assistant · **Render template** | template A below |
| 3 | **Speak Text** | the result of step 2 |
| 4 | **Dictate Text** | you say "nummer to" or "Shell" |
| 5 | Home Assistant · **Render template** | template B, with the *Dictated Text* variable inserted |
| 6 | **Open URLs** | the result of step 5 |

Then, in the car: press the wheel's voice button, say **"Billigste benzin"**,
listen, say which one, and Google Maps starts the route on the CarPlay screen.

**Template A** — read out the three cheapest:

```jinja
{% set s = state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'stations') %}
{% if s %}{% for st in s[:3] %}Nummer {{ loop.index }}: {{ st.company }} {{ st.city }},
{{ st.price }} kroner, {{ st.distance_km }} kilometer. {% endfor %}
{% else %}Ingen stationer i nærheden.{% endif %}
```

**Template B** — pick what you said, return a Maps URL. Replace `SPOKEN` with the
Dictated Text variable:

```jinja
{% set said = "SPOKEN" | lower %}
{% set s = state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'stations')[:3] %}
{% set words = {1: ['1','en','et','one'], 2: ['2','to','two'], 3: ['3','tre','three']} %}
{% set ns = namespace(pick=s[0]) %}
{% for st in s %}
  {% if words[loop.index] | select('in', said) | list or st.company | lower in said %}
    {% set ns.pick = st %}
  {% endif %}
{% endfor %}
https://www.google.com/maps/dir/?api=1&destination={{ ns.pick.latitude }},{{ ns.pick.longitude }}
```

### Why it matches on numbers and chains

Not on station names. "Hummeltoftevej" and "Dronningemaen" are exactly the words
speech recognition gets wrong, and getting them wrong while driving is the worst
time for it. Numbers and chain names ("Shell", "Q8", "OK") recognise reliably,
and if it hears neither, the fallback is the cheapest — a sensible answer rather
than none.

### Simpler variants

- **No conversation:** drop steps 4–5 and route straight to the cheapest. Two
  spoken words, one destination.
- **Assist prompt instead:** CarPlay's Quick Access can hold saved Assist
  prompts, if you would rather ask Assist and just hear the answer. That cannot
  start navigation — Assist has no way to open Google Maps.

### What to verify in the driveway

Whether **Dictate Text** and **Open URLs** render and launch on *your* head unit
is iOS- and car-specific. Everything upstream (Siri triggering the shortcut, the
template rendering, Speak Text) is not in doubt. Test it parked before you rely
on it at 110 km/h.

---

## Loyalty discounts

Every price above is what *you* pay. Configure your cards under
**Configure → Loyalty discounts**, in øre per litre, per chain — the same unit
the cards advertise. The discount is applied before anything reads a price, so
the cheapest-of, the notifications, the card and these in-car surfaces all agree.

The card still shows the pump price alongside (a `−20` badge in the list, and
"Pumpepris 16,99 · din rabat 20 øre" in the popup), so you can always tell why
Home Assistant and the sign on the forecourt disagree.

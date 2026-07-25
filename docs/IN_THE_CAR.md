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
    spoken: "Nummer 1: Q8 Virum, 16,79 kroner, 1,2 kilometer. Nummer 2: …"
    stations: [ {name, company, city, price, list_price, discount_ore,
                 distance_km, latitude, longitude, coord_approx}, … ]
```

It re-ranks whenever the tracked device moves, not only when prices refresh —
otherwise the distances would describe where you were half an hour ago.

> **Check your own entity id before pasting the templates below.** Home Assistant
> builds it from the *area name* you chose when adding the integration, so it is
> `sensor.tankpriser_…` only if you kept the default. If you named the area
> "Silkeborg" it is `sensor.silkeborg_blyfri_95_e10_cheapest_nearby`.
> **Developer tools → States**, filter `cheapest_nearby`, and copy what you see.

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

## CarPlay + Siri — the full walkthrough

CarPlay shows only actionable domains — `button`, `cover`, `input_boolean`,
`input_button`, `light`, `lock`, `scene`, `script`, `switch`. **No sensors, no
map.** So prices cannot be *displayed* in CarPlay by anything, ours included.
What works is **voice**, and it works well: you ask, Siri reads out the three
cheapest with distances, you say which one, and navigation starts.

Apple does not let an integration install a Shortcut on your phone, so this part
is built by hand — once, in about five minutes. Everything hard (ranking,
distances, discounts, the spoken sentence, the coordinates) is already done by
the sensor; you are wiring six actions together.

### Before you start

- The `…_cheapest_nearby` sensor exists (see the top of this page) and shows a
  price in **Developer tools → States**. Copy its exact entity id.
- The Home Assistant app is signed in on the iPhone.
- Google Maps is installed. (Prefer Apple Maps? See *Variants* below.)

### Part 1 — build the shortcut

Do this sitting down, not in the car.

1. Open the **Shortcuts** app → **+** (top right).
2. Tap the shortcut's name at the top → **Rename** → call it
   **Billigste benzin**. *This is the phrase you will say to Siri*, so pick
   something you pronounce cleanly and that sounds unlike your other shortcuts.
3. **Add action** → search `Home Assistant` → **Update location**.
   *Why first: it forces a fresh GPS fix, so the distances describe where you
   are, not where you were when the app last checked in.*
4. **Add action** → search `Home Assistant` → **Render template**. Paste this,
   replacing the entity id with yours:

   ```jinja
   {{ state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'spoken') }}
   ```

   That attribute is already a finished sentence — *"Nummer 1: Q8 Virum, 16,79
   kroner, 1,2 kilometer. Nummer 2: …"* — in your Home Assistant language.
5. **Add action** → search `Speak` → **Speak Text**. Tap its text field and pick
   **Render template** from the variable bar above the keyboard. Expand the
   action (tap the ⌄) and turn **Wait Until Finished** on, so it finishes
   reading before it starts listening.
6. **Add action** → search `Dictate` → **Dictate Text**. Expand it and set
   **Stop Listening → After Pause**.
7. **Add action** → **Render template** again. Paste the template below. Where it
   says `SPOKEN`, select the word, delete it, and insert the **Dictated Text**
   variable from the bar above the keyboard.

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

8. **Add action** → search `Open URLs` → **Open URLs**, and set its input to the
   *second* **Render template** result.
9. **Done**.

### Part 2 — test it parked, on the phone

Say **"Hey Siri, Billigste benzin"** with the engine off.

Expected: it speaks three stations → pauses to listen → you say **"nummer to"**
(or **"Shell"**) → Google Maps opens with the route.

### Part 3 — in the car

Connect CarPlay, press the **voice button on the steering wheel**, and say
**"Billigste benzin"**. The Shortcuts app itself never appears on the CarPlay
screen — voice is the only trigger, and that is by Apple's design, not a
limitation of this integration.

### If something does not work

| What happens | Why, and what to do |
| --- | --- |
| Siri: *"I don't see an app for that"* | The shortcut name is being misheard. Rename it to something more distinct and say it exactly. |
| *"Ingen stationer i nærheden"* | No station within the radius, or the tracked device has no position. Check the sensor in Developer tools → States: `station_count`, `tracked_entity`, `radius_km`. |
| Speaks nothing at all | The entity id in the template is wrong — it follows your **area name**, not always `tankpriser_…`. Copy it from Developer tools → States. |
| Speaks, then nothing happens | Google Maps is not installed, or the last station has no coordinates (approximate positions are deliberately omitted). Try the Apple Maps variant. |
| Distances look stale | The **Update location** action is missing or not first. |
| It picks the wrong station | Say the **chain** ("Shell", "Q8", "OK") instead of the number — it matches either. If it hears neither, it routes to the cheapest on purpose, rather than failing. |

### Variants

- **Apple Maps instead of Google:** change the last line of template B to
  `http://maps.apple.com/?daddr={{ ns.pick.latitude }},{{ ns.pick.longitude }}&dirflg=d`.
- **No conversation, just take me there:** delete actions 6 and 7 and point
  **Open URLs** at a template that returns the URL for `stations[0]`. Two spoken
  words, one destination.
- **Just tell me, do not navigate:** keep actions 1–5 only. This variant also
  works as a saved **Assist prompt** in CarPlay's Quick Access tab.

### What I could not verify for you

Whether **Dictate Text** and **Open URLs** behave on *your* head unit is iOS- and
car-specific — some units are fussier than others. Everything before that (Siri
triggering by name, the template rendering, Speak Text) is not in doubt. That is
why Part 2 exists: test it in the driveway before trusting it at 110 km/h.

---

## Loyalty discounts

Every price above is what *you* pay. Configure your cards under
**Configure → Loyalty discounts**, in øre per litre, per chain — the same unit
the cards advertise. The discount is applied before anything reads a price, so
the cheapest-of, the notifications, the card and these in-car surfaces all agree.

The card still shows the pump price alongside (a `−20` badge in the list, and
"Pumpepris 16,99 · din rabat 20 øre" in the popup), so you can always tell why
Home Assistant and the sign on the forecourt disagree.

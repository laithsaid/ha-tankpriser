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
    station_count: 23             ← how many are in range
    listed_count: 8               ← how many `stations` holds (capped)
    spoken: "Nummer 1: Q8 Hummeltoftevej, 16,79 kroner, 1,2 kilometer. …"
    stations: [ {name, company, city, price, list_price, discount_ore,
                 distance_km, latitude, longitude, coord_approx}, … ]
```

`spoken` names the three cheapest, using each station's road (the house number
is dropped — it is unusable when heard and the map action navigates anyway).
When all three carry the same price, which happens whenever one chain's national
price sweeps the top — OK does this constantly — it is stated once instead of
three times, leaving each station with the only figure that differs:

```
"Alle tre koster 16,19 kroner. Nummer 1: OK Nordre Ringvej, 1,9 kilometer.
 Nummer 2: OK Vestre Ringvej, 2,1 kilometer. Nummer 3: OK Julsøvej, 7,5 kilometer."
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

### Where each part happens

Three different places, which is the easiest thing to get lost in:

| Part | Where you do it |
| --- | --- |
| Nominate the device to measure from | **Home Assistant** — browser or app |
| Build the shortcut (Part 1 below) | **Your iPhone**, in Apple's **Shortcuts** app |
| Use it | **In the car**, by voice only |

The **Shortcuts app is Apple's, and comes with iOS** — a dark icon with two
overlapping rounded squares. Quickest way to find it: swipe down on the home
screen and type `Shortcuts`. If it is not there it was deleted at some point;
reinstall it free from the App Store.

Two things that are easy to assume otherwise:

- **Shortcuts has no CarPlay screen.** You cannot build, edit or even see
  shortcuts on the car display; in the car, voice is the only way to run one.
  That is Apple's design, not a limit of this integration.
- **The Home Assistant actions live inside Shortcuts**, not the other way round.
  When a step says *search "Home Assistant"*, you are searching the Shortcuts
  action picker. Those actions are there because the Home Assistant app is
  installed and signed in on that same iPhone — you never open the HA app while
  building this.

### Before you start

- The `…_cheapest_nearby` sensor exists (see the top of this page) and shows a
  price in **Developer tools → States**. Copy its exact entity id — you will
  paste it into two templates.
- The Home Assistant app is installed and signed in **on the iPhone** (not just
  on the desktop).
- Google Maps is installed on the iPhone. (Prefer Apple Maps? See *Variants*.)
- **Settings → Siri & Search → Siri Responses** is set to **Prefer Spoken
  Responses**. On the default *Automatic*, Siri prints its answer instead of
  speaking it whenever the ring switch is silent — and a shortcut whose whole
  point is being heard while driving then does nothing useful.
- Note which **language** Siri is set to, in the same settings screen. The
  shortcut's name has to be words in *that* language or Siri will not match it.

### Part 1 — the simple version, five actions

Build this first. It asks, reads out the three cheapest, and routes you to the
cheapest one. No dictation, no variables. Get it working end to end before
adding anything — then there is only ever one new thing to debug.

1. On the **iPhone**, open Apple's **Shortcuts** app and tap **+** (top right)
   to create a new, empty shortcut.
2. Tap the shortcut's name at the top → **Rename** → call it
   **Billigste benzin**. *This is the phrase you will say to Siri*, so pick
   something you pronounce cleanly and that sounds unlike your other shortcuts.
   It must be words in **Siri's own language**: a Danish name spoken to an
   English Siri gets transcribed as nonsense, matches nothing, and Siri quietly
   web-searches it instead. On an English Siri, call it *Cheap fuel*.
3. **Add action** → search `Home Assistant` → **Update location**.
   *Why first: it forces a fresh GPS fix, so the distances describe where you
   are, not where you were when the app last checked in.*
4. **Add action** → search `Home Assistant` → **Render template**. The action
   appears with two fields: **Server** (leave it on your Home Assistant) and
   **Template**, pre-filled with Apple's example `{{ now() }}`. Select that
   example, delete it, and paste this instead — with your own entity id:

   ```jinja
   {{ state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'spoken') }}
   ```

   That attribute is already a finished sentence — *"Nummer 1: Q8
   Hummeltoftevej, 16,79 kroner, 1,2 kilometer. Nummer 2: …"* — in your Home
   Assistant language. Danish phrasing follows Home Assistant's language
   setting, not the iPhone's: on an English Home Assistant you get "kilometres"
   and a decimal point, which Siri reads as "sixteen point one nine".
5. **Add action** → search `Speak` → **Speak Text**. Tap its text field and pick
   **Render template** from the variable bar above the keyboard. Expand the
   action (tap the ⌄) and turn **Wait Until Finished** on, so it finishes
   reading before anything else happens.
6. **Add action** → **Render template** again. Clear its `{{ now() }}` and paste
   this — again with your entity id. It returns the route to the cheapest:

   ```jinja
   {%- set s = state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'stations')[0] -%}
   https://www.google.com/maps/dir/?api=1&destination={{ s.latitude }},{{ s.longitude }}
   ```

   The `-` in `{%- … -%}` is not decoration: without it the tag leaves its
   newline behind and the action returns a blank line followed by the URL,
   instead of just the URL.
7. **Add action** → search `Open URLs` → **Open URLs**.

   Take Apple's plain **Open URLs**, *not* "Open URLs in Chrome" or any other
   app's version. Chrome would open the link as a **web page**, and Chrome is
   not a CarPlay app, so nothing would reach the car screen. The plain action
   lets iOS hand a `google.com/maps` link to the **Google Maps app**, which is
   what CarPlay can show.

   Its input must be the **second** Render template — the one returning the URL:

   ```
   Open URLs   [Render template]
   ```

   - Already showing a `Render template` chip? Leave it: Shortcuts fills in the
     action directly above, which is the right one.
   - Empty? Tap the field and pick **Render template** from the variable bar
     above the keyboard.
   - Getting a **"Select Variable"** list? It is asking *which* earlier result to
     use, not for a name you invent. Two entries are called *Render template*;
     pick the **lower** one. The first is the spoken sentence, and opening that
     as a URL does nothing.
8. **Done**.

### Part 2 — test it parked, on the phone

*(Still on the iPhone. The car is not involved yet.)*

Say **"Hey Siri, Billigste benzin"** with the engine off. Expected: it speaks
three stations, then Google Maps opens with a route to the first one.

### Part 3 — in the car

Connect CarPlay, press the **voice button on the steering wheel**, and say
**"Billigste benzin"**. The Shortcuts app itself never appears on the CarPlay
screen — voice is the only trigger, and that is by Apple's design, not a
limitation of this integration.

### Part 4 — optional: choose the station out loud

Only once Part 2 works — *including under Siri*, not just when you tap it.

**Use Ask for Input, not Dictate Text.** Both collect something you say, but
only one survives Siri. When Siri launches a shortcut it keeps the microphone
for the whole run, so a **Dictate Text** action asks for a microphone it cannot
have: the shortcut spins for a while and then ends silently, with no error.
**Ask for Input** has Siri do the asking, so it never needs the handover — and
when you tap the shortcut instead, it falls back to an on-screen prompt.

**The number never goes into the template.** The obvious design — paste the
spoken words into a Jinja template and let Home Assistant work out which station
you meant — fails in a way that is very hard to see: if the variable is not
inserted exactly right, the template still renders, still returns a valid URL,
and still opens a map. It just always opens the *cheapest* one, so it looks like
the matching is broken when really the answer never arrived. Pasting the
template from this page is enough to cause it, because pasting replaces the
inserted variable with the plain word again.

So instead the template returns **all three URLs**, and Shortcuts picks the line.
The only variable you insert goes into a numeric index field, where getting it
wrong opens nothing at all rather than quietly opening the wrong thing.

1. Replace the second **Render template**'s contents with this — your own entity
   id, as before. It returns three lines, one URL per station:

   ```jinja
   {%- for st in state_attr('sensor.tankpriser_blyfri_95_e10_cheapest_nearby', 'stations')[:3] %}{% if not loop.first %}
   {% endif %}https://www.google.com/maps/dir/?api=1&destination={{ st.latitude }},{{ st.longitude }}{%- endfor -%}
   ```

   The whitespace control is load-bearing. Without it the result starts with a
   blank line, and the blank line becomes item 1.

2. **Add action** → search `Split` → **Split Text**. Input: the **Render
   template**. Separator: **New Lines**.
3. **Add action** → search `Ask` → **Ask for Input**. Input type: **Number**.
   Prompt: something short like *"Hvilken?"* — Siri reads it aloud before
   listening.
4. **Add action** → search `Get Item` → **Get Item from List**. List: **Split
   Text**. Get: **Item at Index**. Index: tap the field and pick **Provided
   Input** from the variable bar.
5. Point **Open URLs** at **Get Item from List** instead of at the Render
   template.

   The finished order is: Update location → Render template (spoken) → Speak
   Text → Render template (URLs) → Split Text → Ask for Input → Get Item from
   List → Open URLs.

6. Test parked, **via Siri** — say the shortcut's name, then *"three"* when it
   asks. Shortcuts numbers lists from 1, so three gets you the third station.
   Tapping the shortcut tests the template but not the voice path, and the voice
   path is the one that has to work in the car.

> Prefer to keep the choice inside the template anyway? It can be done, but
> match **whole words**, not substrings: `en` and `et` appear inside `benzin`
> and `hvilken`, and `ok` appears inside the word `spoken` itself — so the
> placeholder, left unreplaced, matches the OK chain. That class of bug is
> exactly why the list-index version above is the documented one.

### If something does not work

| What happens | Why, and what to do |
| --- | --- |
| Siri: *"I don't see an app for that"* | The shortcut name is being misheard. Rename it to something more distinct and say it exactly. |
| Siri web-searches the phrase instead of running anything | Siri could not match what it heard to any shortcut, so it fell back to a search. Almost always a language mismatch: **Settings → Siri & Search → Language**. A Danish shortcut name spoken to an English Siri transcribes as nonsense. Either set Siri to Dansk, or rename the shortcut to words in Siri's language. Saying *"kør \<name\>"* / *"run \<name\>"* also helps Siri treat it as a shortcut rather than a query. |
| It runs, spins for a while, then ends silently — no speech, no map | A **Dictate Text** action. Siri holds the microphone for the whole run and never hands it over, so the action waits for audio that never arrives. Replace it with **Ask for Input** (Part 4). To confirm this is it, duplicate the shortcut, delete the dictation action, and run the copy from Siri. |
| It shows the text instead of reading it aloud | Not the shortcut — **Settings → Siri & Search → Siri Responses**. On *Automatic* Siri prints rather than speaks whenever the ring switch is silent. Set **Prefer Spoken Responses**, and check the physical silent switch. Matters most in CarPlay, where printed text is useless. |
| *"Ingen stationer i nærheden"* | No station within the radius, or the tracked device has no position. Check the sensor in Developer tools → States: `station_count`, `tracked_entity`, `radius_km`. |
| Speaks nothing at all | The entity id in the template is wrong — it follows your **area name**, not always `tankpriser_…`. Copy it from Developer tools → States. |
| It always routes to the first station | You are on the Part 1 version, which does that by design. If you have built Part 4, check **Open URLs** points at **Get Item from List** and not at the Render template — pointing at the template opens its first line, which is the cheapest, every time. |
| Part 4 opens the cheapest no matter what you answer | The answer is not reaching the shortcut. Replace **Open URLs** with **Show Result** fed by `you said: [Provided Input]` and run it: an empty result means **Ask for Input** is not returning anything, and no template change will help. |
| A template action returns a date/time | Apple's default `{{ now() }}` was left in place — clear the field completely before pasting. |
| Speaks, then nothing happens | Google Maps is not installed, or the last station has no coordinates (approximate positions are deliberately omitted). Try the Apple Maps variant. |
| A web page opens instead of the map app | "Open URLs **in Chrome**" (or another browser's action) was used instead of Apple's plain **Open URLs**. A browser cannot hand the link on to Google Maps, and is not a CarPlay app. |
| Nothing opens, and the spoken sentence appeared as a URL | **Open URLs** is pointing at the *first* Render template. Point it at the lower one. |
| Distances look stale | The **Update location** action is missing or not first. |
| It picks the station either side of the one you said | Shortcuts numbers lists from **1**, so "one" is the cheapest. Say the position in the spoken list, not an offset. |
| Nothing opens at all in Part 4 | **Ask for Input** returned nothing or a number above 3, so **Get Item from List** produced an empty item. This is the intended failure — better a visible nothing than quietly routing you somewhere you did not ask for. Answer 1, 2 or 3. |

### Variants

- **Apple Maps instead of Google:** swap the URL inside the loop for
  `http://maps.apple.com/?daddr={{ st.latitude }},{{ st.longitude }}&dirflg=d`.
- **Check what the template returns** before wiring it up: in Home Assistant,
  **Developer tools → Template**, paste the block in. For Part 1 the result pane
  must show one line — the URL and nothing else. For Part 4 it must show exactly
  three lines, with no blank line above or below them.
- **No conversation, just take me there:** keep Part 1 and delete the Speak Text
  action. Two spoken words, one destination.
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

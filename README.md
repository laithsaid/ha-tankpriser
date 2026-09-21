# Tankpriser for Home Assistant

[![HACS: custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Fuel prices — **Blyfri 95/98, Diesel, HVO100, Super E5/E10 and more** — on
your Home Assistant dashboard, on a map, in your car and in Siri.

**Denmark** comes straight from the **official per-station price APIs** that
Danish fuel chains are required to publish — OK, Q8, F24, Shell, OIL! and
Circle K / INGO with no key at all, and Go'on and Uno-X with free ones you
ask them for —
with no scraping, no account and no API key. Geographic filtering uses the free
[DAWA](https://dawadocs.dataforsyningen.dk/) address API.

**Germany** comes from [Tankerkönig](https://creativecommons.tankerkoenig.de/),
the free consumer feed of the Bundeskartellamt's MTS-K — every public forecourt
in the country, updated within five minutes of a price change by law. It needs
a free personal API key, and it works differently enough in a few places to be
worth reading [its own section](#14-germany) before setting it up.

**The Netherlands, Belgium, Luxembourg and France** come from
[ANWB](https://www.anwb.nl/)'s own service — around 3,900 Dutch, 1,800 Belgian
and 230 Luxembourgish forecourts, plus France nationwide, with no key and LPG
and CNG included. None of the four mandates an open price API the way Denmark
and Germany do, so this one is undocumented goodwill rather than law, and **it
publishes no timestamps**: those stations show a price but never a "last
changed". France is also searched around you rather than nationwide. Read
[15. The Netherlands, Belgium, Luxembourg and France](#15-the-netherlands-belgium-luxembourg-and-france)
before relying on them.

**Austria** comes from [E-Control](https://www.spritpreisrechner.at/), the
energy regulator's own Spritpreisrechner — the same feed behind the official
app, published because the price transparency law obliges every station to
report and obliges E-Control to publish. No key and no account. It answers
differently from every other source here, though: **ten stations per fuel, and
it chooses the distance**, so read [15b. Austria](#15b-austria) before you
rely on it.

One setup covers one country. Driving from Rotterdam to Silkeborg? Add
Tankpriser once per country and the one you are standing in answers — the same
question, in euro on the way and in kroner when you get there.

### The countries, at a glance

Seven, each set up as its own Tankpriser entry. "Searched around a point" means
the source has no nationwide answer, so that entry asks for an anchor and a
radius — there is no national map and no "cheapest in the country" sensor for
those.

| Country | Prices come from | Key? | How it searches |
| --- | --- | --- | --- |
| **Denmark** | OK, Q8, F24, Shell, OIL!, Circle K / INGO, Go'on, Uno-X | None, except free ones for Go'on and Uno-X | Nationwide |
| **Germany** | [Tankerkönig](https://creativecommons.tankerkoenig.de/) (MTS-K), ~15,000 forecourts | **Free key, activated by hand** | A circle of at most 25 km — [14](#14-germany) |
| **Netherlands** | [ANWB](https://www.anwb.nl/), ~3,900 forecourts | None | Nationwide |
| **Belgium** | ANWB, ~1,800 forecourts | None | Nationwide |
| **Luxembourg** | ANWB, ~230 forecourts | None | Nationwide |
| **France** | ANWB | None | A circle around an anchor — [15](#15-the-netherlands-belgium-luxembourg-and-france) |
| **Austria** | [E-Control](https://www.spritpreisrechner.at/) (Spritpreisrechner) | None | Ten stations per fuel, and the source picks the distance — [15b](#15b-austria) |

Denmark and Germany are the only two with a law behind the feed. The rest is
published goodwill, and ANWB's carries no price timestamps at all.

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
| 6 | [The map](#6-the-map) | Every station with chain icon and price — all of Denmark by default, the viewport is the filter (Germany, France and Austria plot their own circle instead) |
| 7 | [Live position and follow-me](#7-live-position-and-follow-me) | A blue dot that keeps up with you while driving |
| 8 | [Navigate here](#8-navigate-here) | Hand a forecourt to the phone's own navigator |
| 9 | [Exact forecourt positions](#9-exact-forecourt-positions) | Street addresses geocoded, estimates marked as estimates |
| 10 | [Cars on the map](#10-cars-on-the-map) | Your cars plotted, ringed by fuel level, hideable per device |
| 11 | [In the car: CarPlay, Siri, Android Auto](#11-in-the-car-carplay-siri-and-android-auto) | Three routes to a price on a car screen: an Assist prompt, a Siri Shortcut, and a sensor Android Auto can navigate from |
| 12 | [Fuel-consumption prediction](#12-fuel-consumption-prediction) | Hours of fuel left while driving (measured), days until the next fill-up while parked (learned) |
| 12b | ["Fill up now" alerts](#12b-fill-up-now-alerts) | One push when a car is low *and* passing the cheapest station near it |
| 12c | [Self-correcting predictions](#12c-self-correcting-predictions) | Each refuel grades the last prediction, and a repeated lean is corrected |
| 13 | [Sources that need an API key](#13-sources-that-need-an-api-key) | A guided page for sources that are not open — Germany's is one |
| 14 | [Germany](#14-germany) | ~15,000 German stations, a corridor search while driving, prices to three decimals |
| 15 | [The Netherlands, Belgium, Luxembourg and France](#15-the-netherlands-belgium-luxembourg-and-france) | ~5,900 Benelux forecourts from ANWB plus France, with LPG and CNG — and no price timestamps |
| 15b | [Austria](#15b-austria) | The regulator's own feed, no key — but ten stations per fuel and a radius you do not choose |
| 16 | [Testing without driving](#16-testing-the-driving-features-without-driving) | Drive a virtual car across Germany to exercise all of it from your desk |

### 1. Local price sensors

One sensor per fuel type you select. Its **state is the cheapest price** in your
area; its attributes carry **every station** in range with price, address,
coordinates and when the chain last changed that price, plus `average_price`,
`station_count` and `cheapest_station`.

Eight fuels are modelled: **Blyfri 92**, **Blyfri 95 (E10)**, **Blyfri 98**,
**Blyfri 95 Extra (E5)**, **Oktan 100**, **Diesel (B7)**, **Diesel Extra** and
**HVO100** — plus **LPG** and **CNG** where a country sells them. Blyfri 92
comes from Go'on, and from a single Uno-X forecourt in Terndrup — both of which
need [a free key](#13-sources-that-need-an-api-key); it is kept apart from
Blyfri 95 rather than folded into it, because it is cheaper — so it would win
every ranking — and plenty of cars must not be filled with it. Which
ones exist near you depends on the chains around you — OK and Uno-X sell Oktan
100, Q8 and F24 sell HVO100, and so on.

The area is your **Home location** and a radius of 5–50 km. Prices refresh every
30 minutes by default (minimum 15). A chain that stops answering keeps serving
its last good response for at most 6 hours and then **drops out of the list
entirely** — a stale price with nothing on screen saying so is worse than a
missing one.

### 2. Hidden stations

Some forecourts you will never use — the one behind a motorway junction you
cannot reach, or a chain you refuse on principle. Hidden stations disappear
everywhere: the list, the map, the cheapest-of calculation, the answer Siri
gives you and the fill-up alert. So the number on your dashboard is a price you
would actually drive to, and nothing sends you somewhere you have already said
no to.

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
| When the cheapest price drops below the threshold | the cheapest price falls *and* is under your threshold        |
| Only when the cheapest price decreases            | good news only                                                |

The threshold rule reports **every** fall while the price is under your line,
not only the one refresh that first crossed it — so with a threshold of 17,00 a
drop from 16,19 to 16,09 still reaches you. A rise stays silent, and an
unchanged price cannot repeat itself, so it fires at most once per price change.
The threshold box is used by that rule only; leaving a number in it does not
affect the other three.

The prices each refresh saw are remembered on disk, so a restart no longer
swallows a change: the first refresh after one is compared against the last
prices seen before it, not treated as a fresh start. A baseline older than a
week is ignored — by then it is history rather than news.

Every successful refresh also fires a `tankpriser_price_updated` event you can
build your own automations on.

### 5. The price card

A Lovelace card ships **inside the integration**: no HACS frontend repository,
no `resources:` entry, no YAML. It registers itself and appears in the card
picker as **"Tankpriser Prices"**, with a **visual editor** that offers only the
options that currently do something — switch the map off and the map settings
step out of the way.

Without the map it is a compact price table — station, town, distance, price,
cheapest highlighted — that makes **no external network request at all**. It
repaints when the integration says there is new data, not on a timer of its own.

Each row says **how far away** the station is. Measured from your live position
when the map is already tracking it, and from your Home location otherwise; the
section header says which of the two, because "3,2 km" means something different
from the car than from the house. On a phone with the map and position dot on,
the distances follow you as you drive. Nothing new is asked of the browser: a
card that never had permission to know where you are still measures from Home.

The order is **cheapest first**. Set `sort: distance` for nearest first, or
`show_distance: false` to leave distances out altogether.

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

**📍 asks about a place that is neither you nor a car.** Tap the pin button
under the zoom controls and it lights up; the next tap on the map asks what fuel
costs *there*, **drops the stations it finds onto the map** as ordinary
price pins, and frames them alongside the pin you dropped. The bubble on the pin
itself is only a summary — which country's cheapest is what, how many stations
were plotted and how far was searched — because the detail belongs on the
forecourts, where you can see which way they are and how far apart.

A pick deliberately asks for **far more stations than a spoken answer does**.
`tankpriser.nearby` returns 15 by default, which is a list to read out; a pin
dropped on a city finds hundreds, and a map given only the fifteen *cheapest*
of those shows you the cheap edge of town and nothing you could walk to. The
pick asks for 60.

Picked stations sit in their own layer, so the area prices go on refreshing
underneath them and a refresh never wipes your pick; the next pick replaces it.
Near a border both countries are plotted, each price in its own currency, and
each currency gets its own highlighted cheapest — there is no single cheapest
across two currencies. Useful before a trip, or to see what a town you are
heading for is charging.

It is armed by the button rather than fired by any tap, on purpose: where a
source is queried by area (Germany) every ask is a real request against your own
API key, and a map that queried on every stray tap and the end of every drag
would spend them freely. One tap, one answer, then it disarms itself. Turn the
button off entirely with `ask_on_map: false`.

Leaflet and all chain icons are served by Home Assistant itself, so the map
works on a LAN with no internet. The one exception is the **background tiles**,
which your browser fetches from OpenStreetMap in both themes — see
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

Chains that ship no coordinates (Q8, F24, Circle K and INGO) get their **street
address geocoded against DAWA**, so their markers sit on the real forecourt
rather than a postnummer centre — all 241 Q8/F24 sites when last measured, and
the 402 Circle K and INGO ones join them. Results are cached and
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
cannot appear there — and CarPlay will not show a `sensor` either, so the price
entities cannot stand in for it. Three routes get you the answer anyway, and
they are built differently on purpose.

**Siri and CarPlay ask the `tankpriser.nearby` service.** The shortcut takes the
iPhone's *own* position at the moment you ask, hands it over with the question,
and gets back a finished sentence, the ranked stations and one navigation link
each. Nothing is stored in between — no sensor to go stale, no device tracker
that last checked in an hour ago in a town you have left. Ask *"billigste
benzin"*, hear *"Billigste er OK Nordre Ringvej, 16,19 kroner, 1,9 kilometer
væk"*, and the route starts on the CarPlay screen. The sentence is built
server-side, in Home Assistant's own language, so the shortcut is one action
rather than a Jinja loop.

**CarPlay can also just ask, with nothing installed.** Tankpriser registers an
**Assist intent**, and CarPlay pins an Assist prompt to its Quick Access tab —
one tap on the car's own screen, the same sentence spoken back. No Shortcuts
app, no token. The trade is that Assist cannot hand over the phone's position,
so it answers from the device you nominated; the shortcut carries its own fix
and is the one to use if a stale position would bother you.

**Android Auto reads the `…_cheapest_nearby` sensors**, because a car head unit
lists entities and cannot ask a question. Nominate a device — your phone, or the
car itself — and you get one sensor per fuel: the stations within a radius of
*that device*, cheapest first, each with its distance. Android Auto shows the
price while driving and, because the sensor carries the cheapest station's
latitude/longitude, can **navigate straight to it**.

Both rank against **every station in Denmark**, not the area your price sensors
cover — the point is where you are, and you drive out of that area. Halfway to
the next town you are offered the forecourts halfway to the next town.

The sensors re-rank when the device *moves*, not only on the price poll — and
only write state when the ranking actually changed, so a driving phone does not
flood the recorder.

Ask for a fuel you have not configured and every route says so rather than
quoting another one: a price that sounds right and is about the wrong pump is
the one mistake nobody catches while driving.

The Siri route is **confirmed working on CarPlay** (2026-07-26): asked by name,
the station read out, route started on the car screen. It does depend on a
handful of iPhone settings — see
[11a, setting up your iPhone](#11a-setting-up-your-iphone).

### 12. Fuel-consumption prediction

Tankpriser answers **two different fuel questions**, from a fuel-level entity
Home Assistant already has. It is **free** — a donation is genuinely
appreciated, but nothing is ever withheld.

| | The question | The answer |
| --- | --- | --- |
| **Driving** | Does this tank get me there? | **"2 t 24 min of fuel — empty about 18:40"**, measured from the level dropping in front of you |
| **Parked** | When do I next need to fill up? | **"~9 days"**, from what this car's use actually looks like |

They are not two versions of one number, they are answers to different
questions, and they are worked out in completely different ways. While the car
is running the tank is a **measurement**: the level is visibly falling, so the
rate is arithmetic on the last couple of hours — no history, no learning, and no
correction, because there is nothing to predict. A car on its very first drive
answers this correctly.

Parked, there is no rate to read, so the answer has to come from **habit** — how
much this car gets used — and that is where the learning, and the
[self-correction](#12c-self-correcting-predictions), belong.

The sensor's state stays the *parked* answer in days, so a graph of it means
something and the [fill-up alert](#12b-fill-up-now-alerts) has something stable
to read; the driving figures arrive as attributes and take over the card while
the engine is running.

Each car gets a **`sensor.<car>_days_until_refuel`** with attributes for
consumption, confidence, the predicted empty date, current level, the live
driving figures, and the cheapest nearby station for its fuel. The *parked*
answer arrives in three stages, so you are not waiting weeks for a first number
— the driving answer needs none of this and is available immediately:

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
shows it as a tank gauge with the details. The mechanism — how a refuel is
detected, why small top-ups are ignored, what confidence means — is written out
in [12a. How the prediction learns](#12a-how-the-prediction-learns).

### 12b. "Fill up now" alerts

The one alert that needs three things to line up at once: a car that is **low**,
a station that is **near it**, and that station being **the cheapest** for that
car's fuel. Any one of the three on its own is not worth interrupting anyone
for; together they are the moment a detour is free.

> **Tank Passat**
> 18 %, about 3 days left. OK Silkeborg, 0,3 km away, 16,79 kr./L. 50 øre below
> the dearest nearby.

Tapping it opens turn-by-turn navigation to that forecourt.

It is built to be quiet. You are told **once per low tank** — not once per
update — and the next alert waits until the car has actually been filled. A car
left low on the drive for a week produces one notification, not three hundred.

Needs at least one [car added for prediction](#12-fuel-consumption-prediction),
because that is where the fuel level and the car's position come from. Switch it
on under [4. Turning on price notifications](#4-turning-on-price-notifications),
which is also where its notify target is set.

Switching it on makes every refresh place every station in the country, so the
alert still works when the car has driven well outside your area — the same pool
the "cheapest nearby" sensors use. That costs a little geocoding on the first
refresh after you enable it, and nothing afterwards.

### 12c. Self-correcting predictions

Every refuel is a marked exam paper. The moment a tank closes, Tankpriser can
look back at what it had predicted for that tank and see how it did — and if it
has been wrong **in the same direction** several tanks running, it corrects
itself.

That last part is the distinction that matters. A prediction that is 20 % out in
*both* directions is just irregular driving, and there is nothing to fix. One
that is 20 % out *the same way every time* is a model that is wrong, and can be
nudged back. Only the second is acted on.

The correction is deliberately timid: it needs **five graded tanks** before it
does anything, it applies **half** of the measured lean rather than all of it,
and it will never move a prediction by more than **25 %**. Half rather than all
because some of any measured lean is noise — and because half of a real one is
removed each time, so it converges over successive tanks instead of hunting
back and forth. It is on by default and can be switched off in
[12d](#12d-tuning-and-checking-the-prediction).

**A holiday does not count as a habit.** Drive to France for a fortnight and you
will burn three tanks at three times your usual rate. That is not evidence the
prediction is wrong about your car — it is evidence you went to France. So the
correction takes the **typical** tank rather than the average one: a minority of
freak tanks is ignored outright, however extreme, and the factor only moves when
the unusual driving stops being unusual. Which is exactly when it *has* become
your new normal and correcting is the right answer.

That leaves the fast reaction where it already lived. The prediction itself
folds in the tank you are on right now, weighted by how long it has been
running, so a trip to France shows up in the estimate **within days** — see
[12a](#12a-how-the-prediction-learns). The correction is the slow half on
purpose: one handles a change of behaviour, the other handles a model that is
persistently wrong, and letting either do the other's job makes both worse.

You can also see the marking. A card grades every tank you have ever recorded
and says, in a sentence, whether the prediction is matching reality:

> **Passat** — consistently optimistic by about 21 %.
> lean **+21,1 %** · typical error **22,4 %** · within 10 % **1/5**
> Correction: predictions are shortened by 7 % — this car burns faster than the
> raw model believed. On this history the raw model leaned +21,1 %; with the
> correction it had earned at each point, +9,2 %.

That last line is the point: it shows the correction earning its place on your
own data, rather than asking you to take it on trust.

### 13. Sources that need an API key

Most chains publish openly and need no setup at all. A source that only answers
with a personal key appears under **Chains & API keys** with a step-by-step
guide to requesting one; the key is validated immediately, stored in the config
entry, sent only to that source — never in a URL — and redacted from
diagnostics. Three sources are like this today:

- **Go'on** (Denmark, ~200 stations). The key comes from a two-field form and
  arrives by return mail within minutes, automatically. Optional: everything
  else Danish works without it, and Go'on is simply absent until you add it.
  It is the only source selling **Blyfri 92**, so that fuel appears in the
  picker but stays empty until the key is in.
- **Uno-X** (Denmark, 279 stations, all with exact coordinates). Also
  optional, and the only source that will not take a key as a key: it wants
  **OAuth 2.0 client credentials**, so what you paste is a *pair* —
  `client_id:client_secret` on one line — which Home Assistant trades for a
  token that lives 15 minutes and renews itself. Applying for it needs a human
  at Uno-X to approve, so it is the one key that can take days.
- **Tankerkönig** (Germany) — required there, since it is the only German
  source. See [14. Germany](#14-germany).

Only Germany insists. Everywhere with chains that publish openly, the setup
dialog offers the key fields and accepts a blank: the rest of the country works
without them, and you can add a key later under **Chains & API keys** whenever
it arrives.

### 14. Germany

Germany's ~15,000 stations, from the same integration and the same card. Three
fuels — **Super E10, Super E5 and Diesel** — priced to three decimals, in euro,
because every German forecourt sign carries the 9/10 of a cent and a price
rounded to two would disagree with the pump.

Two things genuinely work differently, both because of the source:

- **There is no national list.** Tankerkönig answers only about a circle, and
  caps that circle at 25 km. So a German setup searches a circle around a point
  you nominate, rather than filtering a nationwide download. The map shows that
  circle rather than the whole country.
- **Asking out loud searches ahead of you.** Parked, that is one 25 km circle.
  Moving, it is a *corridor* of up to three circles laid along your heading,
  reaching up to 135 km down the road, with the stations behind you dropped —
  at 130 km/h half a circle around you is road you have already driven, and a
  station you passed costs a U-turn no price difference repays. Nothing about
  this is a setting: it is worked out from how fast you are actually going.

**The map can follow the car.** Set `follow_tracker:` on the card to your phone
or car tracker and the map stops being a picture of home: it centres on the car
and plots the stations fetched *around it*, refreshed as you drive — which is
the only way to see where to fill up in a country with no national list. It
uses the same `tankpriser.nearby` search as the spoken answer, so the map and
Siri can never disagree about what is cheapest, and while you are moving it is
the road ahead you are shown, not the road behind.

```yaml
type: custom:tankpriser-card
entity: sensor.flensburg_super_e10
show_map: true
follow_tracker: device_tracker.min_telefon
```

Each refresh spends a real search against your API key, so the card is
deliberately lazy about it: never more than once a minute, normally only once
you have driven 5 km, and one extra refresh if you have been crawling for five
minutes. **A parked car spends nothing.** Drag the map and it stops chasing
you, as any map should.

**With both countries set up, the country you are in answers.** Denmark and
Germany are two entries, and `tankpriser.nearby` picks the one whose country
your position falls in — so the same Siri shortcut says Danish kroner at home
and euro on the Autobahn, with no second shortcut and nothing to switch. Near
the border the two overlap, and the one with the nearest forecourt leads.
Background sensors are unaffected either way — each entry keeps watching its
own area.

**Near a border you get both sides at once.** A 25 km circle at Kruså genuinely
holds Danish and German forecourts, so the answer carries a block per country:
the map plots both, and the spoken answer names each country's own cheapest —

> In Denmark the cheapest is OK Kruså, 17,59 kroner, 4,2 kilometres away. In
> Germany the cheapest is team Flensburg, 2,23 euro, 9,8 kilometres away.

**It never says which of the two is cheaper, and that is deliberate.** 17,59
kr/L and 2,23 €/L cannot be ordered without an exchange rate, and a rate baked
into the integration would be wrong the week after it was written. Each price is
shown in its own currency, to its own country's decimals — Germany signs its
forecourts to three, Denmark to two — and the comparison is left to you, which
is the sum you were going to do in your head anyway.

**One country feeds the single-station fields a Shortcut reads** —
`spoken_cheapest` still speaks both, but `stations` and `urls` stay one
country, so an existing shortcut keeps navigating to a station priced in the
currency it just named. **The one that leads is the one with the nearest
forecourt**, which is also the country named first in the sentence above.

Distance decides it because distance is the only thing about two countries'
prices that *can* be compared without inventing an exchange rate. It used to be
the entry anchored nearer, which was a fair guess while every entry sat inside
its own country — but a country whose source answers for the whole nation has
no anchor of its own and inherits your Home location. Asking from Luxembourg
City, that led with France, anchored wherever you put it, over Luxembourg
anchored at your house: both were spoken correctly and `urls[0]` pointed at a
French forecourt 29 km away while a Luxembourgish one stood 1,4 km off.

### 15. The Netherlands, Belgium, Luxembourg and France

Four countries from one source: **ANWB**, the Dutch motoring club, whose own app
draws its prices from it. No key, no signup — around **3,900 Dutch**, **1,800
Belgian** and **230 Luxembourgish** forecourts with exact coordinates, plus
**France nationwide**, including **LPG** at about a quarter of them and **CNG**
at a handful.

They are set up exactly like Germany: one Tankpriser per country, and the one
you are standing in answers. Rotterdam → Hamburg → Silkeborg is three entries
and one question.

**France is the exception, and it is set up like Germany rather than like the
others.** The service answers a bounding box, and it refuses one bigger than
about 6° square — by returning an empty list, not an error — so no single
request can cover France. France therefore asks about a **50 km circle around
you**, which means it needs an anchor point when you add it, and that there is
no nationwide French map and no "cheapest in the country" sensor. Everything you
actually ask while driving — `tankpriser.nearby`, the Siri shortcut, the Assist
prompt, the 📍 pin on the map — works exactly as it does everywhere else.

Three things are different here, and they are worth knowing before you trust a
number:

- **No price timestamps.** ANWB publishes no "last changed" — not per price,
  not per station. Every other source we read does, and the card prints it. For
  these two countries there is simply nothing to print, so the popup shows no
  time rather than the moment we happened to fetch, which is not the same thing
  and would be read as one.
- **No law behind it.** Denmark and Germany oblige their chains to publish;
  none of these four does. The official figures — the Dutch CBS monthly
  averages, the Belgian federal *maximum* price, the French *prix-carburants*
  extract — are national or daily rather than per forecourt, so they cannot
  answer "which station". ANWB works and is clean, but it can change or stop
  without notice.
- **Prices of zero get dropped.** The feed carries a couple of hundred of them.
  A zero is a missing price, not a cheap one, and left in it would win every
  ranking and send you to a pump that is not selling.

**CNG is priced per kilogram**, not per litre, and is kept out of every
comparison with a litre price for that reason — on the card, in the
cheapest-nearby ranking and in what Siri says. A kilogram of gas next to a litre
of petrol is a different question, not a better deal.

**Asking about a country you have not added.** Each country is matched by a
box, and a box is generous — it overspills the border on purpose, so a road
that bends across a frontier still gets an answer. The cost is that a position
can sit inside a country's box while standing somewhere else entirely:
Luxembourg is inside both the Belgian and the German box, and northern France
is inside the Belgian one. Until 0.18.0 that produced a confident lie — pinning
Luxembourg City answered *"no stations within 25 kilometres"*, which was true of
the German stations it had searched and false about the 233 Luxembourgish ones
under the pin. Now an empty answer says **where it looked** (*"no stations
within 25 kilometres in Germany"*), and where the position is covered only by a
country you have not set up it says so instead: *"No prices here: Luxembourg is
not set up in Tankpriser."* If you see that, add that country — it is one more
Tankpriser entry and, for these four, no key.

### 15b. Austria

**E-Control**, the Austrian energy regulator, runs the Spritpreisrechner and
publishes it openly under the Preistransparenzverordnung: every station must
report, and the regulator must publish. No key, no account, no signup — add an
Austrian entry and it works.

It is the most *constrained* source Tankpriser reads, and all three limits are
the API's, not ours:

- **Ten stations per request, cheapest first.** There is no parameter that
  raises it — `radius`, `limit` and `maxResults` are all accepted and all
  quietly ignored. So an Austrian area sensor lists ten forecourts where a
  German one lists fifty, and that is the whole answer rather than a truncation
  of a longer one.
- **One fuel per request.** Repeating `fuelType` is accepted and then answered
  for the first one only, so Tankpriser asks three times — Super 95, Diesel,
  CNG — and merges the answers by the station's own id. A forecourt selling two
  of them is still one station on the map.
- **The radius is E-Control's choice, not yours.** Nothing in the request says
  how far to look; it widens until it has ten. Measured live, that was 2 km in
  Vienna, 10 km near Lienz, and 52 km for CNG, where the pumps are rare. The
  radius you set still applies — it just applies to what came back, so setting
  it small discards stations rather than asking for nearer ones.

Two smaller things worth knowing:

- **Only open forecourts are listed**, unlike Germany. With ten slots to spend,
  a closed station takes the place of one you could drive to. Asked about
  Vienna at closing time, the closed-inclusive list offered four shut
  forecourts and one open at 2,309 €; the open-only list answered 2,195 € — a
  station the first list did not mention at all.
- **No price timestamps**, the same silence as ANWB. Austrian stations show a
  price and never a "last changed".

Austria publishes **Super 95, Diesel and CNG** and nothing else — no Super 98,
no premium diesel — so those are the only fuels offered for an Austrian entry.
CNG is priced per kilogram and is never compared against a litre price.

Like Germany and France, Austria is searched **around a point**, so an Austrian
entry asks for an anchor. Its box overlaps Germany's along the whole northern
border — any rectangle holding both Vorarlberg and the Czech frontier also
holds Munich — which is the same arrangement as Luxembourg and resolves the
same way: the country with the nearer forecourt answers.

### 16. Testing the driving features without driving

The corridor, the direction filtering and the spoken answer only behave
differently when something is *moving*, and nobody wants to drive to Munich to
find out whether they work. `tankpriser.simulate_drive` moves a virtual car
along a route — writing positions, speed and heading exactly as the companion
app does — so all of it runs for real from your desk. See
[16. Simulating a drive](#16-simulating-a-drive).

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
- Internet access from Home Assistant (the sources' APIs, and DAWA for
  Denmark). No `configuration.yaml` entry, ever.
- **For Denmark:** no account and no API key. Two chains, **Go'on** and
  **Uno-X**, are optional and do need a free key — see
  [13](#13-sources-that-need-an-api-key).
- **For the Netherlands, Belgium, Luxembourg and France:** no account and no
  API key — see [15](#15-the-netherlands-belgium-luxembourg-and-france) for what
  that source does and does not publish, and for why France is set up with an
  anchor point and the other three are not.
- **For Austria:** no account and no API key — see
  [15b](#15b-austria) for the three limits E-Control puts on every answer, and
  for why an Austrian entry is set up with an anchor point.
- **For Germany:** a free [Tankerkönig](https://creativecommons.tankerkoenig.de/)
  API key, which you request yourself — see [14. Germany](#14-germany). Expect
  to wait: a person there activates each key by hand, and a key that has not
  been activated yet is rejected exactly like a wrong one.
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

2. Pick the **country**. It is preselected from Home Assistant's own country
   setting. This is the one thing that cannot be changed afterwards — it decides
   the sources, the fuels and the currency, so changing it would mean different
   sensors under the same names. Add a second Tankpriser instead.

3. **Germany only:** paste your **Tankerkönig API key**. It is checked on the
   spot. If it is refused, it is almost always because it has not been activated
   yet — see [14. Germany](#14-germany).

4. Optionally give it a **name** (it becomes the device name and the notification
   title), and pick the **fuel types** to track. Petrol and Diesel are
   preselected.

   ![Set name and fuel types](docs/images/setup-fuel-types.png)

5. **Germany only:** confirm the **point to search from**. It defaults to your
   Home location, which is right if you live there — and wrong if you are a Dane
   watching the forecourts around Flensburg, so move it if so.

6. Submit. For Denmark that is the whole setup: the area comes from your Home
   location and the radius defaults to 10 km. Sensors appear within a few
   seconds.

Everything else is under **Configure** on the integration card, or on the card
in your dashboard. Read on.

---

## Configuration

The numbered sections below match the numbered features above, one for one.
Where the settings live:

| Where | What you can change there |
| --- | --- |
| **Settings → Devices & Services → Tankpriser → Configure** | Radius, fuel types, hidden stations, update interval, nearby device, discounts, notifications, refuel prediction, chain API keys |
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
| **Price threshold** | Only used by the "below threshold" rule, in the country's own unit — kr./L in Denmark (`16.50`), €/L in Germany (`1.70`). Compared against the full price, so `1.699` trips a `1.70` rule. |
| **Also alert when a car is low and passing somewhere cheap** | The ["fill up now" alert](#12b-fill-up-now-alerts). Off by default, and it needs at least one car added for prediction. Uses the same notify service as the rows above. |
| **Alert below this much of a tank** | Default 25 %. A 40 L city car and a 90 L estate disagree about what is worth interrupting for, so this is a slider rather than a rule. |
| **…and a station is within** | Default 5 km. Beyond a few kilometres this stops being a nudge about the forecourt you are passing and becomes an errand. |
| **Open navigation in** | Which app the notification opens when you tap it — Google Maps, Apple Maps or OpenStreetMap. |

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
| `max_stations` | `0` (all) | Cap the number of rows in the table. "All" means everything the sensor publishes, which is itself the cheapest 50 — see below |
| `show_distance` | `true` | How far away each station is, above its price. Measured from your live position when the map is tracking it, otherwise from Home — the header says which |
| `sort` | `price` | `price` = cheapest first; `distance` = nearest first |
| `show_my_location` | `true` | Live GPS dot and the ◎ / ➤ buttons. `false` removes all three and never asks for your location |
| `ask_on_map` | `true` | The 📍 button: arm it, then tap the map to see prices at that point. Independent of `show_my_location` — it is not about where *you* are |
| `follow_me` | `false` | Start with follow-me armed |
| `follow_tracker` | — | A tracker to follow: the map centres on it and plots the stations fetched around it as it moves. Costs one search per refresh, throttled to a minute / 5 km; a parked car costs nothing |
| `show_cars` | `true` | Plot your configured cars on the map |
| `cars` | auto-detect | Explicit list of `…_days_until_refuel` entities to plot |
| `car_picker` | `true` | The 🚗 button: hide/show cars **on this device only** |
| `navigation` | `auto` | `auto` / `geo` / `apple` / `google` / `osm` / `off` |

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
- Turn the whole thing off: `show_my_location: false` removes the dot, both
  buttons and the GPS watch, so nothing on the card can ask for your position.

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

**The iPhone shortcut needs nothing set up here** — it asks the
`tankpriser.nearby` service with the phone's own position, so it works with
these two fields left empty. Skip to [11a](#11a-setting-up-your-iphone) if Siri
is all you want.

**The Assist prompt does need them**, because Assist cannot tell Home Assistant
where the phone is — it answers from the device nominated below, or from Home if
you nominate none. It is the shortest setup of the three:
[11f](#11f-ask-on-the-cars-own-screen--no-shortcut-no-token).

**Android Auto needs the sensors.** *Configure → Area & fuel types*, two fields:

| Field | Notes |
| --- | --- |
| **Rank stations near this device (phone or car)** | A `device_tracker`, `person` or `sensor` that carries latitude/longitude. Leave empty and the nearby sensors are not created at all. |
| **How far to look for nearby stations** | 1–100 km, default 15. |

You then get one `sensor.…_cheapest_nearby` per fuel:

```
sensor.tankpriser_blyfri_95_e10_cheapest_nearby
  state: 16.79                    ← what you pay, discounts included
  attributes:
    cheapest_station: "Q8 Hummeltoftevej 45"
    distance_km: 1.2
    latitude / longitude          ← only when the position is exact
    station_count: 23             ← how many are in range
    spoken_cheapest: "Billigste er Q8 Hummeltoftevej, 16,79 kroner, 1,2 kilometer væk."
    spoken: "Nummer 1: Q8 Hummeltoftevej, 16,79 kroner, 1,2 kilometer. …"
    stations: [ {name, company, city, price, distance_km, …}, … ]
    origin_latitude / origin_longitude   ← where this was measured from
    origin_source: "tracker"             ← or "zone:home", or "none"
    position_updated: "2026-07-26T14:03:11+02:00"
```

> **Copy your own entity id if you use these.** Home Assistant builds it from
> the *area name* you chose, so it is `sensor.tankpriser_…` only if you kept the
> default — name the area "Silkeborg" and it becomes
> `sensor.silkeborg_blyfri_95_e10_cheapest_nearby`. **Developer tools →
> States**, filter `cheapest_nearby`, and copy what is actually there.

**Android Auto then needs nothing more.** Parked, open the companion app →
**Settings → Companion app → Android Auto favorites** (newer builds call it
*Driving favorites*; from Home Assistant 2025.12 an entity's own **Add to** menu
does it too) and add the sensor. Android Auto renders a favourite sensor's state
in its driving list, and its navigation screen offers any entity carrying a
location — which this one does, pointing at the cheapest nearby forecourt. Your
`…_days_until_refuel` sensors work as favourites too.

> Those car sensors carry a position as well, so they appear in the navigation
> screen beside the stations — offering to route you to your own car. Harmless,
> and it is the same attribute the map draws the car from.

**iPhone takes more work**, because Apple does not let an integration install a
Shortcut for you. The rest of this section is that: phone settings first, then
the shortcut, built once in about five minutes.

#### 11a. Setting up your iPhone

Five settings decide whether any of this works in a car. None is obvious, and
each fails quietly in its own way — you get a confident answer about the wrong
place, or Siri stops mid-sentence. Set them before building anything.

| Setting | Where | Why it matters |
| --- | --- | --- |
| **Shortcuts → Location: While Using the App** | Settings → Shortcuts → Location | The shortcut reads the phone's position with Apple's own *Get current location*. Denied, it has nothing to ask about and the run fails at the first action. This is the **Shortcuts** app's permission, not Home Assistant's. |
| **Shortcuts → Precise Location: on** | same screen | Without it iOS hands over a coarse area, and "the cheapest station within 15 km" stops meaning anything. |
| **Never force-quit Home Assistant** | the App Switcher | The *Perform action* step belongs to the Home Assistant app, and iOS stops background-launching an app you have **force-quit** (swiped away) until you open it by hand. A phone restart does the same. Open it once afterwards and leave it alone. |
| **Siri Responses: Prefer Spoken** | Settings → Siri & Search → Siri Responses | On the default *Automatic*, Siri **prints** her answer whenever the ring switch is silent. A shortcut whose whole point is being heard then does nothing useful. |
| **Siri language** | Settings → Siri & Search → Language | Your shortcut's *name* must be words in this language. A Danish name spoken to an English Siri transcribes as nonsense, matches nothing, and gets web-searched instead. |

> **The old *Location: Always* requirement is gone.** Earlier versions of this
> guide had the shortcut push a position through the Home Assistant app and then
> read a sensor, which meant the app needed background location and a head start
> to report it. The shortcut now carries its own coordinates in the question, so
> Home Assistant's own location permission no longer affects the answer. It
> still matters for the `…_cheapest_nearby` sensors and Android Auto, which do
> follow the app's reported position.

<!-- 📸 SCREENSHOT S17 nearby-sensor.png — Developer Tools → Actions running tankpriser.nearby, showing the returned spoken_cheapest and urls -->

Two more things that are easy to assume otherwise:

- **Shortcuts has no CarPlay screen.** You cannot build, edit or even see
  shortcuts on the car display; in the car, voice is the only way to run one.
  Apple's design, not a limit of this integration.
- **The Home Assistant actions live inside Shortcuts**, not the other way round.
  Where a step says *search "Home Assistant"*, you are searching the Shortcuts
  action picker. They are there because the app is installed and signed in on
  that same iPhone — you never open the HA app while building this.

The **Shortcuts app is Apple's and comes with iOS** — a dark icon with two
overlapping rounded squares. Swipe down on the home screen and type `Shortcuts`.
If it is missing it was deleted at some point; reinstall it free.

Also have **Google Maps** installed (Apple Maps works too — see 11e).

#### 11b. The shortcut — eight actions

One shortcut, one behaviour: you ask, it names the cheapest station near you,
says it is taking you there, and starts the route. Nothing to choose, no number
to say, no prompt to answer — in a moving car the shortest useful exchange is
the right one.

It needs a **recent Home Assistant companion app**: the *Perform action* step
only returns an action's reply in newer builds. If your Shortcuts action picker
has no *Perform action*, or it hands back nothing, use the sensor-based build in
[11e](#11e-variants) instead.

1. On the **iPhone**, open **Shortcuts** and tap **+** for a new, empty one.
2. Tap its name at the top → **Rename** → **Billigste benzin**. *This is the
   phrase you will say to Siri*, so pick something you pronounce cleanly and
   that sounds unlike your other shortcuts. It must be words in **Siri's own
   language**. On an English Siri, call it *Cheap fuel*.
3. **Add action** → search `location` → Apple's **Get current location**.

   *This is the whole reason the shortcut is built this way: the position that
   answers the question is taken at the moment you ask it. There is nothing to
   go stale, and no "did the app report in?" step that can quietly be skipped.*
4. **Add action** → search `Home Assistant` → **Perform action**.

   Set **Server** to your Home Assistant, and **Action** to
   **`Tankpriser: Cheapest stations near a point`** (`tankpriser.nearby`). In its
   data/JSON field, type this:

   ```json
   {"latitude": , "longitude": }
   ```

   then put the cursor after each `:` and insert the **Current Location**
   variable from the bar above the keyboard — tapping the inserted chip lets you
   choose which detail it passes, so set the first to **Latitude** and the
   second to **Longitude**. Both must be the *number*, not the place name.

   Nothing else is required. `fuel`, `radius_km` and `maps` all have defaults —
   the first fuel your area is configured for, 15 km, and Google Maps links.
   [11e](#11e-variants) shows what to add if you want to change them.

   > **Look at what comes back, once.** Add a **Quick Look** action after this
   > one, run the shortcut on the phone, and read the dictionary: you should see
   > `spoken_cheapest`, `count`, `stations` and `urls`. That one look tells you
   > whether the rest of the shortcut is reading the right keys, and splits every
   > later problem in half. Some app versions hand over the whole reply, with the
   > answer nested under **`service_response`**; others hand you the answer
   > itself. **Whichever you see is what the next steps must use** — if yours is
   > nested, prefix the keys below with `service_response.`. Delete the Quick
   > Look once you know.

5. **Add action** → search `dictionary` → **Get dictionary value**. Set it to
   get **Value** for key `spoken_cheapest`, from the *Perform action* result.
6. **Add action** → search `Speak` → **Speak Text**. Tap its text field and pick
   **Dictionary Value** from the variable bar. Expand it (tap ⌄) and turn
   **Wait Until Finished** on.

   That switch is what makes the shortcut wait for her to finish the sentence.
   Without it, opening the map takes the audio and cuts her off mid-word.

   To have it say something of your own afterwards — *"Jeg sætter kurs mod den
   nu"* — type that into the same field after the variable. The sentence itself
   is built by the integration, with the house number dropped (unusable when
   heard) and a Danish decimal comma, so "16,19" is read as sixteen nineteen
   rather than "sixteen point one nine".

   **Danish or English is decided by Home Assistant, not by the iPhone**, under
   *Settings → System → General → Language*. On an English Home Assistant you
   get "kilometres" and *"16.19"*, which sounds odd next to a Danish shortcut
   name. Your own interface language is separate, in your user profile, so the
   dashboard can stay in English if you prefer it that way.
7. **Add action** → search `Wait` → **Wait**, set to **1 second**.

   When the shortcut ends, Siri says "OK" on top of it — her own
   acknowledgement, which nothing here produces and no setting suppresses.
   Without this pause the map opens over her and clips it. One second of silence
   lets her finish.
8. **Add action** → **Get dictionary value** again, this time for key `urls`,
   from the same *Perform action* result.

   Check that **in** really points at *Perform action* and not at the dictionary
   value from step 5 — Shortcuts offers the nearest output first, and here the
   nearest one is the wrong one.
9. **Add action** → search `list` → **Get item from list**, set to **First
   Item**.

   `urls` is index-aligned with `stations`, so its first entry is the route to
   the station she just named.
10. **Add action** → search `Open URLs` → **Open URLs**.

    Take Apple's plain **Open URLs**, *not* "Open URLs in Chrome" or any other
    app's version — a browser would open the link as a **web page**, and browsers
    are not CarPlay apps, so nothing would reach the car screen.

    Its input must be the **Item from List** chip, and it often arrives holding
    the wrong one: a **Dictionary Value**, which is the whole `urls` list and
    would fire off every route at once. To change it, tap the field, **backspace
    the chip away**, and tap **Item from List** in the strip above the keyboard —
    or **Select Variable** if the strip does not offer it. It is a chip you tap,
    never words you type: that field takes a URL, so typing the name is refused
    at the space.

    > Nothing named *Item from List* on offer? Then step 9 is not above this
    > action — add it, or drag it up. A variable only appears to actions that
    > come after the one producing it.

    Easiest fix of all when a field will not let go: delete **Open URLs** and
    add it again. Landing directly under *Get item from list*, it fills itself
    in correctly.
11. **Done.** The finished order is eight actions: Get current location →
    Perform action → Get dictionary value (`spoken_cheapest`) → Speak Text →
    Wait → Get dictionary value (`urls`) → Get item from list → Open URLs.

**Test it parked, on the phone.** Say *"Hey Siri, Billigste benzin"* with the
engine off: it should name one station and open Google Maps to it.

**Then test it in the car.** Connect CarPlay, press the **voice button on the
steering wheel**, say the name. The Shortcuts app never appears on the CarPlay
screen — voice is the only trigger.

<!-- 📸 SCREENSHOT S18 (optional) siri-shortcut.png — the finished Shortcut, or a photo of the CarPlay screen -->

#### 11c. When it does not work

| What happens | Why, and what to do |
| --- | --- |
| Siri: *"something went wrong"*, and the Home Assistant app was not running | The *Perform action* step belongs to that app, and iOS stops background-launching an app you have **force-quit** (swiped away in the App Switcher) until you open it by hand. A phone restart does the same until the first launch. Open Home Assistant once and leave it in the background. **Adding an "Open App" action does not fix this** — tested in a car: Siri opens the app and the shortcut stops there, because handing the foreground to another app ends the run. |
| The run stops at the first action, or names a station nowhere near you | **Shortcuts** has no location permission, or not a precise one. *Settings → Shortcuts → Location* (11a). This is the Shortcuts app's own permission — Home Assistant's makes no difference here. |
| *Perform action* errors, or the action picker has no such action | Check its **Server** field first, then that the action is `tankpriser.nearby`. If *Perform action* is not in the picker at all, the companion app is too old — update it, or use the sensor-based build in [11e](#11e-variants). |
| Speak Text says nothing, and the dictionary value is empty | The key path is wrong for your app version. Put a **Quick Look** after *Perform action* (step 4) and read the reply: if the answer sits under **`service_response`**, use `service_response.spoken_cheapest` and `service_response.urls`. |
| *Perform action* complains the data is not valid JSON | A variable went in as the location's *name* rather than its number, or the phone wrote the decimal with a **comma** — `56,17` is not a number in JSON. Tap the variable chip and pick **Latitude** / **Longitude** explicitly. |
| Siri starts saying something, gets cut off, and the map opens | **Wait Until Finished** is off on the Speak Text. Expand the action with ⌄ and turn it on — that is what makes the shortcut hold until she is done. |
| Siri starts to say "OK" and is cut off as the map opens | Hers, not the shortcut's — she acknowledges the run finishing, and the map takes the audio as it comes forward. The **Wait of 1 second** (step 7) gives her time to finish. |
| Siri: *"I don't see an app for that"* | The shortcut name is being misheard. Rename it to something more distinct and say it exactly. |
| Siri web-searches the phrase instead of running anything | She could not match what she heard to any shortcut. Almost always a language mismatch: **Settings → Siri & Search → Language**. Either set Siri to Dansk, or rename the shortcut to words in Siri's language. Saying *"kør \<name\>"* also helps her treat it as a shortcut rather than a query. |
| It shows the text instead of reading it aloud | Not the shortcut — **Siri Responses** is on *Automatic*, so she prints whenever the ring switch is silent. Set **Prefer Spoken Responses** (11a). |
| It runs, spins, then ends silently — no speech, no map | A **Dictate Text** action, left over from an older version of this guide. Siri holds the microphone for the whole run, so it waits for audio that never arrives. Delete it; nothing here needs it. |
| *"Ingen stationer i nærheden"* | Nothing sells that fuel within the radius. Not an error, and not a position problem — the service answers about wherever you actually are. Widen it with `radius_km` ([11e](#11e-variants)). |
| Speaks, then nothing happens | Google Maps is not installed, or that station's position is only estimated — those get an **empty** URL on purpose rather than being dropped, because dropping one would shift every station after it and navigate you to the wrong forecourt. Try the Apple Maps variant below. |
| Google Maps opens but asks you to choose a starting point | It opened a route *preview* and could not resolve your position by itself. Give **Google Maps** its own **Location** permission in iOS Settings; it does not inherit Home Assistant's. (The `dir_action=navigate` that prevents this is already in the URL the service builds.) |
| A web page opens instead of the map app | "Open URLs **in Chrome**" was used instead of Apple's plain **Open URLs**. |
| Nothing opens, and the spoken sentence appeared as a URL | **Open URLs** is pointing at the wrong variable — it must be the **Item from List** from step 8, not the first dictionary value. |
| **You are already navigating somewhere, and nothing happens** | Expected, and not fixable from a shortcut — see 11d. |

#### 11d. Already navigating? What that can and cannot do

If Google Maps (or Apple Maps) is **already running a route**, the shortcut
cannot slip a fuel stop into it. That is not a limitation of this integration,
and there is no way round it from a shortcut:

- iOS tells nothing — not Shortcuts, not us — **where you are currently being
  navigated to**. So even a route rebuilt as "station first, then destination"
  has no destination to put second.
- Neither Maps app has a URL that means *"add a stop to the route you are on"*.
  A maps link starts a **new** route, which is why an active navigation either
  ignores it or offers to replace what you were doing.

What does work while navigating is the map app's own feature: in **Google
Maps**, tap the **magnifying glass → Gas stations** with a route running and it
lists them along your way, adding one as a stop without losing your destination.
It will not know your prices or your loyalty discounts, so the useful pairing is
to **ask Tankpriser first, then pick that chain in Maps**.

Simplest of all: if you know you will want fuel, run the shortcut *before* you
start the route. It takes you to the pump; you set your real destination after.

#### 11e. Variants

The first three are one field in the *Perform action* JSON — no template to
edit, no second entity to find:

- **Apple Maps instead of Google:** add `"maps": "apple"`. `"osm"` gives
  OpenStreetMap directions.
- **A different fuel:** add `"fuel": "diesel"` (or `blyfri98`, `blyfri95plus`,
  `oktan100`, `dieselplus`, `hvo100`). A second shortcut with its own name gives
  you one per fuel.
- **Look further:** add `"radius_km": 30`. Anything from 1 to 100.

  ```json
  {"latitude": , "longitude": , "fuel": "diesel", "radius_km": 30, "maps": "apple"}
  ```

- **Just tell me, do not navigate:** delete the last three actions (the `urls`
  lookup, Item from List and Open URLs). If that is all you want, the Assist
  prompt in [11f](#11f-ask-on-the-cars-own-screen--no-shortcut-no-token) does
  the same thing with no shortcut at all.
- **Name three and let you choose one.** The reply also carries `spoken`, which
  names the three cheapest with distances, and `urls` is index-aligned with
  them — so "number two" is item 2 of that list. Building a shortcut that asks
  takes two more actions and an **Ask for Input**. It is deliberately not
  documented here: one station and a route is the version that works while
  driving.
- **No Home Assistant app at all.** Swap *Perform action* for Apple's **Get
  contents of URL**: method **POST** to
  `https://<your-ha>/api/services/tankpriser/nearby?return_response`, headers
  `Authorization: Bearer <a long-lived token>` and
  `Content-Type: application/json`, and the same JSON as the request body. Make
  a token under your Home Assistant profile → **Security** → *Long-lived access
  tokens*. The reply is nested here, so read `service_response.spoken_cheapest`.
  This removes the force-quit failure entirely, at two costs: the token sits in
  the shortcut, and **you must use an address that works away from home** —
  nothing is switching between your internal and external URLs for you.
- **The older, sensor-based shortcut.** Before `tankpriser.nearby` existed, this
  guide pushed a position through the app with *Update location*, waited two
  seconds, and read `spoken_cheapest` off the `…_cheapest_nearby` sensor with
  two **Render template** actions. It still works if you have the sensors
  configured, and it is the fallback if your companion app is too old for
  *Perform action*. The template was
  `{{ states.sensor.tankpriser_blyfri_95_e10_cheapest_nearby.attributes.spoken_cheapest }}`
  — dotted, with no quote marks, because a `'` copied through a phone comes back
  curled and Jinja cannot parse it. Two traps came with it: a second *Render
  template* action arrives with its **Server** field pre-filled from the
  previous action's output and fails every time until you set it, and — the one
  this section exists to avoid — if the app has not reported a position
  recently it answers confidently about the town you left, with nothing on
  screen to say so.

**Verified in a car, 2026-07-26** — on the sensor-based build above. The
service-based shortcut in 11b is the same exchange with that stale position
designed out; it was **built and run on an iPhone on 2026-09-20**, spoke its
sentence and opened the route, on a companion app that returned the reply
**bare** — no `service_response` prefix needed. In a car it is still new:
**test it parked before trusting it on a motorway.**

#### 11f. Ask on the car's own screen — no Shortcut, no token

CarPlay has four tabs, and it will act on nine kinds of entity: `button`,
`cover`, `fan`, `light`, `lock`, `scene`, `script`, `switch` and friends. **A
sensor is not one of them**, and no car platform lets Home Assistant draw a map
— so neither the card nor the `…_cheapest_nearby` sensors can appear on an
Apple car screen at all. That is why [11b](#11b-the-shortcut--eight-actions)
exists.

But there is a second thing CarPlay will pin to Quick Access: an **Assist
prompt** — a fixed sentence, one tap, answered out loud on the car's screen.
Tankpriser registers an Assist intent for it, so the tap answers with the same
sentence Siri reads: *"Billigste er OK Nordre Ringvej, 16,19 kroner, 1,9
kilometer væk."*

No Shortcuts app, no long-lived token, no *Get contents of URL* — three of the
four things that can silently break the shortcut are simply not in this path.
**The fourth one is**, and it is the trade: Assist has no idea where your phone
is, so the answer is measured from the device you nominated above, not from a
position the phone hands over as it asks. Nominate your phone and it is right
whenever the companion app has reported recently. In a tunnel, or after a
force-quit, it will confidently answer about where you were. If that matters
more to you than the setup cost, use [11b](#11b-the-shortcut--eight-actions).

**1. Teach Assist the sentences.** Home Assistant only reads them from your own
config folder, so an integration cannot ship them. Create
`config/custom_sentences/da/tankpriser.yaml` (File editor, Studio Code Server or
Samba) with:

```yaml
language: "da"
intents:
  TankpriserCheapest:
    data:
      - sentences:
          - "billigste brændstof"
          - "hvor tanker jeg billigst"
          - "hvor er det billigst at tanke"
      - sentences:
          - "billigste {fuel}"
          - "hvor er {fuel} billigst"
          - "hvor køber jeg billigst {fuel}"
lists:
  fuel:
    values:
      - "benzin"
      - "blyfri 95"
      - "blyfri 98"
      - "diesel"
      - "diesel extra"
      - "hvo"
```

In English, the same file under `config/custom_sentences/en/`:

```yaml
language: "en"
intents:
  TankpriserCheapest:
    data:
      - sentences:
          - "cheapest fuel"
          - "where is the cheapest fuel"
      - sentences:
          - "cheapest {fuel}"
          - "where is the cheapest {fuel}"
lists:
  fuel:
    values:
      - "petrol"
      - "diesel"
      - "diesel extra"
      - "hvo"
```

Restart Home Assistant, then test it by typing *billigste diesel* into Assist on
your dashboard. You should get the sentence straight back.

**2. Pin it in CarPlay.** On the iPhone: companion app → **Settings → CarPlay**
→ *Quick access*, **Add Assist prompt**, and type the sentence exactly as above.
It then sits on the Quick Access tab as a button; tapping it asks and speaks the
answer.

**3. Or say it.** The same sentence works anywhere Assist listens — the app's
Assist button, a Voice Preview Edition puck, a wall tablet. `"Hey Siri"` still
needs the shortcut in 11b; Assist is not a Siri trigger.

A few things worth knowing:

- **A fuel you do not follow is said so, not substituted.** Ask for HVO100 on a
  setup that tracks petrol and diesel and it answers *"Jeg følger ikke HVO100"*
  rather than quoting the petrol price — an answer that sounds entirely right
  and is about the wrong pump is the one mistake you cannot catch while driving.
- **Both country vocabularies work near a border.** With a German entry
  configured, *"billigste super e10"* and *"billigste blyfri 95"* ask for the
  same pump.
- **No device nominated?** It answers from your Home location, which is what the
  price sensors describe anyway.

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

**The prediction card:** Add card → **"Tankpriser Prediction"**. Pick your cars
in the editor's *"Which cars"* field — it lists only cars, and takes **as many as
you like**. Added from the picker it starts with all of them.

```yaml
type: custom:tankpriser-prediction-card
entities:
  - sensor.passat_days_until_refuel
  - sensor.polo_days_until_refuel
```

Each car gets its own block, named, with its own gauge and figures; the donation
ask appears once for the whole card rather than once per car. With a **single**
car the card titles itself with that car's name and the block heading is
dropped, so nothing is said twice:

```yaml
type: custom:tankpriser-prediction-card
entity: sensor.passat_days_until_refuel   # the single-car form still works
title: Passat            # optional; defaults to the car's own name, "" for none
```

Prefer one card per car — different dashboard positions, or a card each in
separate columns? That still works; add the card once per car.

<!-- 📸 SCREENSHOT S20 prediction-card.png — the prediction card with the tank gauge, ideally in "ready" state -->

#### 12a. How the prediction learns

Worth reading once, because it explains every number the card shows you and
every case where it politely refuses to give one.

**It learns from refuel to refuel.** Everything between one fill-up and the next
is a *tank*: how many litres went in, how long they lasted, and — if you gave it
an odometer — how far they took you. A tank is the natural unit because it is
self-correcting: it already averages your busy days with your quiet ones, and it
needs no assumption about how you drive.

**A refuel is a jump upwards.** When the level rises by at least **15 % of the
tank** in one go, the previous tank is closed and a new one begins. That
threshold is why a **small top-up is ignored**: five litres to get home reads as
noise, not as the start of a new tank, and treating it as one would throw away a
perfectly good half-finished measurement.

**The estimate leans on your recent driving.** Completed tanks are averaged with
an exponential weighting — the newest counts about twice as much as the one
before it — so a change of job or a holiday shows up within a tank or two rather
than being diluted by a year of history. On top of that, the tank *you are on
right now* is folded in continuously, weighted by how much time it covers, so a
new pattern reaches the number in days. That weighting is the whole trick for
irregular driving: one busy Saturday nudges the estimate; a busy fortnight moves
it.

**`days_until_empty` is then just division** — litres in the tank divided by
litres a day. With an odometer it also reports **L/100 km**, which is the figure
you can sanity-check against the car's own trip computer.

**Confidence** (0–1, in the attributes) is two things multiplied: how many tanks
it has learned from, approaching its maximum at **six**, and how *consistent*
those tanks were. Wildly varying tanks keep confidence low no matter how many
there are, which is the honest answer — a car driven unpredictably genuinely is
harder to predict. A single partial tank is capped at **0.3** however good it
looks.

**Why it sometimes says nothing.** Before two completed tanks it will not claim
`ready`, and before **3 days *and* 5 % of a tank** it will not give a number at
all. Both refusals are deliberate: a car parked for three days would otherwise
report "empty in nine years", and a single long trip would be projected as a
daily habit. `tankpriser.seed_demo_history` fills in synthetic tanks if you want
to see the card populated immediately — it **overwrites** real history, so use it
on a car you are only experimenting with.

**If you change the tank size**, run `tankpriser.reset_history` for that car:
every stored tank was measured as a fraction of the old capacity.

#### 12a-ii. And while the car is running

None of the above applies to the driving answer, which is measured rather than
learned. It appears on its own, with no setup, whenever three things are true:

- the car has **reported recently** — within 20 minutes. Readings are only
  stored when the level actually changes, so a parked car goes quiet and that
  silence is the signal. No ignition or speed entity is needed;
- the readings span at least **10 minutes**, so the rate is not dividing by
  almost nothing;
- and at least **1,5 % of the tank** has gone, because a fuel sender is coarse
  and a parked car on a slope wanders. Below that it is noise, not a journey.

It measures over the **last two hours** — long enough to average out a slosh,
short enough to still describe *this* drive rather than this morning's. Anything
before your last fill-up is excluded, or a refuel inside the window would read
as the car un-burning forty litres.

With an odometer it also reports **L/100 km and km/h for the current drive**,
which is the figure you can check against the car's own trip computer.

### 12d. Tuning and checking the prediction

*([what this feature does](#12c-self-correcting-predictions))*

**Configure → Refuel prediction.** Both settings need at least one
[car](#12-adding-a-car-for-prediction); neither affects fuel prices.

| Field | Notes |
| --- | --- |
| **Learn from past predictions and correct future ones** | **On by default.** After each refuel the prediction is compared with what the tank actually did; a lean shared by the *typical* tank, over five or more of them, is corrected by half its size, capped at 25 %. A holiday's worth of unusual tanks is ignored. Turn it off to see the raw model. |
| **Show me how accurate the prediction has been (advanced)** | Off by default. Adds the `tankpriser.prediction_accuracy` action and makes the accuracy card work. Until this is on the action does not exist at all — it is not in the action picker and not in the API. |

**Reading the marking.** With the second option on, add the card:

```yaml
type: custom:tankpriser-accuracy-card
# car: Passat        # optional; blank grades every car
# show_tanks: false  # optional; hides the per-tank table
```

Or call `tankpriser.prediction_accuracy` from **Developer tools → Actions** for
the same thing as data.

The card creates no entity, so it exists only where you put it. It is still a
card, though — anyone who can see that dashboard can see it. Put it on a view of
your own, or give it a `visibility:` condition, if other people use your Home
Assistant.

**How a tank is graded, and the trap it avoids.** The sensor answers "days until
empty", but nobody drives to empty — you fill up at a quarter tank, or wherever
you like. So comparing "we said 11 days" against "you refuelled after 8" would
be measuring your refuelling *habit*, and would report a perfect model as wildly
optimistic for ever. What is graded instead is the **rate**:

> At the start of a tank, using only the tanks *before* it, the model believed
> some litres per day. That tank then really burned 46 L over 9,4 days. So: how
> long would the model have said those 46 L would last? It would have said 11,2.
> That is **+19 %** — optimistic.

Each tank is marked by a model that had not yet seen it, which is the only
honest way to grade a model on its own history. **Positive means optimistic** —
the fuel did not last as long as predicted, which is the direction that leaves
you walking.

Because your completed tanks are already stored, this works **backwards over the
history you already have**. You get a verdict the first time you look, not after
months of collecting.

| What you see | What it means |
| --- | --- |
| **lean** | The average *signed* error — the headline. Near zero means no consistent bias, whatever the individual tanks did. |
| **typical error** | The average size of the error, ignoring direction. High with a low lean = irregular driving, not a broken model. |
| **worst** | The single furthest-off tank. |
| **within 10 %** | How many tanks landed close enough to plan a week around. |
| **trend** | Recent errors minus earlier ones. Negative means it is getting better as it learns. |
| **▾ on a date** | That tank ended below 10 % — you cut it fine, so being optimistic actually costs you something. |

A car with one refuel shows nothing, and says so: a tank can only be graded
against the tanks before it.

**When the correction does nothing, and why that is right.** On even driving the
factor sits at 1.0 — there is no lean to remove, and the report says so in
words. It also stays at 1.0 through a holiday, and through the two or three
tanks it takes the prediction to absorb a *permanent* change of habit such as a
new job: that lag is the estimate catching up, it resolves on its own, and a
correction fitted to it would still be leaning long after the model had
recovered. What the correction is for is a bias that never resolves — a car
whose use keeps drifting the same way, where the estimate is permanently
averaging a past that no longer applies.

It is also updated only when a tank closes, because that is when new evidence
exists. Grading more often — nightly, hourly — would re-derive the same number
from the same tanks; there is nothing to learn from between two refuels.

### 13. Entering a source API key

*([what this feature does](#13-sources-that-need-an-api-key))*

**Configure → Chains & API keys** — the menu entry only appears when a source
for *this entry's country* needs a key. Pick the source, follow the guide in the
dialog, paste the key (it is checked immediately) and save. Clearing the field
removes the key and stops using that source.

For **Go'on**, the form at
[goon.nu/faa-adgang-til-api](https://goon.nu/faa-adgang-til-api/) wants an
e-mail address and nothing else; the key is issued automatically. One trap worth
knowing: Go'on allows **one request per key per 30 seconds**, so testing a key
twice in quick succession is refused and the dialog says it cannot connect even
though the key is fine. Wait half a minute and save again.

For **Uno-X**, apply at
[unoxmobility.dk/privat/braendstofpriser](https://unoxmobility.dk/privat/braendstofpriser#pris-api)
or write to info@unox.dk with the subject *Adgang til pris-API*. A person there
approves it, so unlike Go'on's it does not arrive by return mail. What arrives is
**two** values, a client id and a client secret; paste them into the one field
joined by a colon —

```
client_id:client_secret
```

— exactly as the Uno-X documentation writes it for `curl -u`. Home Assistant
exchanges them for a 15-minute token before each fetch and holds the token until
it is nearly spent, so the secret itself is sent to one place only. Uno-X has the
same **one request per key per 30 seconds** limit as Go'on, with the same
consequence for saving twice in quick succession.

### 14. Germany

*([what this feature does](#14-germany))*

**Getting the key.** Register at
[creativecommons.tankerkoenig.de](https://creativecommons.tankerkoenig.de/#register)
with your name and e-mail, and confirm that you are not an oil company, a
station operator or an IT supplier to either — those are barred from this data
by the licence, not by us. The key arrives by e-mail but **does not work yet**:
a person at Tankerkönig activates it by hand, which can take days. Until then
every request is answered with *"Key existiert nicht oder ist deaktiviert"*,
and Home Assistant will report it as a rejected key, because from the outside
the two are identical.

**The point you search from.** *Configure → Area & fuel types → Search from this
point.* It has to stand still, and that is deliberate: the background sensors,
the price history and the notifications all compare one refresh with the next,
and an area that followed your phone would swap the entire station list every
time you drove anywhere — firing "price changed" for a hundred stations while
no price moved. Asking out loud is unaffected: that always searches from where
you actually are.

**The radius** offers up to 25 km and defaults to it, because that is
Tankerkönig's hard ceiling and one request buys the same answer whatever size
you ask for. There is nothing to save by asking for less.

**What one German refresh costs:** exactly one request, every poll. The default
30-minute interval is 48 requests a day, comfortably inside what the source asks
for. Asking out loud costs one to four more, once, at the moment you ask.

**Prices are shown to three decimals** and compared at full precision, so a
`1,699` still trips a "below 1,70" rule that a rounded `1,70` would not. The
spoken sentence rounds to two — nobody reads out the 9/10 of a cent.

**Loyalty discounts do not apply.** They are configured in øre off a Danish pump
price, and are ignored for German stations rather than quietly subtracted from a
euro price.

### 15. The Netherlands, Belgium, Luxembourg and France

*([what this feature does](#15-the-netherlands-belgium-luxembourg-and-france))*

**Add Tankpriser again and pick the country.** For **the Netherlands, Belgium
and Luxembourg** that is all of it: no key, no anchor point to choose — these
three answer for the whole country at once, so the map and the nationwide
"cheapest near me" work the same way they do in Denmark.

**France also asks for an anchor point and a radius**, like Germany, because no
single request can cover it — see
[what this feature does](#15-the-netherlands-belgium-luxembourg-and-france).
Pick the 50 km radius it offers; a smaller circle costs the same one request and
finds less. There is no nationwide French map, and no French "cheapest in the
country" sensor.

Fuels offered there are the ones those forecourts actually sell: **Euro 95
(E10)**, **Super 98 (E5)**, **Diesel (B7)**, **Premium diesel**, **LPG** and
**CNG** — named the local way, so France offers **SP95-E10**, **SP98-E5**,
**Gazole (B7)** and **GPL**. Prices are in euro and shown to three decimals, the
way the signs are written.

**Driving Rotterdam → Silkeborg** takes three entries — one Dutch, one German,
one Danish — and no switching: `tankpriser.nearby` answers from the country your
position falls in, so the same Siri shortcut or Assist prompt speaks euro on the
way and kroner when you arrive. Near a border both sides answer, each in its own
currency, and they are never added up or compared — the one with the **nearest
forecourt** is named first and is the one a Siri Shortcut will navigate to,
since that is the only thing about two countries' prices that can be compared
without inventing an exchange rate.

**A trip through the Ardennes wants four**, because Luxembourg and the French
border sit inside Belgium's box: without entries of their own they are answered
by a neighbour that has nothing there. You will be told rather than left
guessing — the pin says *"No prices here: Luxembourg is not set up in
Tankpriser"* — but the fix is to add the country.

Two limits of the middle leg, neither new: Germany's Tankerkönig answers only
about a 25 km circle, so there is no national German map, and it needs
[its own free key](#14-germany).

### 15b. Austria

*([what this feature does](#15b-austria))*

**Add Tankpriser again, pick Austria, and give it an anchor point and a
radius.** No key and no account: E-Control publishes openly. Like Germany and
France it searches around a point, so there is no nationwide Austrian map and
no Austrian "cheapest in the country" sensor.

**Set the radius to the largest you would actually drive.** It does not shape
the request — E-Control decides how far to look and stops at ten stations per
fuel — so the radius only filters the answer. Set it too small and a real
station that came back is thrown away; set it generously and you keep what
there is.

Fuels offered are the three Austria publishes: **Super 95**, **Diesel** and
**CNG**. There is no Super 98 and no premium diesel in this feed, so neither is
on the list. CNG is priced per kilogram and is kept out of every comparison
with a litre price.

**Expect a shorter list than Germany's.** Ten per fuel is the ceiling on every
answer, so an Austrian area sensor lists ten forecourts where a German one
lists fifty, and a sparse Austrian map is the source being honest rather than
something failing. Only **open** forecourts are listed, which is also
deliberate — [15b](#15b-austria) has the measurement that decided it.

**A Munich → Graz drive wants two entries**, one German and one Austrian. The
boxes overlap along the whole border, which is intended: the country with the
nearer forecourt answers, each in its own currency, and neither is converted
into the other.

### 16. Simulating a drive

*([what this feature does](#16-testing-the-driving-features-without-driving)).*

First, point your area at the simulated car: *Configure → Area & fuel types →
Rank stations near this device* → `device_tracker.tankpriser_sim`. Without this
the drive happens and nothing watches it; the log says so when you start one.

Then, in **Developer tools → Actions**:

```yaml
action: tankpriser.simulate_drive
data:
  route: de_north_south     # Flensburg to Munich, ~830 km
  speed_kmh: 130
  interval: 60              # real seconds between steps
  time_scale: 60            # 60 s of driving per real second
  announce: true            # ask, and log what would be said
```

**`time_scale` is what makes a long route watchable.** At `1` the car drives in
real time, so 135 km at 130 km/h takes the hour it would really take — which is
why nobody ever ran the long routes. At `60`, one real minute covers a simulated
hour, and Flensburg to Munich takes about six minutes instead of six hours.

The car still *reports* the speed you set, so the corridor is planned for
130 km/h and not for the compressed figure — only the waiting is squeezed.
Announcements stay limited to one every 30 real seconds however fast the car
moves, so speeding the drive up does not multiply the requests against your API
key.

Watch it in *Settings → System → Logs* (set `custom_components.tankpriser` to
info), where every step prints the position, the sentence, how many circles were
searched and how far:

```
Tankpriser simulation @ 220 km (52.9014, 9.8321): Billigste er Raiffeisen
Osterminnerweg, 2,19 euro, 23,6 kilometer væk. [408 found, 3 circles, 135 km
searched, moving]
```

Routes shipped: `de_north_south`, `de_west_east`, `dk_de_border`,
`dk_north_south`. Or give your own `waypoints: [[lat, lon], …]` — straight lines
are driven between them, so add one wherever the road actually turns.

`announce: true` is what makes the test worth running, and it is also what costs
requests: each step asks for a real answer, which in Germany is one to four
requests against your key. A 60-second interval is about four a minute at worst.
Set `announce: false` to move the car and spend nothing. `tankpriser.stop_simulation`
stops it and parks the car where it got to.

---

## Entities and attributes

Entity ids below assume the default name "Tankpriser"; if you named the
integration something else, that name is used instead.

### `sensor.tankpriser_<fuel>` — one per tracked fuel

**State:** the cheapest price in your area, in the country's unit — kr./L in
Denmark, €/L in Germany. The state carries the full figure; `country` and
`price_decimals` in the attributes say how to write it.

| Attribute | Meaning |
| --- | --- |
| `stations` | The cheapest stations in range, up to 50: `name`, `company`, `address`, `city`, `postnummer`, `price`, `list_price`, `discount_ore`, `updated`, `latitude`, `longitude`, `coord_approx` |
| `cheapest_station`, `cheapest_price` | The winner |
| `average_price` | Mean across the area — every station, not just the listed ones |
| `station_count` / `listed_count` | How many sell this fuel here / how many are in `stations` above |
| `discounted` | `true` if any price here has one of your loyalty discounts applied |
| `area`, `radius`, `fuel_type`, `fuel_key` | What this sensor covers |

**Why `stations` stops at 50.** Home Assistant's recorder refuses to store a
state whose attributes exceed 16 KB — it does not shorten them, it stores none
of them and logs a warning, so the sensor keeps working while its *history*
holds nothing. A 10 km area in Denmark was never close to that line. A 50 km
French one is: around Lyon it is roughly 400 forecourts and 110 KB. The list is
cheapest-first, so the 50 that survive are the 50 the list exists to show, and
`station_count` still tells you the real total. A handful of very long station
names can trim it below 50 — `listed_count` says what actually went in.

### `sensor.tankpriser_<fuel>_cheapest_nearby` — only when a device is nominated

**State:** the cheapest price within the nearby radius of that device.

| Attribute | Meaning |
| --- | --- |
| `stations` | Up to 15, cheapest first, each with `distance_km` |
| `spoken_cheapest` | The **single** cheapest as a ready-to-speak sentence, in HA's language — the same sentence `tankpriser.nearby` returns to the Siri shortcut |
| `spoken` | The three cheapest as a ready-to-speak sentence, for a shortcut that lets you choose |
| `cheapest_station`, `cheapest_price`, `distance_km` | The winner |
| `latitude`, `longitude` | The winner's position — **this is what Android Auto navigates to**. Omitted when the position is only estimated |
| `station_count` / `listed_count` | How many are in range / how many are listed above |
| `tracked_entity`, `radius_km` | What "nearby" means here |
| `origin_latitude`, `origin_longitude`, `origin_source` | The position this ranking was measured from, and where it came from: `tracker` (the device's own coordinates), `zone:home` (it reported a zone instead), or `none` |
| `position_updated` | When that device last told Home Assistant anything. If it is old, so is the answer. Only the sensors are exposed to this — the Siri shortcut carries its own fresh position, see [11b](#11b-the-shortcut--eight-actions) |

### `sensor.<car>_days_until_refuel` — one per car

**State:** days until the tank is empty, or `unknown` while learning.

| Attribute | Meaning |
| --- | --- |
| `status` | `learning` / `estimating` / `ready` |
| `current_level_l`, `current_level_percent`, `tank_capacity_l` | Where the tank is now |
| `avg_consumption`, `consumption_unit`, `method`, `basis` | What it learned and how |
| `learned_tanks`, `confidence` | How much it has to go on (0–1) |
| `calibration` | The [self-correction](#12c-self-correcting-predictions) in force. `1.0` = none. Above 1 means this car burns faster than the raw model believed, so the projection was shortened. |
| `mode` | `driving` or `parked` — whether the live figures below are present |
| `hours_until_empty`, `live_empty_at` | While driving: how long this tank lasts at the rate it is going, and roughly when it runs out. Measured, not predicted, so no correction applies |
| `live_consumption`, `live_consumption_unit` | The current burn, in L/h |
| `live_l_per_100km`, `live_speed_kmh` | The current drive's efficiency and average speed, when an odometer is configured |
| `live_measured_over_hours` | How much driving that reading was taken from, so a doubtful figure can be judged |
| `predicted_empty` | ISO timestamp |
| `cheapest_station`, `cheapest_price` | For this car's fuel, in your area |
| `latitude`, `longitude`, `car_picture` | Present when the source entity supplies them — this is what puts the car on the map |
| `source_entity`, `level_attribute`, `odometer_entity` | Exactly which config is in use, for debugging |

<!-- 📸 SCREENSHOT S21 device-page.png — the Tankpriser device page listing the sensors and the car sub-entries -->

## Services, events and diagnostics

| Service | What it does |
| --- | --- |
| `tankpriser.nearby` | The cheapest stations around a position **you supply**, returned directly to the caller — a spoken sentence, the ranked stations, and one navigation URL per station. No entity, no device tracker, nothing that can be stale in between. Fields: `latitude`, `longitude` (both required), `fuel`, `radius_km`, `maps`, `limit`. Returns `spoken_cheapest`, `spoken`, `stations` and `urls`, plus what was
actually searched: `searched_km`, `circles`, `moving`, `speed_kmh`, `course_deg`
and `motion_source`. `radius_km` is the *parked* radius and is ignored where the
source imposes its own ceiling or where you are moving — the shape of the search
is worked out, not asked for. The *position* is the one you hand in; how fast
you are moving is read from whichever configured tracker is standing at that
position, whatever entry it belongs to. If none of them is there, the answer is
a circle rather than a corridor, which is the honest reading of "we cannot tell
which way you are going". `limit` is how many stations come back, cheapest
first — 15 by default, up to 100. The spoken sentence names three whatever it
says, so raise it when you want a map full of forecourts rather than something
to read aloud; `count` is always the real total found, however few are
returned. This is what the Siri shortcut in [11b](#11b-the-shortcut--eight-actions) calls, through the companion app's *Perform action*; Apple's *Get contents of URL* with a long-lived token reaches it without the app at all. Also for automations that announce prices unprompted. |
| `tankpriser.seed_demo_history` | Injects synthetic tanks into a car so the prediction shows a number immediately. For testing and demos — **it overwrites learned history**. Fields: `car` (blank = all), `tanks`, `litres_per_day`, `days_per_tank` |
| `tankpriser.reset_history` | Clears a car's learned history, returning it to `learning`. Use after changing the tank size, or to undo a demo seed. Field: `car` (blank = all) |
| `tankpriser.simulate_drive` | Drives a virtual car along a route, writing positions, speed and heading onto a tracker entity so the corridor search and the spoken answer can be tested without driving. Fields: `route` **or** `waypoints`, `speed_kmh`, `interval`, `tracker`, `announce`, `loop`, `fuel`. See [16. Simulating a drive](#16-simulating-a-drive) |
| `tankpriser.stop_simulation` | Stops the running simulation and parks the car where it got to, with its speed set to zero so nothing goes on believing it is moving |
| `tankpriser.test_notification` | Rehearses a price drop and sends the notification it would produce, titled `… (test)`. Checks the rule, the threshold and the notify service in one call, instead of waiting for the chains to move. If nothing can be sent it tells you which of those is the reason. Field: `drop_ore` (how much cheaper to pretend, default 10 øre/L) |

| Intent | What it does |
| --- | --- |
| `TankpriserCheapest` | The Assist intent behind the CarPlay prompt. Optional slot `fuel` (free text — "benzin", "diesel extra", "super e10" are all understood, in Danish, English or a country's own name for the pump). Answers from the nominated device, or Home. Its sentences live in your own `custom_sentences/` folder — [11f](#11f-ask-on-the-cars-own-screen--no-shortcut-no-token) has both languages ready to paste. The reply also carries `stations` and `urls` as speech slots, so an automation firing the intent need not call the service again. |

| Event | Payload |
| --- | --- |
| `tankpriser_price_updated` | `entry_id`, `area`, `radius`, `station_count` — fired after every successful refresh |
| `tankpriser_simulation_step` | `entity_id`, `latitude`, `longitude`, `course`, `speed_kmh`, `travelled_km`, `total_km` — fired at every step of a simulated drive, so an automation can watch one without reading the log |

**Diagnostics:** the integration's ⋮ → *Download diagnostics* gives the entry
config, the resolved area and the current station data. API keys, the
integration's name and your notify service are redacted; attach it to a bug
report as-is.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Sensors have no stations | **Home location not set** (*Settings → System → General*), or your radius is small and rural. The log says `No HA Home location set` in that case. The map is unaffected in `national` coverage — it does not use the radius. |
| The map shows stations far outside my radius | That is `coverage: national`, the default: the viewport is the filter, not your radius. Set `coverage: area` to pin it. |
| Germany: "the source rejected that key" | Almost always a key that has not been activated yet — that is done by hand at Tankerkönig and can take days. The refusal is identical to a wrong key, so check the activation mail has actually arrived before assuming you mistyped it. |
| Germany: no stations at all, and the key is fine | The point you search from is somewhere with no forecourts inside 25 km — or in another country. *Configure → Area & fuel types → Search from this point*. |
| Germany: the map only shows a small area | That is the whole map there is. Tankerkönig answers only about a circle of at most 25 km, so there is no nationwide list to plot and none can be built. |
| Siri says *parked* while you are clearly driving | Nothing configured is following the device you are asking from. Motion is worked out by comparing the position you hand in against a tracker standing in the same place; with no tracker there, the search is a circle around you and says so. Nominate the phone under *Configure → Area & fuel types → Rank stations near this device* on any one entry — it does not have to be the entry for the country you are in. |
| A simulated drive changes nothing | The area is not following the simulated car. *Configure → Area & fuel types → Rank stations near this device* → `device_tracker.tankpriser_sim`. The log warns about this when the drive starts. |
| A chain you expect is missing | OK, Q8, F24, Shell, OIL! and Circle K / INGO publish openly; **Go'on and Uno-X each need a free key** you paste under *Chains & API keys* — Uno-X's is a `client_id:client_secret` pair. A chain that fails for more than 6 hours also drops out on purpose rather than showing stale prices. |
| Every Tankpriser card is a small red error box on one device, and fine on the others | That device is not loading the card script. Fully close and reopen the HA app there (on iPhone/iPad: *Settings → Companion app → Debugging → Reset frontend cache*), or hard-refresh the browser. If it comes back, check *Settings → Dashboards → ⋮ → Resources* lists `/tankpriser/tankpriser-card.js` — if it does not, the integration logs a warning saying so at startup, and adding it by hand as a **JavaScript Module** is the fix. On an iPad, one build of the companion app refused to load any custom card while Safari on the same iPad loaded them all; if you meet that, open the dashboard in Safari and add it to the Home Screen. |
| The card says "Configuration error" | Same cause as the row above: a browser holding an old `index.html`. |
| The map is blank/grey but markers show | The background tiles are blocked (no internet, or a DNS/ad blocker). The prices are unaffected; `show_map: false` removes the dependency. |
| No blue position dot | HA served over plain `http`, or location permission denied for the site. Browsers disable geolocation on `http`. |
| A station has a dashed `≈` pin and no navigate button | Its position is only estimated — deliberately not handed to a navigator. |
| No `…_cheapest_nearby` sensors | No device nominated under *Configure → Area & fuel types*, or the entity you chose carries no latitude/longitude. |
| `…_cheapest_nearby` names stations in the town you left | The nominated device has not reported its position recently, so the answer was measured from wherever it last checked in. The candidates are nationwide, so a wrong town means a wrong position: check `origin_latitude` / `origin_longitude` / `position_updated` on the sensor. This affects the sensors and Android Auto only; the Siri shortcut does not read them. |
| Card distances measured from the wrong place | With no live position the card measures from Home — the header says `from home`. It switches to `from you` only once the map's position dot has a fix, which needs HTTPS and permission. |
| Prediction stuck on `learning` | It needs ≥ 3 days *and* ≥ 5 % of the tank consumed. A parked car never leaves this state. `tankpriser.seed_demo_history` shows what the card looks like meanwhile. |
| Prediction looks wrong after changing tank size | Run `tankpriser.reset_history` for that car. |
| The fill-up alert never fires | It needs all three at once: a car below the level you set, a station within the distance you set, and a notify service. It is also deliberately quiet — once per low tank, and not again until the car has been filled. The log at debug level says which of those stopped it. |
| Notifications never arrive | Run `tankpriser.test_notification` — it rehearses a 10 øre drop and either delivers one or names the reason it cannot. Reloading the integration is *not* a test: that clears the comparison baseline, so the first refresh after a reload is deliberately silent. A notify service belonging to a phone or tablet you have since removed is the common cause. |

More depth — logs, installing a build by hand, running the test suite — is in
[`docs/TESTING.md`](docs/TESTING.md).

## Privacy and data sources

- **Prices** come from each chain's own public API: OK, Q8/F24, Shell, OIL!,
  Circle K / INGO and — once you add their keys — Go'on and Uno-X. The Netherlands, Belgium
  and Luxembourg come from ANWB's own service, and so does France — one circle
  at a time, since no single request covers it.
  Each is fetched nationwide, cached for 10 minutes and shared by everything in
  the integration, so a shorter poll interval does not multiply requests. The
  User-Agent identifies this integration honestly, with a link, rather than
  impersonating a browser.
- **German prices** come from
  [Tankerkönig](https://creativecommons.tankerkoenig.de/), the free consumer
  feed of the Bundeskartellamt's Markttransparenzstelle für Kraftstoffe
  (MTS-K), licensed **[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  — attribution required**, which is what this paragraph is. Each user's own
  Home Assistant fetches with that user's own key; nothing is proxied through
  us and no copy of the dataset is redistributed, which the licence forbids
  under penalty. Oil companies, station operators and their IT suppliers are
  barred from using this data — if that is you, do not use the German half of
  this integration.
- **Austrian prices** come from
  [E-Control](https://www.spritpreisrechner.at/), the Austrian energy
  regulator, published under the Preistransparenzverordnung. No key and no
  account, and the regulator's own app makes the same requests. Each fuel is a
  separate request and each answer is at most ten stations, so an Austrian
  refresh costs three requests per circle rather than one — the same 10-minute
  cache covers them.
- **Geography** comes from [DAWA](https://dawadocs.dataforsyningen.dk/) — free,
  keyless, run by the Danish state — for postnummer/radius resolution and for
  geocoding station addresses. Geocodes are cached for 180 days. Germany needs
  neither: every German station arrives with exact coordinates already.
- **Nothing about you is sent anywhere.** No account, no telemetry, no
  third-party analytics. Your position never leaves the browser: the blue dot is
  drawn locally.
- **The one external request your browser makes** is for map tiles, from
  OpenStreetMap. Those reveal your IP and roughly which
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
| S18 | `siri-shortcut.png` | The finished Shortcut, or the CarPlay screen *(optional)* | Section 11c |
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
- [`docs/TESTING.md`](docs/TESTING.md) — installing a build in a real Home
  Assistant, verifying it, running the test suite, and a troubleshooting table.

Bugs and chain requests: [issues](https://github.com/laithsaid/ha-tankpriser/issues).

## Support

Tankpriser is free and open source, prediction included. If it saves you money,
a donation is genuinely appreciated: **[paypal.me/tankpriser](https://paypal.me/tankpriser)**.

The same link sits in both cards' footers, always. It is one line, it withholds
nothing, and it is the only thing the project asks in return — so there is no
card option to hide it or to point it elsewhere. Forking? Change `DONATE_URL` in
`const.py` (and the copy at the top of `www/tankpriser-card.js`).

## Disclaimer

Prices come directly from each fuel chain's public price API and may be delayed
or inaccurate; the forecourt sign wins. This project is not affiliated with any
fuel chain. Use responsibly and keep the polling interval reasonable.

Licensed under the [MIT License](LICENSE).

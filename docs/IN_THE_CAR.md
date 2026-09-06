# Tankpriser in the car — moved

This page used to hold the CarPlay, Siri and Android Auto setup. It was in two
places at once — the phone settings in the README, the shortcut steps here — and
that is a bad way to follow instructions with one hand on a steering wheel.

**It now lives in one place: the README.**

→ **[In the car: CarPlay, Siri and Android Auto](../README.md#11-in-the-car-carplay-siri-and-android-auto)**
  — how Siri and Android Auto each get the answer.

→ **[Setting up the in-car sensors](../README.md#11-setting-up-the-in-car-sensors)**
  — the whole walkthrough, in order:

| | |
| --- | --- |
| **11a** | Setting up your iPhone — the five iOS settings that decide whether any of it works |
| **11b** | The shortcut — eight actions, no questions asked |
| **11c** | When it does not work |
| **11d** | Already navigating? What that can and cannot do |
| **11e** | Variants |

The two routes are built differently. **Siri asks the `tankpriser.nearby`
service** with the phone's own position, so nothing in between can go stale.
**Android Auto needs no shortcut at all**: add the `…_cheapest_nearby` sensor to
the companion app's Android Auto favourites and it appears in the driving list,
with navigation straight to the cheapest forecourt.

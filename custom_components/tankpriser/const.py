"""Constants for the Tankpriser integration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DOMAIN: Final = "tankpriser"


def _manifest_version() -> str:
    """The installed version, read from the manifest.

    Read rather than repeated as a literal: a hand-maintained copy drifts, and
    ours did — the User-Agent claimed 0.7.1 for four minor releases, which is
    exactly the field a chain would use to identify our traffic.
    """
    try:
        manifest = Path(__file__).with_name("manifest.json")
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "0"


VERSION: Final = _manifest_version()

# --- Countries -------------------------------------------------------------
# One config entry covers one country: the sources, the fuels sold, the
# currency and the decimals a price is quoted to are all country-wide facts,
# and mixing two of them into one list produces prices that cannot be compared.
#
# ADDING A COUNTRY is meant to be two data entries and a parser: one `Country`
# below, one `Provider` in sources.py, and the function that turns that
# source's JSON into `Station`s. Everything else is derived — which fuels the
# dialog offers comes from the provider's product map, which radii it offers
# comes from the provider's own ceiling, and every price shown or spoken takes
# its unit and its decimals from the `Country` record here. Nothing else in the
# integration should learn a country's name.
# Lowercase because these double as translation keys — Home Assistant requires
# those to match [a-z0-9-_]+, and hassfest fails the build over it. They are our
# own internal keys rather than ISO-3166 codes on the wire, so the case is free
# to give away.
COUNTRY_DK: Final = "dk"
COUNTRY_DE: Final = "de"
DEFAULT_COUNTRY: Final = COUNTRY_DK


@dataclass(frozen=True)
class Country:
    """How one country quotes a fuel price."""

    code: str
    name: str
    # What a sensor is measured in, and what the card prints after a number.
    unit: str
    # The word said out loud. One per currency rather than one per language:
    # "euro" is understood in a Danish sentence, and a table of currency words
    # per spoken language is a lot of translation for no extra clarity.
    spoken_currency: str
    # Digits after the decimal separator when a price is *shown*. Germany signs
    # its forecourts to three, so 1,72 on screen would disagree with the pump;
    # Denmark uses two. Comparisons always use the full float — rounding first
    # can tie 1,715 with 1,719, and 1,699 must still trip a 1,70 rule.
    decimals: int
    # The small change a saving is quoted in. Danish fuel cards are advertised
    # in øre off the pump price, and the same instinct reads a German gap as
    # cents, so "12 øre cheaper" and "12 cents cheaper" are the natural phrase
    # in each place.
    minor_unit: str = "øre"
    # What this country calls fuels we already model, where it differs. E10 is
    # the same 95-octane petrol as "Blyfri 95", so it keeps the key and changes
    # only its name.
    labels: Mapping[str, str] = field(default_factory=dict)
    # Roughly where the country is, as (lat_min, lon_min, lat_max, lon_max).
    # It exists so a caller that knows where the phone is — the voice service,
    # a simulated drive — asks the entry for the country it is actually *in*.
    # Without it the first configured entry answered for everywhere, so asking
    # for cheap fuel outside Hamburg searched the Danish stations and said
    # there was nothing nearby.
    #
    # The boxes are generous and they overlap along a shared border, which is
    # deliberate: a box tight enough to split Flensburg from Kruså would be
    # wrong the moment a road bends. Where two match, the entry anchored
    # nearer wins, which is the answer the person who configured both wants.
    bbox: tuple[float, float, float, float] | None = None

    def contains(self, latitude: float, longitude: float) -> bool:
        """Whether a position falls inside this country's box."""
        if self.bbox is None:
            return False
        lat_min, lon_min, lat_max, lon_max = self.bbox
        return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max


COUNTRIES: Final[dict[str, Country]] = {
    country.code: country
    for country in (
        Country(
            COUNTRY_DK,
            "Denmark",
            "kr./L",
            "kroner",
            2,
            "øre",
            bbox=(54.4, 7.7, 57.9, 15.3),
        ),
        Country(
            COUNTRY_DE,
            "Germany",
            "€/L",
            "euro",
            3,
            "cent",
            {
                "blyfri95": "Super E10",
                "blyfri95plus": "Super E5",
                "diesel": "Diesel",
            },
            bbox=(47.2, 5.8, 55.1, 15.1),
        ),
    )
}


def country_of(code: str) -> Country:
    """The country record, falling back to the default rather than raising:
    a stored code we no longer recognise must not break an existing entry.

    Case-insensitive, because the codes were uppercase for one afternoon and an
    entry written in that window would otherwise silently become Danish.
    """
    return COUNTRIES.get(str(code or "").lower()) or COUNTRIES[DEFAULT_COUNTRY]


def price_unit(country: str) -> str:
    """What a price in this country is measured in."""
    return country_of(country).unit


def price_decimals(country: str) -> int:
    """How many decimals to *show* a price to in this country."""
    return country_of(country).decimals


def spoken_currency(country: str) -> str:
    """The currency word to say out loud in this country."""
    return country_of(country).spoken_currency


def minor_unit(country: str) -> str:
    """What a small price difference is counted in here (øre, cent)."""
    return country_of(country).minor_unit


def format_price(value: float, country: str, decimals: int | None = None) -> str:
    """A price written the way this country writes it.

    Every country we cover uses the decimal comma, so that is not yet a
    per-country field — the first country that does not gets one.
    """
    places = price_decimals(country) if decimals is None else decimals
    return f"{value:.{places}f}".replace(".", ",")

# --- Data sources ----------------------------------------------------------
# Since 2026-01-01 Danish law requires every fuel chain to publish an open
# per-station price API. We aggregate the free, no-auth ones directly instead
# of scraping fuelfinder.dk (whose radius endpoint is dead). Each provider is
# fetched nationwide, then filtered to the configured area by postnummer.
#
# Q8 + F24 share one endpoint; it needs BOTH page and pageSize (a bare call
# returns zero records). Shell returns a plain JSON array with coordinates.
Q8_URL: Final = "https://beta.q8.dk/Station/GetStationPrices?page=1&pageSize=2000"
SHELL_URL: Final = "https://shellpumpepriser.geoapp.me/v1/prices"
# OK: ~690 stations, all with coordinates. No auth; response cached ~2 min and
# rate-limited server-side, which our provider cache already respects.
OK_URL: Final = "https://mobility-prices.ok.dk/api/v1/fuel-prices"
# OIL!: ~70 stations, no auth, but priced one fuelType per request. We query
# only the fuel types OIL! actually sells and merge them by station_id.
OIL_URL: Final = "https://apim-fuel-prices-prod.azure-api.net/Oil-FuelPrices/prices"
OIL_FUELTYPES: Final = {"95E10": "blyfri95", "DieselB7": "diesel"}

# Germany: Tankerkoenig, the free consumer feed of the Bundeskartellamt's
# MTS-K. Needs a personal key (see the Provider entry in sources.py) and
# answers only about a circle — there is no nationwide response to filter, and
# no way to build one: the radius is capped and tiling the country would be
# exactly the abuse the cap exists to prevent.
TANKERKOENIG_URL: Final = "https://creativecommons.tankerkoenig.de/json/list.php"
TANKERKOENIG_MAX_RADIUS_KM: Final = 25

# Sent with every provider request. We identify honestly rather than
# impersonating a browser: these are open JSON APIs published under the price
# transparency law, all five endpoints were verified to answer this UA, and a
# chain with a problem can reach us through the URL instead of silently
# blocking what looks like a fake Chrome.
REQUEST_HEADERS: Final = {
    "User-Agent": (
        f"HomeAssistant-Tankpriser/{VERSION} "
        "(+https://github.com/laithsaid/ha-tankpriser)"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "da,en-US;q=0.9,en;q=0.8",
}

# Seconds to cache each provider's nationwide response, shared across all
# configured areas so many areas cost one fetch per provider.
PROVIDER_CACHE_TTL: Final = 600.0

# How long a failing provider's last good response may keep being served.
# Beyond this the chain drops out of the list entirely: stale prices are worse
# than absent ones, because nothing on screen tells the user they are old.
MAX_STALE_AGE: Final = 6 * 3600.0

# --- Geo (DAWA) ------------------------------------------------------------
# Danmarks Adressers Web API — free, no key. Resolves the configured
# postnummer + radius into the set of postnumre inside that circle, which we
# use to filter stations, and supplies town-centre coordinates for stations
# whose provider does not ship exact coordinates.
DAWA_BASE_URL: Final = "https://api.dataforsyningen.dk"

# --- Configuration keys ----------------------------------------------------
# Which country this entry covers. Set once at setup and never edited: it
# decides the sources, the fuels and the currency, so changing it would mean a
# different set of entities under the same ids.
CONF_COUNTRY: Final = "country"
# Where an area-scoped country searches from: {"latitude": .., "longitude": ..}.
# Absent means Home. It exists because the background sensors, the notification
# baseline and the history all have to compare the *same* stations from one
# refresh to the next, so that point must stand still — and because the point
# worth watching may not be where Home is (a Dane watching Flensburg).
CONF_ANCHOR: Final = "anchor"
CONF_POSTNUMMER: Final = "postnummer"
CONF_RADIUS: Final = "radius"
CONF_FUEL_TYPES: Final = "fuel_types"
CONF_AREA_NAME: Final = "area_name"
CONF_EXCLUDED_STATIONS: Final = "excluded_stations"
CONF_SCAN_INTERVAL: Final = "scan_interval"
# Per-chain loyalty discount in øre/L, e.g. {"ok": 20, "f24": 30}. Danish cards
# are advertised in øre off the pump price, so that is the unit we ask for.
CONF_DISCOUNTS: Final = "discounts"
# Nobody's card is worth more than a krone or two per litre; a bigger number is
# a typo (kroner entered as øre) and would invent a negative price.
MAX_DISCOUNT_ORE: Final = 200
# Rank stations near a device you nominate (phone or car), for the voice /
# in-car surfaces where the map card cannot reach.
CONF_NEARBY_TRACKER: Final = "nearby_tracker"
CONF_NEARBY_RADIUS_KM: Final = "nearby_radius_km"
DEFAULT_NEARBY_RADIUS_KM: Final = 15
# How many stations the nearby sensor lists. Enough to choose from out loud,
# few enough that an attribute stays small.
NEARBY_MAX_STATIONS: Final = 8
# How many of those the `spoken` sentence names. Three is what a driver can hold
# in their head long enough to answer "number two".
SPOKEN_STATIONS: Final = 3
# Per-chain API keys, stored in the config entry's *data* (not options):
# {provider_key: credential}. Redacted from diagnostics, never logged.
CONF_CREDENTIALS: Final = "credentials"
# Which chain the credential dialog is currently editing (flow-local).
CONF_PROVIDER: Final = "provider"

# Navigation links. `dir_action=navigate` starts turn-by-turn straight away;
# without it Google Maps opens a route *preview* and — if it cannot resolve
# your position itself — asks you to pick a starting point, which is a dialog
# nobody wants at 110 km/h. Shared by the `nearby` service and the fill-up
# notification so both send you to the same place the same way.
MAPS_URLS: Final = {
    "google": (
        "https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
        "&travelmode=driving&dir_action=navigate"
    ),
    "apple": "http://maps.apple.com/?daddr={lat},{lon}&dirflg=d",
    "osm": "https://www.openstreetmap.org/directions?to={lat}%2C{lon}",
}
DEFAULT_MAPS: Final = "google"

# Notification options
CONF_NOTIFY_ENABLED: Final = "notify_enabled"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_RULE: Final = "notify_rule"
CONF_NOTIFY_THRESHOLD: Final = "notify_threshold"

# --- "Fill up now" notification -------------------------------------------
# The one alert that needs a car, a position and a price list at once: see
# fillup.py. Off by default — it interrupts, so it should be asked for.
CONF_FILLUP_ENABLED: Final = "fillup_enabled"
CONF_FILLUP_LEVEL: Final = "fillup_level_pct"
CONF_FILLUP_NEAR_KM: Final = "fillup_near_km"
CONF_FILLUP_MAPS: Final = "fillup_maps"

# --- Prediction accuracy (advanced) ---------------------------------------
# Grades the consumption prediction against what the cars actually did — see
# accuracy.py. Off by default, and its service is not even registered until it
# is switched on: it is a tool for judging the model, not a feature of it, and
# an accuracy figure shown to someone who did not ask for one reads as an
# apology rather than as information.
CONF_ACCURACY_ENABLED: Final = "accuracy_enabled"
# Whether to feed that grading back into the prediction as a correction. On by
# default: a model that has been leaning the same way every tank is measurably
# wrong, and the correction is damped and capped so it cannot run away. Unlike
# the grading above this is not advanced — it changes the number people read,
# so it is an ordinary setting with an ordinary explanation.
CONF_CALIBRATION_ENABLED: Final = "calibration_enabled"

# --- Radius ----------------------------------------------------------------
# We do our own geographic filtering, so any radius works where the source
# publishes the whole country. Where it does not, the source imposes a ceiling
# and `sources.radius_options` trims this ladder to it — see there, which is
# also where the default comes from.
RADIUS_OPTIONS: Final = ["5 km", "10 km", "15 km", "25 km", "50 km"]
DEFAULT_RADIUS: Final = "10 km"


def radius_to_metres(radius: str | int | float) -> int:
    """Parse a radius label ('10 km') or number into metres (default 10 km)."""
    if isinstance(radius, (int, float)):
        return int(radius * 1000)
    digits = "".join(c for c in str(radius) if c.isdigit())
    return int(digits) * 1000 if digits else 10_000


# --- Polling ---------------------------------------------------------------
# Chains refresh prices roughly daily; a gentle default keeps load tiny.
DEFAULT_SCAN_INTERVAL_MIN: Final = 30
MIN_SCAN_INTERVAL_MIN: Final = 15

# --- Change-detection baseline ---------------------------------------------
# Notifications compare each refresh with the one before it. Held only in
# memory, that baseline died with every restart: the first refresh afterwards
# became the new baseline, and whatever the price did across the gap was never
# announced. Since the chains move prices about once a day, a daily restart
# meant a notification could never arrive at all.
PRICE_STORAGE_VERSION: Final = 1
PRICE_STORAGE_KEY_PREFIX: Final = "tankpriser_prices"
# Written a few seconds after a refresh rather than during it; Home Assistant
# flushes pending saves on shutdown, so a clean restart loses nothing.
BASELINE_SAVE_DELAY: Final = 10.0
# Past this, the last prices we saw are history rather than news, and comparing
# against them would announce a "drop" that happened while HA was switched off
# weeks ago. A week covers every realistic outage.
MAX_BASELINE_AGE: Final = 7 * 24 * 3600.0

# --- Notification rules ----------------------------------------------------
RULE_ANY: Final = "any_change"
RULE_CHEAPEST: Final = "cheapest_change"
RULE_THRESHOLD: Final = "below_threshold"
RULE_DECREASE: Final = "decrease_only"
NOTIFY_RULES: Final = [RULE_ANY, RULE_CHEAPEST, RULE_THRESHOLD, RULE_DECREASE]
DEFAULT_NOTIFY_RULE: Final = RULE_CHEAPEST

# --- Fuel types ------------------------------------------------------------
# Normalized internal key -> default display name. Providers use their own
# product names; sources.py maps each provider product onto one of these keys,
# and a country may rename one (see `Country.labels`). The unit is *not* here:
# it belongs to the country, not to the fuel. Only motor fuels are modelled
# (AdBlue and EV charging are intentionally skipped). blyfri95 and diesel are
# the common denominators present at nearly every station, so they are the
# sensible defaults everywhere.
FUEL_TYPES: Final = {
    "blyfri95": "Blyfri 95 (E10)",
    "blyfri98": "Blyfri 98",
    "blyfri95plus": "Blyfri 95 Extra (E5)",
    "oktan100": "Oktan 100",
    "diesel": "Diesel (B7)",
    "dieselplus": "Diesel Extra",
    "hvo100": "HVO100",
}
DEFAULT_FUEL_TYPES: Final = ["blyfri95", "diesel"]


def fuel_label(key: str, country: str = DEFAULT_COUNTRY) -> str:
    """What to call a fuel in a given country."""
    return country_of(country).labels.get(key) or FUEL_TYPES.get(key, key)

# --- Consumption prediction (per-car subentries) ---------------------------
# Each car is a config *subentry* under the single Tankpriser entry, so a user
# can add as many cars as they like — the only requirement is that HA already
# has an entity exposing the car's fuel level. The prediction is FREE; we only
# ask for a donation (see DONATE_URL).
SUBENTRY_CAR: Final = "car"

CONF_SOURCE_ENTITY: Final = "source_entity"
CONF_LEVEL_ATTRIBUTE: Final = "level_attribute"
CONF_LEVEL_UNIT: Final = "level_unit"
CONF_TANK_CAPACITY: Final = "tank_capacity_l"
CONF_ODOMETER_ENTITY: Final = "odometer_entity"
CONF_ODOMETER_ATTRIBUTE: Final = "odometer_attribute"
CONF_CAR_FUEL: Final = "fuel_key"

# How the source entity expresses the level.
LEVEL_UNIT_PERCENT: Final = "percent"
LEVEL_UNIT_LITRES: Final = "litres"
LEVEL_UNITS: Final = [LEVEL_UNIT_PERCENT, LEVEL_UNIT_LITRES]

# Refuel detection: an upward jump of at least this fraction of the tank marks
# the end of one consumption segment and the start of the next.
REFUEL_MIN_JUMP_FRACTION: Final = 0.15
# At this many completed segments the prediction is called "ready". Below it we
# still answer — from the tank currently in progress — but say so (status
# "estimating") and cap the confidence.
MIN_SEGMENTS_FOR_PREDICTION: Final = 2
# The tank in progress counts as an observation once it has BOTH run this long
# and burnt this much of the tank. Without the second condition a car parked for
# three days would report a rate of ~0 L/day, i.e. "empty in nine years"; with
# only the second, a single big trip would be projected as a daily habit.
# Three days rather than one: for someone who drives in bursts, a one-day window
# is a single trip, not a habit.
EARLY_MIN_DAYS: Final = 3.0
EARLY_MIN_CONSUMED_FRACTION: Final = 0.05
# One partial tank is a guess, not a measurement — never claim more than this.
EARLY_CONFIDENCE_CAP: Final = 0.3
# When completed tanks exist, the tank in progress is blended in with a weight of
# (its days) / (a typical tank's days), capped at 1. Irregular driving is the
# reason: without this a single 20 L Saturday would carry the same weight as a
# whole tank and swing a 12-day prediction to 3, then back again once the car sat
# still for a week. A short window now nudges; it earns its say as it lengthens.
# The fallback is only used if completed tanks somehow have no duration.
TYPICAL_TANK_DAYS_FALLBACK: Final = 10.0
# Exponential weighting of recent tanks vs older ones (0<alpha<=1; higher =
# more weight on the most recent segment). Used by the estimator.
EWMA_ALPHA: Final = 0.5
# Number of learned tanks at which confidence reaches its count-based maximum.
CONFIDENCE_TARGET_SEGMENTS: Final = 6
# A segment shorter than this (days) is ignored when computing a daily rate —
# guards against divide-by-tiny-duration blow-ups from bursty sensor updates.
MIN_SEGMENT_DAYS: Final = 0.05

# --- Burning it right now --------------------------------------------------
# While the car is actually running, the tank is a measurement rather than a
# guess: the level is visibly dropping, so "at this rate it is empty at 18:40"
# is arithmetic on what is happening, needing no history and no correction.
# These four constants decide when there is enough of a drop to trust.
#
# How far back to measure the current burn. Long enough to average out a slosh
# and a coarse sensor, short enough to still describe *this* drive.
LIVE_WINDOW_S: Final = 2 * 3600.0
# If nothing has been reported for longer than this the car is not running —
# readings are only stored when the level actually moves, so silence is the
# signal. Twenty minutes covers a fuel gauge that only reports in coarse steps.
LIVE_MAX_GAP_S: Final = 1200.0
# The window has to span at least this long, or a rate divides by almost zero.
LIVE_MIN_SPAN_S: Final = 600.0
# ...and show at least this much of the tank gone. Fuel senders are coarse and
# a parked car on a slope can wander a little; below this it is noise, not a
# journey.
LIVE_MIN_DROP_FRACTION: Final = 0.015

# .storage bounds, so the learned history cannot grow without limit.
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = "tankpriser_consumption"
MAX_RAW_SAMPLES: Final = 500
MAX_SEGMENTS: Final = 50

# Donation is by free will: the prediction works for everyone, we simply ask.
# PayPal.Me rather than Ko-fi: the tip platforms price in USD/EUR only, and this
# is a Danish audience paying for Danish fuel. The card carries its own copy of
# this URL (www/tankpriser-card.js) — change both together.
DONATE_URL: Final = "https://paypal.me/tankpriser"

# Chain identification, for discounts. A station only tells us a `company`
# string ("Q8 Service", "F24", "OK Plus"…), so each chain is matched by pattern.
# ORDER MATTERS: "ok" is two letters and appears inside other words, so it is
# tested last. The card carries the same table for its icons — keep them in step.
CHAINS: Final = [
    ("oil", "OIL!", r"oil"),
    ("f24", "F24", r"f24"),
    ("q8", "Q8", r"q8"),
    ("shell", "Shell", r"shell"),
    ("circlek", "Circle K / INGO", r"circle ?k|ingo"),
    ("goon", "Go'on", r"go.?on"),
    ("unox", "Uno-X", r"uno.?x"),
    ("ok", "OK", r"ok|^ok"),
]


# Static path under which the bundled Lovelace card is served. The whole www/
# directory is exposed, because the card also loads its vendored Leaflet build
# from www/vendor/ — the browser must never need a public CDN for the map.
CARD_BASE_URL: Final = "/tankpriser"
CARD_URL: Final = f"{CARD_BASE_URL}/tankpriser-card.js"

# Event fired after every successful refresh (for user automations).
EVENT_PRICE_UPDATED: Final = "tankpriser_price_updated"

# --- Drive simulation ------------------------------------------------------
# See simulate.py. A test fixture, not a feature of the integration: it moves a
# virtual tracker so the driving logic can be exercised without driving.
EVENT_SIMULATION_STEP: Final = "tankpriser_simulation_step"
SIMULATION_DATA_KEY: Final = "tankpriser_simulation"
SIMULATION_ENTITY: Final = "device_tracker.tankpriser_sim"
# Real seconds between steps. The floor is not politeness: with `announce` on,
# every step asks the source for a handful of circles, and a source that is
# queried by area is rate-limited per key — yours, not ours.
DEFAULT_SIMULATION_INTERVAL_S: Final = 60.0
MIN_SIMULATION_INTERVAL_S: Final = 15.0
DEFAULT_SIMULATION_SPEED_KMH: Final = 110.0

# Routes worth driving, as waypoints. Straight lines between them, so add one
# wherever a road genuinely bends. Named so a test is one service call rather
# than a page of coordinates; extend per country as countries are added.
SIMULATION_ROUTES: Final[dict[str, list[tuple[float, float]]]] = {
    # The full length of Germany on the A7/A5: Danish border to Munich, ~830 km
    # through every price region the country has.
    "de_north_south": [
        (54.7820, 9.4360),   # Flensburg, at the border
        (53.5511, 9.9937),   # Hamburg
        (52.3759, 9.7320),   # Hannover
        (51.3127, 9.4797),   # Kassel
        (50.1109, 8.6821),   # Frankfurt
        (49.4521, 11.0767),  # Nuremberg
        (48.1351, 11.5820),  # Munich
    ],
    # Across the industrial west into Saxony, ~640 km.
    "de_west_east": [
        (50.7753, 6.0839),   # Aachen
        (50.9375, 6.9603),   # Cologne
        (51.5136, 7.4653),   # Dortmund
        (51.3127, 9.4797),   # Kassel
        (51.3397, 12.3731),  # Leipzig
        (51.0504, 13.7373),  # Dresden
    ],
    # The one a Dane actually drives: down Jutland and over the border to the
    # cheap forecourts around Flensburg, ~200 km.
    "dk_de_border": [
        (55.4038, 10.4024),  # Odense
        (55.4904, 9.4722),   # Kolding
        (55.0714, 9.4394),   # Sønderborg road, south Jutland
        (54.7820, 9.4360),   # Flensburg
        (54.5000, 9.5500),   # Schleswig
    ],
    # Denmark end to end, for the national side, ~470 km.
    "dk_north_south": [
        (57.7210, 10.5830),  # Skagen
        (57.0488, 9.9217),   # Aalborg
        (56.1629, 10.2039),  # Aarhus
        (55.4904, 9.4722),   # Kolding
        (55.4038, 10.4024),  # Odense
        (55.6761, 12.5683),  # Copenhagen
    ],
}

"""Constants for the Tankpriser integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "tankpriser"

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

# Sent with every provider request. We identify honestly rather than
# impersonating a browser: these are open JSON APIs published under the price
# transparency law, all five endpoints were verified to answer this UA, and a
# chain with a problem can reach us through the URL instead of silently
# blocking what looks like a fake Chrome.
REQUEST_HEADERS: Final = {
    "User-Agent": (
        "HomeAssistant-Tankpriser/0.7.1 "
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
CONF_POSTNUMMER: Final = "postnummer"
CONF_RADIUS: Final = "radius"
CONF_FUEL_TYPES: Final = "fuel_types"
CONF_AREA_NAME: Final = "area_name"
CONF_EXCLUDED_STATIONS: Final = "excluded_stations"
CONF_SCAN_INTERVAL: Final = "scan_interval"
# Per-chain API keys, stored in the config entry's *data* (not options):
# {provider_key: credential}. Redacted from diagnostics, never logged.
CONF_CREDENTIALS: Final = "credentials"
# Which chain the credential dialog is currently editing (flow-local).
CONF_PROVIDER: Final = "provider"

# Notification options
CONF_NOTIFY_ENABLED: Final = "notify_enabled"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_RULE: Final = "notify_rule"
CONF_NOTIFY_THRESHOLD: Final = "notify_threshold"

# --- Radius ----------------------------------------------------------------
# We now do our own geographic filtering, so any radius works. We keep the
# familiar "N km" labels; RADIUS_KM parses the number out for DAWA.
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

# --- Notification rules ----------------------------------------------------
RULE_ANY: Final = "any_change"
RULE_CHEAPEST: Final = "cheapest_change"
RULE_THRESHOLD: Final = "below_threshold"
RULE_DECREASE: Final = "decrease_only"
NOTIFY_RULES: Final = [RULE_ANY, RULE_CHEAPEST, RULE_THRESHOLD, RULE_DECREASE]
DEFAULT_NOTIFY_RULE: Final = RULE_CHEAPEST

# --- Fuel types ------------------------------------------------------------
# Normalized internal key -> (display name, unit). Providers use their own
# product names; sources.py maps each provider product onto one of these keys.
# Only motor fuels are modelled (AdBlue and EV charging are intentionally
# skipped). blyfri95 and diesel are the common denominators present at nearly
# every station and are the sensible defaults.
FUEL_TYPES: Final = {
    "blyfri95": ("Blyfri 95 (E10)", "kr./L"),
    "blyfri98": ("Blyfri 98", "kr./L"),
    "blyfri95plus": ("Blyfri 95 Extra (E5)", "kr./L"),
    "oktan100": ("Oktan 100", "kr./L"),
    "diesel": ("Diesel (B7)", "kr./L"),
    "dieselplus": ("Diesel Extra", "kr./L"),
    "hvo100": ("HVO100", "kr./L"),
}
DEFAULT_FUEL_TYPES: Final = ["blyfri95", "diesel"]

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
EARLY_MIN_DAYS: Final = 1.0
EARLY_MIN_CONSUMED_FRACTION: Final = 0.05
# One partial tank is a guess, not a measurement — never claim more than this.
EARLY_CONFIDENCE_CAP: Final = 0.3
# Exponential weighting of recent tanks vs older ones (0<alpha<=1; higher =
# more weight on the most recent segment). Used by the estimator.
EWMA_ALPHA: Final = 0.5
# Number of learned tanks at which confidence reaches its count-based maximum.
CONFIDENCE_TARGET_SEGMENTS: Final = 6
# A segment shorter than this (days) is ignored when computing a daily rate —
# guards against divide-by-tiny-duration blow-ups from bursty sensor updates.
MIN_SEGMENT_DAYS: Final = 0.05

# .storage bounds, so the learned history cannot grow without limit.
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = "tankpriser_consumption"
MAX_RAW_SAMPLES: Final = 500
MAX_SEGMENTS: Final = 50

# Donation is by free will: the prediction works for everyone, we simply ask.
# Placeholder until the real Ko-fi / MobilePay handle is wired in (issue 4).
DONATE_URL: Final = "https://github.com/laithsaid/ha-tankpriser"

# Static path under which the bundled Lovelace card is served. The whole www/
# directory is exposed, because the card also loads its vendored Leaflet build
# from www/vendor/ — the browser must never need a public CDN for the map.
CARD_BASE_URL: Final = "/tankpriser"
CARD_URL: Final = f"{CARD_BASE_URL}/tankpriser-card.js"

# Event fired after every successful refresh (for user automations).
EVENT_PRICE_UPDATED: Final = "tankpriser_price_updated"

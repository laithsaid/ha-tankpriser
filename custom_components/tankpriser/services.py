"""Tankpriser services.

* ``nearby`` — the cheapest stations around a point *you supply*, with a
  ready-to-speak sentence and navigation links. Returns response data, so a
  caller (a Siri Shortcut over the REST API, an automation, a voice assistant)
  gets a complete answer from one call, with no entity and no device tracker in
  between. That is the point: a phone that hands over its own position cannot
  be told about the town it left an hour ago.

Two more for the per-car prediction:

* ``seed_demo_history`` — inject synthetic tanks so the prediction shows a
  number right away, instead of waiting days for real refuel cycles.
* ``reset_history`` — clear a car's learned history (e.g. after changing the
  tank size, or to undo a demo seed).

Both of those act on all configured cars, optionally filtered by name.
"""

from __future__ import annotations

from collections.abc import Iterator

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_FUEL_TYPES,
    DEFAULT_NEARBY_RADIUS_KM,
    DOMAIN,
    FUEL_TYPES,
    NEARBY_MAX_STATIONS,
    SPOKEN_STATIONS,
)
from .coordinator import async_national_stations, credentials_of, discounts_of
from .nearby import rank_nearby, spoken_cheapest, spoken_sentence

SERVICE_NEARBY = "nearby"
SERVICE_SEED_DEMO = "seed_demo_history"
SERVICE_RESET = "reset_history"

ATTR_CAR = "car"
ATTR_TANKS = "tanks"
ATTR_LITRES_PER_DAY = "litres_per_day"
ATTR_DAYS_PER_TANK = "days_per_tank"

ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_FUEL = "fuel"
ATTR_RADIUS_KM = "radius_km"
ATTR_MAPS = "maps"

# Navigation links are built here rather than left to the caller: a Shortcut can
# read a string out of a response, but assembling one per station from a nested
# list is a page of actions on a phone.
_MAPS_URL = {
    "google": "https://www.google.com/maps/dir/?api=1&destination={lat},{lon}",
    "apple": "http://maps.apple.com/?daddr={lat},{lon}&dirflg=d",
    "osm": "https://www.openstreetmap.org/directions?to={lat}%2C{lon}",
}

_NEARBY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LATITUDE): cv.latitude,
        vol.Required(ATTR_LONGITUDE): cv.longitude,
        vol.Optional(ATTR_FUEL): vol.In(list(FUEL_TYPES)),
        vol.Optional(ATTR_RADIUS_KM, default=float(DEFAULT_NEARBY_RADIUS_KM)): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_MAPS, default="google"): vol.In(list(_MAPS_URL)),
    }
)

_SEED_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CAR): cv.string,
        vol.Optional(ATTR_TANKS, default=3): vol.All(
            vol.Coerce(int), vol.Range(min=2, max=10)
        ),
        vol.Optional(ATTR_LITRES_PER_DAY, default=5.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=50.0)
        ),
        vol.Optional(ATTR_DAYS_PER_TANK, default=7.0): vol.All(
            vol.Coerce(float), vol.Range(min=1.0, max=60.0)
        ),
    }
)
_RESET_SCHEMA = vol.Schema({vol.Optional(ATTR_CAR): cv.string})


def _default_fuel(hass: HomeAssistant) -> str | None:
    """The first fuel any entry is configured for.

    So a caller that only cares about petrol need not know the internal key.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        fuels = entry.options.get(CONF_FUEL_TYPES, entry.data.get(CONF_FUEL_TYPES, []))
        for key in fuels:
            if key in FUEL_TYPES:
                return key
    return None


def _cars(hass: HomeAssistant, name: str | None) -> Iterator:
    """Yield the car trackers across all entries, optionally filtered by name."""
    for value in hass.data.get(DOMAIN, {}).values():
        for tracker in getattr(value, "cars", {}).values():
            if not name or tracker.name == name:
                yield tracker


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Tankpriser services once."""
    if hass.services.has_service(DOMAIN, SERVICE_NEARBY):
        return

    async def _nearby(call: ServiceCall) -> ServiceResponse:
        fuel = call.data.get(ATTR_FUEL) or _default_fuel(hass)
        if fuel is None:
            raise HomeAssistantError(
                "No fuel given, and no Tankpriser area is configured to take a "
                "default from."
            )
        latitude = call.data[ATTR_LATITUDE]
        longitude = call.data[ATTR_LONGITUDE]

        stations = await async_national_stations(
            hass, credentials_of(hass), discounts_of(hass)
        )
        ranked = rank_nearby(
            stations,
            latitude,
            longitude,
            call.data[ATTR_RADIUS_KM] * 1000.0,
            fuel,
        )
        language = str(getattr(hass.config, "language", "") or "")
        danish = language.lower().startswith("da")
        listed = ranked[:NEARBY_MAX_STATIONS]
        template = _MAPS_URL[call.data[ATTR_MAPS]]
        return {
            "fuel": fuel,
            "fuel_type": FUEL_TYPES[fuel][0],
            "unit": FUEL_TYPES[fuel][1],
            # In range, not listed below: a count that silently equalled the cap
            # reads as "there are only 8 stations near you", which is never true.
            "count": len(ranked),
            # One station, said plainly — what the documented shortcut speaks.
            "spoken_cheapest": spoken_cheapest(ranked, danish=danish),
            "spoken": spoken_sentence(ranked, danish=danish),
            "spoken_count": min(len(ranked), SPOKEN_STATIONS),
            "stations": listed,
            # Index-aligned with `stations`, so "the third one she named" is
            # urls[3] in a Shortcut. An estimated position gets an empty string
            # rather than being skipped: dropping it would silently shift every
            # station after it up one, and navigate you to the wrong forecourt.
            "urls": [
                ""
                if s["coord_approx"]
                else template.format(lat=s["latitude"], lon=s["longitude"])
                for s in listed
            ],
        }

    async def _seed(call: ServiceCall) -> None:
        for tracker in _cars(hass, call.data.get(ATTR_CAR)):
            await tracker.seed_demo(
                call.data[ATTR_TANKS],
                call.data[ATTR_LITRES_PER_DAY],
                call.data[ATTR_DAYS_PER_TANK],
            )

    async def _reset(call: ServiceCall) -> None:
        for tracker in _cars(hass, call.data.get(ATTR_CAR)):
            await tracker.reset()

    hass.services.async_register(
        DOMAIN,
        SERVICE_NEARBY,
        _nearby,
        schema=_NEARBY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_SEED_DEMO, _seed, schema=_SEED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET, _reset, schema=_RESET_SCHEMA)

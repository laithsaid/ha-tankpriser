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

* ``test_notification`` — rehearse a price drop so the notification rule and its
  delivery can be checked without waiting for the chains to move. A reload
  cannot stand in for this: it clears the comparison baseline, so the first
  refresh after one is deliberately silent.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_FUEL_TYPES,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_RULE,
    CONF_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_RULE,
    DEFAULT_NEARBY_RADIUS_KM,
    DOMAIN,
    FUEL_TYPES,
    NEARBY_MAX_STATIONS,
    SPOKEN_STATIONS,
)
from .coordinator import (
    TankpriserData,
    async_national_stations,
    credentials_of,
    discounts_of,
)
from .nearby import rank_nearby, spoken_cheapest, spoken_sentence
from .notifications import evaluate_and_notify

SERVICE_NEARBY = "nearby"
SERVICE_SEED_DEMO = "seed_demo_history"
SERVICE_RESET = "reset_history"
SERVICE_TEST_NOTIFICATION = "test_notification"

ATTR_DROP_ORE = "drop_ore"

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
# `dir_action=navigate` starts turn-by-turn straight away; without it Google
# Maps opens a route *preview* and — if it cannot resolve your position itself —
# asks you to pick a starting point, which is a dialog nobody wants at 110 km/h.
_MAPS_URL = {
    "google": (
        "https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
        "&travelmode=driving&dir_action=navigate"
    ),
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
_TEST_NOTIFICATION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DROP_ORE, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=500)
        ),
    }
)


def _prices_before_a_drop(data: TankpriserData, ore: int) -> TankpriserData:
    """The same stations as they would have been `ore` øre/L dearer.

    Used as the "previous" half of a rehearsed comparison. Copies rather than
    mutates: `data` is the coordinator's live snapshot, and raising the prices
    the card is reading from would be a lie with a 30-minute half-life.
    """
    krone = ore / 100.0
    return TankpriserData(
        stations=[
            replace(
                station,
                prices={
                    fuel: round(price + krone, 2)
                    for fuel, price in station.prices.items()
                },
            )
            for station in data.stations
        ]
    )


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

    async def _test_notification(call: ServiceCall) -> None:
        ore = call.data[ATTR_DROP_ORE]
        areas = 0
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if coordinator is None or coordinator.data is None:
                continue
            areas += 1
            options = entry.options
            if not options.get(CONF_NOTIFY_ENABLED):
                raise ServiceValidationError(
                    f'Notifications are switched off for "{entry.title}". Turn '
                    "them on under the integration's Configure -> Notifications."
                )
            if not str(options.get(CONF_NOTIFY_SERVICE) or ""):
                raise ServiceValidationError(
                    f'No notify service is set for "{entry.title}", so there is '
                    "nowhere to send one. Pick one under Configure -> "
                    "Notifications."
                )
            sent = await evaluate_and_notify(
                hass,
                entry,
                _prices_before_a_drop(coordinator.data, ore),
                coordinator.data,
                test=True,
            )
            if not sent:
                rule = options.get(CONF_NOTIFY_RULE, DEFAULT_NOTIFY_RULE)
                raise ServiceValidationError(
                    f'Nothing was sent for "{entry.title}": a {ore} øre drop '
                    f'does not satisfy the "{rule}" rule with the current '
                    "prices. Check the rule, and the threshold if that rule "
                    "uses one — the log says which test failed."
                )
        if not areas:
            raise HomeAssistantError(
                "No Tankpriser area has prices yet, so there is nothing to "
                "compare against. Wait for the first refresh and try again."
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_NEARBY,
        _nearby,
        schema=_NEARBY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_SEED_DEMO, _seed, schema=_SEED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET, _reset, schema=_RESET_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_TEST_NOTIFICATION,
        _test_notification,
        schema=_TEST_NOTIFICATION_SCHEMA,
    )

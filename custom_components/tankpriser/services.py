"""Tankpriser services.

Two small maintenance/testing services for the per-car prediction:

* ``seed_demo_history`` — inject synthetic tanks so the prediction shows a
  number right away, instead of waiting days for real refuel cycles.
* ``reset_history`` — clear a car's learned history (e.g. after changing the
  tank size, or to undo a demo seed).

Both act on all configured cars, optionally filtered by name.
"""

from __future__ import annotations

from collections.abc import Iterator

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

SERVICE_SEED_DEMO = "seed_demo_history"
SERVICE_RESET = "reset_history"

ATTR_CAR = "car"
ATTR_TANKS = "tanks"
ATTR_LITRES_PER_DAY = "litres_per_day"
ATTR_DAYS_PER_TANK = "days_per_tank"

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


def _cars(hass: HomeAssistant, name: str | None) -> Iterator:
    """Yield the car trackers across all entries, optionally filtered by name."""
    for value in hass.data.get(DOMAIN, {}).values():
        for tracker in getattr(value, "cars", {}).values():
            if not name or tracker.name == name:
                yield tracker


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Tankpriser services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SEED_DEMO):
        return

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

    hass.services.async_register(DOMAIN, SERVICE_SEED_DEMO, _seed, schema=_SEED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET, _reset, schema=_RESET_SCHEMA)

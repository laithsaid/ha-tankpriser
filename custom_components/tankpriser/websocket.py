"""WebSocket API for Tankpriser.

Serves the full nationwide station list to the Lovelace card's "national" map
mode. This deliberately does NOT go through a sensor attribute: ~1200 stations
would bloat the state machine, so the card asks for them on demand instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .coordinator import async_national_stations, credentials_of, discounts_of

WS_TYPE_STATIONS = "tankpriser/stations"

# The assembled payload is memoised briefly. The provider fetches underneath are
# already cached, but building and serialising ~1200 stations is not free and
# this command is open to every logged-in user, not just admins — without this,
# repeat calls do that work on the event loop as fast as they arrive. Short
# enough that a discount or credential change shows up almost immediately, and
# the key covers both anyway.
_PAYLOAD_TTL = 60.0
_payload_cache: tuple[float, str, list[dict]] | None = None
_payload_lock = asyncio.Lock()


def _cache_key(credentials: dict[str, str], discounts: dict[str, int]) -> str:
    """Fingerprint the inputs that change the payload, without holding a
    second copy of any credential."""
    parts = [
        f"{key}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"
        for key, value in sorted(credentials.items())
    ]
    parts += [f"{key}={value}" for key, value in sorted(discounts.items())]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_STATIONS})
@websocket_api.async_response
async def ws_stations(hass: HomeAssistant, connection, msg) -> None:
    """Return every Danish station with coordinates and current prices."""
    global _payload_cache  # noqa: PLW0603
    credentials = credentials_of(hass)
    discounts = discounts_of(hass)
    key = _cache_key(credentials, discounts)

    async with _payload_lock:
        cached = _payload_cache
        if (
            cached is not None
            and cached[1] == key
            and (time.monotonic() - cached[0]) < _PAYLOAD_TTL
        ):
            connection.send_result(msg["id"], {"stations": cached[2]})
            return

        result = await _build_payload(hass, credentials, discounts)
        _payload_cache = (time.monotonic(), key, result)

    connection.send_result(msg["id"], {"stations": result})


async def _build_payload(
    hass: HomeAssistant, credentials: dict[str, str], discounts: dict[str, int]
) -> list[dict]:
    """Flatten every placed station for the national map.

    The fetching, discounting and positioning is `async_national_stations`,
    shared with the `nearby` service so the map and the voice answer cannot
    disagree about a price or a position.
    """
    stations = await async_national_stations(hass, credentials, discounts)
    return [
        {
            "name": s.name,
            "company": s.company,
            "postnummer": s.postnummer,
            "city": s.city,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "coord_approx": s.coord_approx,
            "updated": s.updated,
            "prices": s.prices,
            "list_prices": s.list_prices,
            "discount_ore": s.discount_ore,
        }
        for s in stations
        if s.latitude is not None  # unplaceable: nothing to draw
    ]


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the Tankpriser websocket commands (called once)."""
    websocket_api.async_register_command(hass, ws_stations)

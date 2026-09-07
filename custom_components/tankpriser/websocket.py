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

from .const import price_decimals, price_unit
from .coordinator import (
    async_station_pool,
    credentials_of,
    discounts_of,
    exclusions_of,
    pool_target,
)

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


def _cache_key(
    credentials: dict[str, str],
    discounts: dict[str, int],
    hidden: set[str],
    target: tuple[str, object],
) -> str:
    """Fingerprint the inputs that change the payload, without holding a
    second copy of any credential.

    The target is part of it because for an area-scoped country the payload is
    one circle, and moving the anchor changes which stations exist at all.
    """
    parts = [
        f"{key}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"
        for key, value in sorted(credentials.items())
    ]
    parts += [f"{key}={value}" for key, value in sorted(discounts.items())]
    parts += sorted(hidden)
    parts.append(f"{target[0]}@{target[1]}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_STATIONS})
@websocket_api.async_response
async def ws_stations(hass: HomeAssistant, connection, msg) -> None:
    """Return every placed station in the configured country, with its prices.

    With entries for two countries the map answers for the first of them; a
    map that spans a border needs a pool that spans one, and for an
    area-scoped source no such pool exists.
    """
    global _payload_cache  # noqa: PLW0603
    credentials = credentials_of(hass)
    discounts = discounts_of(hass)
    country, area = pool_target(hass)
    key = _cache_key(credentials, discounts, exclusions_of(hass), (country, area))

    async with _payload_lock:
        cached = _payload_cache
        if (
            cached is not None
            and cached[1] == key
            and (time.monotonic() - cached[0]) < _PAYLOAD_TTL
        ):
            connection.send_result(msg["id"], _envelope(country, cached[2]))
            return

        result = await _build_payload(hass, credentials, discounts, country, area)
        _payload_cache = (time.monotonic(), key, result)

    connection.send_result(msg["id"], _envelope(country, result))


def _envelope(country: str, stations: list[dict]) -> dict:
    """The stations plus how this country writes a price.

    The card draws prices itself, into map pins and cluster labels, so it
    cannot take the unit and the decimals from an entity the way the table
    does — the national map has no entity behind it.
    """
    return {
        "stations": stations,
        "country": country,
        "unit": price_unit(country),
        "decimals": price_decimals(country),
    }


async def _build_payload(
    hass: HomeAssistant,
    credentials: dict[str, str],
    discounts: dict[str, int],
    country: str,
    area: object = None,
) -> list[dict]:
    """Flatten every placed station for the map.

    The fetching, discounting and positioning is `async_station_pool`, shared
    with the `nearby` service so the map and the voice answer cannot disagree
    about a price or a position. For Denmark that is the whole country; for a
    country whose source only answers about a circle it is the entry's own
    anchored circle, which is as much of a map as such a source can give.
    """
    stations = await async_station_pool(hass, credentials, discounts, country, area)
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

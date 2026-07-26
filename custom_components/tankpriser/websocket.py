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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import geo, geocode
from .const import CONF_CREDENTIALS, CONF_DISCOUNTS, DOMAIN
from .sources import apply_discounts, fetch_all

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


def _credentials(hass: HomeAssistant) -> dict[str, str]:
    """Collect per-chain API keys from the configured entries.

    The websocket command is integration-wide (not tied to one entry), so it
    uses whatever keys any entry has; today there is only ever one entry.
    """
    creds: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        creds.update(entry.data.get(CONF_CREDENTIALS, {}) or {})
    return creds


def _discounts(hass: HomeAssistant) -> dict[str, int]:
    """Merge the per-chain discounts from the configured entries."""
    discounts: dict[str, int] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        discounts.update(entry.options.get(CONF_DISCOUNTS, {}) or {})
    return discounts


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_STATIONS})
@websocket_api.async_response
async def ws_stations(hass: HomeAssistant, connection, msg) -> None:
    """Return every Danish station with coordinates and current prices."""
    global _payload_cache  # noqa: PLW0603
    credentials = _credentials(hass)
    discounts = _discounts(hass)
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
    """Fetch, place and flatten every station for the national map."""
    session = async_get_clientsession(hass)
    stations = await fetch_all(session, credentials)
    # The national map is a different view of the same prices, so it must carry
    # the same discounts the area sensors do.
    stations = apply_discounts(stations, discounts)

    # Stations without provider coordinates (Q8/F24): use the geocoded street
    # address where we have one, and kick off the lookups for any we do not.
    # Same cache the coordinator fills, so this normally costs nothing.
    geocoder = geocode.async_get(hass)
    await geocoder.async_load()
    unresolved = geocoder.apply([s for s in stations if s.latitude is None])
    if unresolved:
        geocoder.async_schedule(session, unresolved)

    # Whatever is still unplaced falls back to its postnummer centre.
    missing = {s.postnummer for s in stations if s.latitude is None}
    centers = await geo.centers_for(session, missing) if missing else {}

    result = []
    for s in stations:
        lat, lon, approx = s.latitude, s.longitude, s.coord_approx
        if lat is None:
            center = centers.get(s.postnummer)
            if center is None:
                continue  # cannot place it on the map
            lat, lon, approx = center[0], center[1], True
        result.append(
            {
                "name": s.name,
                "company": s.company,
                "postnummer": s.postnummer,
                "city": s.city,
                "latitude": lat,
                "longitude": lon,
                "coord_approx": approx,
                "updated": s.updated,
                "prices": s.prices,
                "list_prices": s.list_prices,
                "discount_ore": s.discount_ore,
            }
        )

    return result


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the Tankpriser websocket commands (called once)."""
    websocket_api.async_register_command(hass, ws_stations)

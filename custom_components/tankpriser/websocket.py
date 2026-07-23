"""WebSocket API for Tankpriser.

Serves the full nationwide station list to the Lovelace card's "national" map
mode. This deliberately does NOT go through a sensor attribute: ~1200 stations
would bloat the state machine, so the card asks for them on demand instead.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import geo
from .const import CONF_CREDENTIALS, DOMAIN
from .sources import fetch_all

WS_TYPE_STATIONS = "tankpriser/stations"


def _credentials(hass: HomeAssistant) -> dict[str, str]:
    """Collect per-chain API keys from the configured entries.

    The websocket command is integration-wide (not tied to one entry), so it
    uses whatever keys any entry has; today there is only ever one entry.
    """
    creds: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        creds.update(entry.data.get(CONF_CREDENTIALS, {}) or {})
    return creds


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_STATIONS})
@websocket_api.async_response
async def ws_stations(hass: HomeAssistant, connection, msg) -> None:
    """Return every Danish station with coordinates and current prices."""
    session = async_get_clientsession(hass)
    stations = await fetch_all(session, _credentials(hass))

    # Stations without provider coordinates (Q8/F24) get their postnummer centre.
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
            }
        )

    connection.send_result(msg["id"], {"stations": result})


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the Tankpriser websocket commands (called once)."""
    websocket_api.async_register_command(hass, ws_stations)

"""Diagnostics for Tankpriser.

Users paste these dumps into GitHub issues, so anything secret must be
redacted here — today that is the per-chain API keys.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_AREA_NAME, CONF_CREDENTIALS, CONF_NOTIFY_SERVICE, DOMAIN
from .sources import PROVIDERS

# Chain API keys, plus the fields that identify *this* user rather than the
# problem: the area's display name and the notify target (usually a device
# name). The postnummer is deliberately NOT redacted — it is the single most
# useful field when debugging why an area resolved to the wrong stations.
TO_REDACT = {CONF_CREDENTIALS, CONF_AREA_NAME, CONF_NOTIFY_SERVICE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    stations = coordinator.data.stations if coordinator and coordinator.data else []
    configured = set(entry.data.get(CONF_CREDENTIALS, {}) or {})

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        # Which chains have a key, without revealing any of them.
        "providers": {
            key: {
                "needs_credential": p.needs_credential,
                "credential_configured": key in configured,
                "experimental": p.experimental,
            }
            for key, p in PROVIDERS.items()
        },
        "station_count": len(stations),
        "companies": sorted({s.company for s in stations}),
    }

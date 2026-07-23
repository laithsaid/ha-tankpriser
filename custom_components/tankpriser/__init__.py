"""The Tankpriser integration."""

from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CARD_BASE_URL, CARD_URL, DOMAIN
from .coordinator import TankpriserCoordinator
from .websocket import async_register as async_register_ws

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

_CARD_REGISTERED = False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services (the national-stations websocket).

    The Lovelace card is registered here rather than in `async_setup_entry`:
    `add_extra_js_url` only affects frontend pages served *after* the call, so
    doing it at component setup makes the card available as early as possible.
    Registering it per-entry meant a client that loaded the frontend while the
    entry was still setting up (or retrying) got a page with no card script,
    and every dashboard using it rendered "Configuration error" until that
    client fetched a fresh index.html — which mobile apps rarely do, since
    pull-to-refresh reuses the cached document.
    """
    async_register_ws(hass)
    await _async_register_card(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entries. v1 (postnummer-based) still works as-is; the
    coordinator falls back to the stored postnummer when present."""
    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tankpriser from a config entry."""
    # Belt and braces: `async_setup` normally did this already, but an entry can
    # be added to a running instance whose component setup predates this code.
    await _async_register_card(hass)

    coordinator = TankpriserCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve and auto-register the bundled Lovelace card once."""
    global _CARD_REGISTERED  # noqa: PLW0603
    if _CARD_REGISTERED:
        return

    # Serve the whole www/ directory: the card lives at CARD_URL and its
    # vendored Leaflet build under CARD_BASE_URL/vendor/.
    www_path = os.path.join(os.path.dirname(__file__), "www")
    version = _card_version(hass)

    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_BASE_URL, www_path, False)]
        )
    except ImportError:
        # Older cores fall back to the sync registration helper.
        hass.http.register_static_path(CARD_BASE_URL, www_path, False)

    try:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Could not auto-register the Tankpriser card; add %s as a "
            "dashboard resource manually if the card does not appear.",
            CARD_URL,
        )

    _CARD_REGISTERED = True


def _card_version(hass: HomeAssistant) -> str:
    """Return the integration version, used for card cache-busting."""
    integration = hass.data.get("integrations", {}).get(DOMAIN)
    if integration is not None:
        return str(getattr(integration, "version", "0"))
    return "0"

"""The Tankpriser integration."""

from __future__ import annotations

import logging
import os

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CARD_BASE_URL, CARD_URL, DOMAIN, SUBENTRY_CAR
from .consumption import ConsumptionTracker
from .coordinator import TankpriserCoordinator
from .services import async_register_services
from .websocket import async_register as async_register_ws

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Tankpriser is configured entirely from the UI; there is nothing to put in
# configuration.yaml. Declaring this is required because we implement
# async_setup (to register the websocket command and the card early).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Each half of "publishing the card" is tracked separately: they fail
# independently, and a failed one must be retried rather than latched.
_STATIC_PATH_REGISTERED = False
_EXTRA_JS_REGISTERED = False
_RESOURCE_REGISTERED = False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services (the national-stations websocket).

    The Lovelace card is registered here rather than in `async_setup_entry`:
    `add_extra_js_url` only affects frontend pages served *after* the call, so
    doing it at component setup makes the card available as early as possible.
    Registering it per-entry meant a client that loaded the frontend while the
    entry was still setting up (or retrying) got a page with no card script,
    and every dashboard using it rendered "Configuration error" until that
    client fetched a fresh index.html — which mobile apps rarely do, since
    pull-to-refresh reuses the cached document. See `_async_register_card` for
    why that is no longer the only delivery route.
    """
    async_register_ws(hass)
    async_register_services(hass)
    await _async_register_card(hass)

    # `lovelace` may still be setting up when we get here (it is not a
    # dependency of ours — the card must also work without it). Try the
    # resource registration again once everything is up.
    if not _RESOURCE_REGISTERED:

        async def _retry(_event: Event) -> None:
            await _async_register_card(hass)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _retry)

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
    # Only the parts that have not succeeded yet are retried.
    await _async_register_card(hass)

    coordinator = TankpriserCoordinator(hass, entry)
    # Before the first refresh, so that refresh has last run's prices to compare
    # against instead of silently becoming the new baseline.
    await coordinator.async_restore_baseline()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _async_setup_cars(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_setup_cars(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: TankpriserCoordinator
) -> None:
    """Start a consumption tracker for each car subentry."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_CAR:
            continue
        tracker = ConsumptionTracker(hass, entry, subentry)
        try:
            await tracker.async_start()
        except Exception:  # noqa: BLE001 - one bad car must not break setup
            _LOGGER.exception("Could not start consumption tracker for %s", subentry.title)
            continue
        coordinator.cars[subentry.subentry_id] = tracker


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            for tracker in coordinator.cars.values():
                await tracker.async_stop()
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and make every client load it.

    The card is published two ways on purpose, because they reach different
    clients:

    * `add_extra_js_url` writes a <script> tag into index.html *as it is
      served*. A client that already holds an index.html from before the
      integration existed — or from a moment early in a restart, before this
      component was set up — never sees that tag. The Home Assistant apps hold
      on to that document for days (pull-to-refresh does not refetch it), so
      those clients render "Configuration error" indefinitely while every other
      device shows the card fine.
    * A Lovelace *resource* is fetched over the live websocket every time a
      dashboard opens, so it reaches exactly those stale clients. This is what
      HACS-installed cards use and it is the one that fixes first-open on a new
      phone, a new user, or after a restart.
    """
    global _STATIC_PATH_REGISTERED, _EXTRA_JS_REGISTERED, _RESOURCE_REGISTERED  # noqa: PLW0603

    # Serve the whole www/ directory: the card lives at CARD_URL and its
    # vendored Leaflet build under CARD_BASE_URL/vendor/.
    www_path = os.path.join(os.path.dirname(__file__), "www")
    version = _card_version(hass)
    url = f"{CARD_URL}?v={version}"

    if not _STATIC_PATH_REGISTERED:
        # No fallback for cores that lack StaticPathConfig: hacs.json requires
        # 2025.2 and it has existed since 2024.7, so the old
        # hass.http.register_static_path path could never run — and Home
        # Assistant has since deleted that method, so a "safety net" calling it
        # would only turn a clear ImportError into a puzzling AttributeError.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_BASE_URL, www_path, False)]
        )
        _STATIC_PATH_REGISTERED = True

    if not _EXTRA_JS_REGISTERED:
        try:
            from homeassistant.components.frontend import add_extra_js_url

            add_extra_js_url(hass, url)
            _EXTRA_JS_REGISTERED = True
        except Exception:  # noqa: BLE001
            # Not latched: a later call (entry setup, HA started) tries again.
            _LOGGER.warning(
                "Could not add the Tankpriser card to the frontend; add %s as a "
                "dashboard resource manually if the card does not appear.",
                CARD_URL,
            )

    if not _RESOURCE_REGISTERED:
        try:
            _RESOURCE_REGISTERED = await _async_register_lovelace_resource(hass, url)
        except Exception:  # noqa: BLE001 - never block setup over a dashboard nicety
            _LOGGER.debug("Could not register the Tankpriser Lovelace resource", exc_info=True)
        if _RESOURCE_REGISTERED:
            _LOGGER.debug("Tankpriser card published as a Lovelace resource: %s", url)
        elif hass.is_running:
            # Only the extra_js route is left, and that one misses any client
            # holding an older index.html — which is how a phone or tablet ends
            # up showing every Tankpriser card as a red error box while every
            # other device is fine. Said once, at startup, because it is the
            # only warning that explains that symptom.
            _LOGGER.warning(
                "Tankpriser could not add its card to the Lovelace resource "
                "list (dashboards in YAML mode own that list). If a device "
                "shows the cards as an error box, add %s as a dashboard "
                "resource of type 'JavaScript Module'.",
                url,
            )


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add (or update) the card in the Lovelace resource list.

    Returns True when the resource list holds our current URL. Storage-mode
    dashboards only: in YAML mode the resource list is owned by
    configuration.yaml and must not be written to, and there the index.html
    injection above is the whole story.
    """
    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        return False  # lovelace not set up (yet)

    resources = getattr(lovelace, "resources", None)
    # This is not a version check: ResourceYAMLCollection has no create/update,
    # so a dashboard in YAML mode owns its resource list and we leave it alone.
    if resources is None or not hasattr(resources, "async_create_item"):
        return False

    # The collection lazy-loads; async_items() is empty until it has.
    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True

    for item in resources.async_items():
        item_url = str(item.get("url", ""))
        if item_url.split("?")[0] != CARD_URL:
            continue
        # Ours already: keep the ?v= in step with the installed version, so an
        # update is not masked by a browser cache holding the old file.
        if item_url != url:
            await resources.async_update_item(
                item["id"], {"res_type": "module", "url": url}
            )
        return True

    await resources.async_create_item({"res_type": "module", "url": url})
    _LOGGER.debug("Registered the Tankpriser card as a Lovelace resource: %s", url)
    return True


def _card_version(hass: HomeAssistant) -> str:
    """Return the integration version, used for card cache-busting."""
    integration = hass.data.get("integrations", {}).get(DOMAIN)
    if integration is not None:
        return str(getattr(integration, "version", "0"))
    return "0"

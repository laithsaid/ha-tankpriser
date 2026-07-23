"""Config and options flow for Tankpriser."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .sources import (
    PROVIDERS,
    invalidate_cache,
    providers_needing_credential,
    validate_credential,
)
from .const import (
    CONF_AREA_NAME,
    CONF_CREDENTIALS,
    CONF_EXCLUDED_STATIONS,
    CONF_FUEL_TYPES,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_RULE,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_THRESHOLD,
    CONF_PROVIDER,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    DEFAULT_FUEL_TYPES,
    DEFAULT_NOTIFY_RULE,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    FUEL_TYPES,
    MIN_SCAN_INTERVAL_MIN,
    NOTIFY_RULES,
    RADIUS_OPTIONS,
)

FUEL_SELECT_OPTIONS = [
    selector.SelectOptionDict(value=key, label=meta[0])
    for key, meta in FUEL_TYPES.items()
]
RADIUS_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(options=RADIUS_OPTIONS)
)
FUEL_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=FUEL_SELECT_OPTIONS,
        multiple=True,
        mode=selector.SelectSelectorMode.LIST,
    )
)


class TankpriserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup.

    The area is your HA Home location; only the fuel types are asked for here.
    The radius (default 10 km) is adjustable afterwards under Options.
    """

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the fuel types to track (area = HA Home location)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_FUEL_TYPES):
                errors[CONF_FUEL_TYPES] = "no_fuel_types"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                area_name = str(user_input.get(CONF_AREA_NAME, "")).strip() or "Tankpriser"
                return self.async_create_entry(
                    title=area_name,
                    data={
                        CONF_FUEL_TYPES: user_input[CONF_FUEL_TYPES],
                        CONF_AREA_NAME: area_name,
                    },
                    options={
                        CONF_RADIUS: DEFAULT_RADIUS,
                        CONF_FUEL_TYPES: user_input[CONF_FUEL_TYPES],
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_AREA_NAME, default=""): str,
                vol.Required(
                    CONF_FUEL_TYPES, default=DEFAULT_FUEL_TYPES
                ): FUEL_SELECT,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return TankpriserOptionsFlow(entry)


class TankpriserOptionsFlow(OptionsFlow):
    """Edit radius, fuel types, station filter, notifications and chain keys."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        # Sub-steps each save a slice, so start from the current options and
        # merge — otherwise saving one page would wipe the others.
        self._options: dict[str, Any] = dict(entry.options)
        self._provider: str = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which group of settings to edit."""
        menu = ["settings", "notifications"]
        if providers_needing_credential():
            menu.append("chains")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Area, fuel types, hidden stations and poll interval."""
        if user_input is not None:
            self._options.update(user_input)
            return self._save()

        options = self._entry.options
        data = self._entry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RADIUS,
                    default=options.get(
                        CONF_RADIUS, data.get(CONF_RADIUS, DEFAULT_RADIUS)
                    ),
                ): RADIUS_SELECT,
                vol.Required(
                    CONF_FUEL_TYPES,
                    default=options.get(
                        CONF_FUEL_TYPES, data.get(CONF_FUEL_TYPES, DEFAULT_FUEL_TYPES)
                    ),
                ): FUEL_SELECT,
                vol.Optional(
                    CONF_EXCLUDED_STATIONS,
                    default=options.get(CONF_EXCLUDED_STATIONS, []),
                ): self._station_selector(),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MIN,
                        max=360,
                        step=5,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Price-change notification rule and target."""
        if user_input is not None:
            # Empty threshold -> drop it so it does not linger.
            if not user_input.get(CONF_NOTIFY_THRESHOLD):
                user_input.pop(CONF_NOTIFY_THRESHOLD, None)
                self._options.pop(CONF_NOTIFY_THRESHOLD, None)
            self._options.update(user_input)
            return self._save()

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NOTIFY_ENABLED,
                    default=options.get(CONF_NOTIFY_ENABLED, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    default=options.get(CONF_NOTIFY_SERVICE, ""),
                ): self._notify_service_selector(),
                vol.Required(
                    CONF_NOTIFY_RULE,
                    default=options.get(CONF_NOTIFY_RULE, DEFAULT_NOTIFY_RULE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=NOTIFY_RULES,
                        translation_key="notify_rule",
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_THRESHOLD,
                    description={
                        "suggested_value": options.get(CONF_NOTIFY_THRESHOLD)
                    },
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="notifications", data_schema=schema)

    # -- chains that need a personal API key --------------------------------
    async def async_step_chains(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List the chains that need a key, with their current status."""
        pending = providers_needing_credential()
        if not pending:
            # Nothing to configure: every chain we support is open today.
            return self.async_abort(reason="no_credential_chains")
        if user_input is not None:
            self._provider = user_input[CONF_PROVIDER]
            return await self.async_step_provider()

        stored = self._entry.data.get(CONF_CREDENTIALS, {}) or {}
        options = [
            selector.SelectOptionDict(
                value=p.key,
                label=f"{p.name} — {'configured' if stored.get(p.key) else 'no key yet'}"
                + (" (experimental)" if p.experimental else ""),
            )
            for p in pending
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="chains", data_schema=schema)

    async def async_step_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show one chain's how-to guide and take its API key.

        The guide text comes from the Provider record, so adding a chain never
        means touching this step or the translations.
        """
        provider = PROVIDERS[self._provider]
        errors: dict[str, str] = {}

        if user_input is not None:
            credential = str(user_input.get(CONF_API_KEY, "")).strip()
            stored = dict(self._entry.data.get(CONF_CREDENTIALS, {}) or {})
            if not credential:
                # Empty field = remove the key and stop using that chain.
                stored.pop(provider.key, None)
                return self._save_credentials(stored)

            session = async_get_clientsession(self.hass)
            try:
                found = await validate_credential(
                    session, provider.key, credential
                )
            except aiohttp.ClientResponseError as err:
                errors["base"] = (
                    "invalid_auth" if err.status in (401, 403) else "cannot_connect"
                )
            except (aiohttp.ClientError, ValueError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if not found:
                    # Accepted the key but returned nothing usable — the parser
                    # is wrong or the account has no access.
                    errors["base"] = "no_stations"
                else:
                    stored[provider.key] = credential
                    return self._save_credentials(stored)

        current = (self._entry.data.get(CONF_CREDENTIALS, {}) or {}).get(
            provider.key, ""
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_API_KEY,
                    description={"suggested_value": current},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="provider",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "name": provider.name,
                "guide": provider.guide or "",
                "signup_url": provider.signup_url or "",
                "warning": (
                    "\n\n⚠️ This chain is experimental: the parser has not been "
                    "verified against a real response yet."
                    if provider.experimental
                    else ""
                ),
            },
        )

    # -- saving --------------------------------------------------------------
    def _save(self) -> ConfigFlowResult:
        """Persist the merged options and finish the flow."""
        return self.async_create_entry(title="", data=self._options)

    def _save_credentials(self, credentials: dict[str, str]) -> ConfigFlowResult:
        """Persist API keys into entry *data* (options hold user preferences).

        The provider cache is dropped for the changed chain so a corrected key
        takes effect on the next refresh instead of after the 10-minute TTL.
        """
        invalidate_cache(self._provider)
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_CREDENTIALS: credentials},
        )
        return self._save()

    def _station_selector(self) -> selector.SelectSelector:
        """Multi-select of currently discovered stations (custom values ok)."""
        coordinator = (
            self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if self.hass
            else None
        )
        names: list[str] = []
        if coordinator is not None and coordinator.data is not None:
            names = sorted({s.name for s in coordinator.data.stations})
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=names,
                multiple=True,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _notify_service_selector(self) -> selector.SelectSelector:
        """Dropdown of available notify.* services."""
        services = []
        if self.hass:
            notify_services = self.hass.services.async_services().get("notify", {})
            services = sorted(f"notify.{name}" for name in notify_services)
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=services,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

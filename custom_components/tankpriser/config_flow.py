"""Config and options flow for Tankpriser."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .sources import (
    PROVIDERS,
    ProviderAuthError,
    country_needs_area,
    default_fuel_types,
    country_of,
    default_radius,
    fuel_types_for,
    invalidate_cache,
    providers_needing_credential,
    radius_options,
    validate_credential,
)
from .const import (
    CHAINS,
    CONF_ACCURACY_ENABLED,
    CONF_ANCHOR,
    CONF_CALIBRATION_ENABLED,
    CONF_FILLUP_ENABLED,
    CONF_FILLUP_LEVEL,
    CONF_FILLUP_MAPS,
    CONF_FILLUP_NEAR_KM,
    CONF_AREA_NAME,
    CONF_COUNTRY,
    CONF_CAR_FUEL,
    CONF_CREDENTIALS,
    CONF_DISCOUNTS,
    CONF_EXCLUDED_STATIONS,
    CONF_FUEL_TYPES,
    CONF_LEVEL_ATTRIBUTE,
    CONF_LEVEL_UNIT,
    CONF_NEARBY_RADIUS_KM,
    CONF_NEARBY_TRACKER,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_RULE,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_THRESHOLD,
    CONF_ODOMETER_ATTRIBUTE,
    CONF_ODOMETER_ENTITY,
    CONF_PROVIDER,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE_ENTITY,
    CONF_TANK_CAPACITY,
    COUNTRIES,
    DEFAULT_COUNTRY,
    DEFAULT_FUEL_TYPES,
    DEFAULT_NEARBY_RADIUS_KM,
    DEFAULT_NOTIFY_RULE,
    DEFAULT_SCAN_INTERVAL_MIN,
    MAX_DISCOUNT_ORE,
    DOMAIN,
    FUEL_TYPES,
    LEVEL_UNIT_PERCENT,
    LEVEL_UNITS,
    MIN_SCAN_INTERVAL_MIN,
    DEFAULT_MAPS,
    MAPS_URLS,
    NOTIFY_RULES,
    SUBENTRY_CAR,
    fuel_label,
)
from .fillup import DEFAULT_LOW_PCT, DEFAULT_NEAR_KM

COUNTRY_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=list(COUNTRIES),
        translation_key="country",
        mode=selector.SelectSelectorMode.LIST,
    )
)
# Every fuel we model, for the per-car picker: a car keeps the fuel it burns
# whichever country it is standing in.
FUEL_SELECT_OPTIONS = [
    selector.SelectOptionDict(value=key, label=label)
    for key, label in FUEL_TYPES.items()
]


def _radius_select(country: str) -> selector.SelectSelector:
    """Radius dropdown, limited to what this country's sources can serve."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=radius_options(country))
    )


def _fuel_select(country: str) -> selector.SelectSelector:
    """Fuel picker holding the fuels sold in this country, named as they are
    named there — a German forecourt sells Super E10, not Blyfri 95."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=key, label=fuel_label(key, country))
                for key in fuel_types_for(country)
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )
# Single-fuel picker used by the per-car flow (which fuel to price against).
CAR_FUEL_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=FUEL_SELECT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN
    )
)
LEVEL_UNIT_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(options=LEVEL_UNITS, translation_key="level_unit")
)
CAPACITY_SELECT = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=500, step=0.1, unit_of_measurement="L",
        mode=selector.NumberSelectorMode.BOX,
    )
)
ENTITY_SELECT = selector.EntitySelector()


def _keyed_providers(country: str) -> list:
    """Sources for a country that will not answer without a personal key."""
    return [p for p in providers_needing_credential() if p.country == country]


def _guides(providers: list) -> str:
    """The how-to text for each source, as one markdown block.

    Built from the Provider records so that adding a source never means editing
    this step or its translations.
    """
    return "\n\n".join(
        f"**{p.name}** — {p.signup_url}\n\n{p.guide}" for p in providers
    )


def _clean_anchor(value: Any) -> dict[str, float] | None:
    """Keep only the two numbers we need out of a LocationSelector value.

    The selector also returns a radius when asked to, and Home Assistant stores
    whatever we hand back verbatim; narrowing it here keeps the stored option a
    stable shape that `coordinator.anchor` can read without guessing.
    """
    if not isinstance(value, dict):
        return None
    try:
        return {
            "latitude": float(value["latitude"]),
            "longitude": float(value["longitude"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _clean_car_input(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate and normalise the per-car form; return (data, errors)."""
    data = dict(user_input)
    name = str(data.get(CONF_NAME, "")).strip()
    if not name:
        return {}, {CONF_NAME: "name_required"}
    data[CONF_NAME] = name

    # Optional dotted attribute paths: keep only when non-empty.
    for key in (CONF_LEVEL_ATTRIBUTE, CONF_ODOMETER_ATTRIBUTE):
        value = str(data.get(key, "")).strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)

    # An odometer attribute is meaningless without an odometer entity.
    if not str(data.get(CONF_ODOMETER_ENTITY, "")).strip():
        data.pop(CONF_ODOMETER_ENTITY, None)
        data.pop(CONF_ODOMETER_ATTRIBUTE, None)

    return data, {}


def _car_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the add/edit-a-car form, pre-filled from ``defaults``."""

    def suggest(key: str) -> dict[str, Any]:
        value = defaults.get(key)
        return {"suggested_value": value} if value not in (None, "") else {}

    return vol.Schema(
        {
            vol.Required(CONF_NAME, description=suggest(CONF_NAME)): selector.TextSelector(),
            vol.Required(
                CONF_SOURCE_ENTITY, description=suggest(CONF_SOURCE_ENTITY)
            ): ENTITY_SELECT,
            vol.Optional(
                CONF_LEVEL_ATTRIBUTE, description=suggest(CONF_LEVEL_ATTRIBUTE)
            ): selector.TextSelector(),
            vol.Required(
                CONF_LEVEL_UNIT,
                default=defaults.get(CONF_LEVEL_UNIT, LEVEL_UNIT_PERCENT),
            ): LEVEL_UNIT_SELECT,
            vol.Required(
                CONF_TANK_CAPACITY, description=suggest(CONF_TANK_CAPACITY)
            ): CAPACITY_SELECT,
            vol.Optional(
                CONF_ODOMETER_ENTITY, description=suggest(CONF_ODOMETER_ENTITY)
            ): ENTITY_SELECT,
            vol.Optional(
                CONF_ODOMETER_ATTRIBUTE, description=suggest(CONF_ODOMETER_ATTRIBUTE)
            ): selector.TextSelector(),
            vol.Required(
                CONF_CAR_FUEL,
                default=defaults.get(CONF_CAR_FUEL, DEFAULT_FUEL_TYPES[0]),
            ): CAR_FUEL_SELECT,
        }
    )


class TankpriserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup.

    Country first, because it decides everything the second step can offer: the
    sources, the fuels sold and — for a country whose API only answers about a
    circle — whether we need a point to search from at all. One entry per
    country, so a Dane who fills up across the German border can add both.
    """

    VERSION = 3

    def __init__(self) -> None:
        self._country: str = DEFAULT_COUNTRY
        self._credentials: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the country this entry covers."""
        if user_input is not None:
            self._country = user_input[CONF_COUNTRY]
            # One entry per country rather than the old single instance, so
            # both can coexist; an existing Danish entry was migrated onto this
            # same id, so re-adding Denmark still aborts as it always did.
            await self.async_set_unique_id(f"{DOMAIN}_{self._country}")
            self._abort_if_unique_id_configured()
            if _keyed_providers(self._country):
                return await self.async_step_credentials()
            return await self.async_step_area()

        # Home Assistant usually already knows where it is standing. Its own
        # country is an uppercase ISO code; ours are lowercase, because they
        # are also translation keys.
        configured = str(getattr(self.hass.config, "country", "") or "").lower()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_COUNTRY,
                    default=configured if configured in COUNTRIES else DEFAULT_COUNTRY,
                ): COUNTRY_SELECT
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the API keys this country cannot be read without.

        Asked here, before the entry exists, rather than left to Options: an
        entry with no key has nothing to fetch, so its very first refresh fails
        and it never finishes setting up — and Options is an awkward place to
        reach on an entry in that state. Better to find out now, especially for
        a key that is merely waiting to be activated.
        """
        providers = _keyed_providers(self._country)
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            for provider in providers:
                credential = str(user_input.get(provider.key, "")).strip()
                if not credential:
                    errors[provider.key] = "key_required"
                    continue
                try:
                    found = await validate_credential(
                        session, provider.key, credential
                    )
                except ProviderAuthError:
                    errors[provider.key] = "invalid_auth"
                except aiohttp.ClientResponseError as err:
                    errors[provider.key] = (
                        "invalid_auth"
                        if err.status in (401, 403)
                        else "cannot_connect"
                    )
                except (aiohttp.ClientError, ValueError, TimeoutError):
                    errors[provider.key] = "cannot_connect"
                else:
                    if found:
                        self._credentials[provider.key] = credential
                    else:
                        errors[provider.key] = "no_stations"
            if not errors:
                return await self.async_step_area()

        schema = vol.Schema(
            {
                vol.Required(
                    provider.key,
                    description={
                        "suggested_value": self._credentials.get(provider.key, "")
                    },
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                )
                for provider in providers
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"guides": _guides(providers)},
        )

    async def async_step_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the area and choose the fuels to track."""
        country = self._country
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_FUEL_TYPES):
                errors[CONF_FUEL_TYPES] = "no_fuel_types"
            else:
                # Falling back to the integration's own name produced
                # `sensor.tankpriser_super_e10` for a German entry, which says
                # nothing about where it is and collides with the next country
                # added. The country reads better and stays unique per entry;
                # the entry can be renamed afterwards either way.
                area_name = (
                    str(user_input.get(CONF_AREA_NAME, "")).strip()
                    or country_of(country).name
                )
                options: dict[str, Any] = {
                    CONF_RADIUS: default_radius(country),
                    CONF_FUEL_TYPES: user_input[CONF_FUEL_TYPES],
                }
                anchor = _clean_anchor(user_input.get(CONF_ANCHOR))
                if anchor:
                    options[CONF_ANCHOR] = anchor
                return self.async_create_entry(
                    title=area_name,
                    data={
                        CONF_COUNTRY: country,
                        CONF_FUEL_TYPES: user_input[CONF_FUEL_TYPES],
                        CONF_AREA_NAME: area_name,
                        CONF_CREDENTIALS: dict(self._credentials),
                    },
                    options=options,
                )

        fields: dict[Any, Any] = {
            vol.Optional(CONF_AREA_NAME, default=""): str,
            vol.Required(
                CONF_FUEL_TYPES, default=default_fuel_types(country)
            ): _fuel_select(country),
        }
        if country_needs_area(country):
            # Asked here rather than left to Options because without it the
            # very first refresh searches wherever Home is, which for this kind
            # of entry is quite often the wrong country entirely.
            fields[vol.Required(CONF_ANCHOR, default=self._home())] = (
                selector.LocationSelector()
            )

        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"country": country},
        )

    def _home(self) -> dict[str, float]:
        """Home Assistant's own position, as a LocationSelector value."""
        return {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
        }

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return TankpriserOptionsFlow(entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Cars are subentries, so the user can add as many as they want."""
        return {SUBENTRY_CAR: CarSubentryFlowHandler}


class CarSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one car for fuel-consumption prediction.

    A car only needs an entity that already exposes its fuel level (state or an
    attribute). Odometer is optional — with it we predict in L/100 km, without
    it we fall back to a time-based estimate.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new car."""
        return await self._async_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing car."""
        return await self._async_form(user_input, reconfigure=True)

    async def _async_form(
        self, user_input: dict[str, Any] | None, reconfigure: bool
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        defaults: dict[str, Any] = (
            dict(self._get_reconfigure_subentry().data) if reconfigure else {}
        )

        if user_input is not None:
            data, errors = _clean_car_input(user_input)
            if not errors:
                if reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=data[CONF_NAME],
                        data=data,
                    )
                return self.async_create_entry(title=data[CONF_NAME], data=data)
            defaults = user_input

        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=_car_schema(defaults),
            errors=errors,
        )


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
        menu = ["settings", "discounts", "notifications", "prediction"]
        if self._pending_providers():
            menu.append("chains")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Area, fuel types, hidden stations and poll interval."""
        country = self._country
        if user_input is not None:
            anchor = user_input.pop(CONF_ANCHOR, None)
            self._options.update(user_input)
            cleaned = _clean_anchor(anchor)
            if cleaned:
                self._options[CONF_ANCHOR] = cleaned
            return self._save()

        options = self._entry.options
        data = self._entry.data
        stored_radius = options.get(CONF_RADIUS, data.get(CONF_RADIUS, ""))
        schema_fields: dict[Any, Any] = {
                vol.Required(
                    CONF_RADIUS,
                    # An entry created before its country had a ceiling can hold
                    # a radius the dropdown no longer offers, and a default the
                    # selector cannot show is rejected outright.
                    default=stored_radius
                    if stored_radius in radius_options(country)
                    else default_radius(country),
                ): _radius_select(country),
                vol.Required(
                    CONF_FUEL_TYPES,
                    default=[
                        key
                        for key in options.get(
                            CONF_FUEL_TYPES,
                            data.get(CONF_FUEL_TYPES, DEFAULT_FUEL_TYPES),
                        )
                        if key in fuel_types_for(country)
                    ]
                    or default_fuel_types(country),
                ): _fuel_select(country),
                vol.Optional(
                    CONF_EXCLUDED_STATIONS,
                    default=options.get(CONF_EXCLUDED_STATIONS, []),
                ): self._station_selector(),
                # Optional: without it the "cheapest nearby" sensors are not
                # created at all (see sensor.py).
                vol.Optional(
                    CONF_NEARBY_TRACKER,
                    description={
                        "suggested_value": options.get(CONF_NEARBY_TRACKER, "")
                    },
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["device_tracker", "person", "sensor"]
                    )
                ),
                vol.Required(
                    CONF_NEARBY_RADIUS_KM,
                    default=options.get(
                        CONF_NEARBY_RADIUS_KM, DEFAULT_NEARBY_RADIUS_KM
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=1,
                        unit_of_measurement="km",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
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
        if country_needs_area(country):
            schema_fields[
                vol.Required(CONF_ANCHOR, default=self._anchor_default())
            ] = selector.LocationSelector()
        return self.async_show_form(
            step_id="settings", data_schema=vol.Schema(schema_fields)
        )

    @property
    def _country(self) -> str:
        """The country this entry covers (entries predating countries are DK)."""
        return str(self._entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY))

    def _anchor_default(self) -> dict[str, float]:
        """The stored search point, or Home Assistant's own position."""
        stored = _clean_anchor(self._entry.options.get(CONF_ANCHOR))
        if stored:
            return stored
        return {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
        }

    async def async_step_prediction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How the refuel prediction behaves: correction, and grading it."""
        if user_input is not None:
            self._options.update(user_input)
            return self._save()

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CALIBRATION_ENABLED,
                    default=options.get(CONF_CALIBRATION_ENABLED, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_ACCURACY_ENABLED,
                    default=options.get(CONF_ACCURACY_ENABLED, False),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="prediction", data_schema=schema)

    async def async_step_discounts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Loyalty discounts per chain, in øre per litre.

        Danish fuel cards are advertised as "20 øre/L hos OK", so øre is the
        unit people already have in their heads. Storing zero for every chain
        would bloat the options, so blanks are dropped.
        """
        if user_input is not None:
            self._options[CONF_DISCOUNTS] = {
                key: int(value)
                for key, value in user_input.items()
                if value and int(value) > 0
            }
            return self._save()

        current = self._entry.options.get(CONF_DISCOUNTS, {}) or {}
        schema = vol.Schema(
            {
                vol.Optional(key, default=float(current.get(key, 0))): (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=MAX_DISCOUNT_ORE,
                            step=1,
                            unit_of_measurement="øre/L",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                )
                for key, _label, _pattern in CHAINS
            }
        )
        return self.async_show_form(step_id="discounts", data_schema=schema)

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
                # The "fill up now" alert. It shares this page because it shares
                # the notify service above — one target, set once.
                vol.Required(
                    CONF_FILLUP_ENABLED,
                    default=options.get(CONF_FILLUP_ENABLED, False),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_FILLUP_LEVEL,
                    default=options.get(CONF_FILLUP_LEVEL, DEFAULT_LOW_PCT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=90,
                        step=5,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_FILLUP_NEAR_KM,
                    default=options.get(CONF_FILLUP_NEAR_KM, DEFAULT_NEAR_KM),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=50,
                        step=1,
                        unit_of_measurement="km",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_FILLUP_MAPS,
                    default=options.get(CONF_FILLUP_MAPS, DEFAULT_MAPS),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(MAPS_URLS), translation_key="maps"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="notifications", data_schema=schema)

    # -- chains that need a personal API key --------------------------------
    async def async_step_chains(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List the sources that need a key, with their current status."""
        pending = self._pending_providers()
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

    def _pending_providers(self) -> list:
        """Sources needing a key that serve *this* entry's country.

        A Danish entry must not be offered a German key: the source would never
        be fetched for it, so the dialog would be asking for something it has
        no use for.
        """
        return _keyed_providers(self._country)

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
            except ProviderAuthError:
                # The source said the key itself is no good — including the
                # "not activated yet" that every new Tankerkönig key returns
                # until a human there gets to it.
                errors["base"] = "invalid_auth"
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

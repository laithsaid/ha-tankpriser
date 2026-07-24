"""Per-car fuel-consumption tracking (the Home Assistant glue).

One :class:`ConsumptionTracker` per car subentry watches the car's fuel-level
entity, feeds normalised litre readings into the pure :mod:`prediction` model,
and persists the learned history via HA's ``Store``. Sensors subscribe to a
tracker to refresh when a new reading lands.

The prediction is free for everyone — see ``DONATE_URL``; we only ask.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import (
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAR_FUEL,
    CONF_LEVEL_ATTRIBUTE,
    CONF_LEVEL_UNIT,
    CONF_ODOMETER_ATTRIBUTE,
    CONF_ODOMETER_ENTITY,
    CONF_SOURCE_ENTITY,
    CONF_TANK_CAPACITY,
    LEVEL_UNIT_PERCENT,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .prediction import (
    ConsumptionModel,
    Prediction,
    dig,
    predict,
    to_float,
    to_litres,
)

_LOGGER = logging.getLogger(__name__)

# Persist at most this often for ordinary readings; a refuel flushes at once.
SAVE_DELAY_SECONDS = 300
# Ignore level wobble below this many litres so we do not store noise.
_EPSILON_L = 0.05
_MISSING = (None, "", STATE_UNKNOWN, STATE_UNAVAILABLE)


class ConsumptionTracker:
    """Learns one car's consumption from its fuel-level entity."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.subentry = subentry
        self.car_id = subentry.subentry_id

        cfg = subentry.data
        self.name: str = cfg[CONF_NAME]
        self.source_entity: str = cfg[CONF_SOURCE_ENTITY]
        self.level_attribute: str | None = cfg.get(CONF_LEVEL_ATTRIBUTE) or None
        self.level_unit: str = cfg.get(CONF_LEVEL_UNIT, LEVEL_UNIT_PERCENT)
        self.capacity_l: float = float(cfg[CONF_TANK_CAPACITY])
        self.odometer_entity: str | None = cfg.get(CONF_ODOMETER_ENTITY) or None
        self.odometer_attribute: str | None = cfg.get(CONF_ODOMETER_ATTRIBUTE) or None
        self.fuel_key: str | None = cfg.get(CONF_CAR_FUEL)

        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{self.car_id}"
        )
        self.model: ConsumptionModel = ConsumptionModel(self.capacity_l)
        self._unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []

    # -- lifecycle ----------------------------------------------------------
    async def async_start(self) -> None:
        """Load stored history, prime from the current state, subscribe."""
        raw = await self._store.async_load()
        if raw:
            try:
                self.model = ConsumptionModel.from_dict(raw, self.capacity_l)
            except (KeyError, ValueError, TypeError):
                _LOGGER.warning(
                    "Discarding unreadable consumption history for %s", self.name
                )
                self.model = ConsumptionModel(self.capacity_l)

        self._ingest_current()
        self._unsub = async_track_state_change_event(
            self.hass, [self.source_entity], self._handle_event
        )

    async def async_stop(self) -> None:
        """Unsubscribe and flush the latest history to disk."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        try:
            await self._store.async_save(self.model.as_dict())
        except Exception:  # noqa: BLE001 - never let teardown raise
            _LOGGER.exception("Failed to persist consumption history for %s", self.name)

    # -- listeners (sensors subscribe here) ---------------------------------
    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Register a sensor callback; returns an unsubscribe function."""
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # -- ingestion ----------------------------------------------------------
    @callback
    def _handle_event(self, event: Event) -> None:
        self._ingest_current()

    def _read(self, entity_id: str | None, attribute: str | None):
        """Current value of an entity's state or a (possibly nested) attribute."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _MISSING:
            return None
        if attribute:
            return dig(dict(state.attributes), attribute)
        return state.state

    @callback
    def _ingest_current(self) -> None:
        """Read the current level (+odometer), feed the model, persist, notify."""
        litres = to_litres(
            self._read(self.source_entity, self.level_attribute),
            self.level_unit,
            self.capacity_l,
        )
        if litres is None:
            return

        odo = None
        if self.odometer_entity:
            odo = to_float(self._read(self.odometer_entity, self.odometer_attribute))

        # Collapse repeats: a level unchanged within epsilon (and no new odo)
        # is not worth a new sample.
        if self.model.samples:
            last = self.model.samples[-1]
            if abs(last.litres - litres) < _EPSILON_L and (
                odo is None or last.odo == odo
            ):
                return

        now = dt_util.utcnow().timestamp()
        refuel = self.model.add_reading(now, litres, odo)

        if refuel:
            # A refuel just closed a tank — persist promptly so a restart keeps it.
            self._store.async_delay_save(self._data_for_save, 0)
        else:
            self._store.async_delay_save(self._data_for_save, SAVE_DELAY_SECONDS)
        self._notify()

    @callback
    def _data_for_save(self) -> dict:
        return self.model.as_dict()

    # -- prediction ---------------------------------------------------------
    def predict(self) -> Prediction | None:
        """Current prediction, or None while still learning."""
        return predict(self.model, self.model.current_litres)

    @property
    def current_litres(self) -> float | None:
        return self.model.current_litres

    @property
    def current_pct(self) -> float | None:
        current = self.model.current_litres
        if current is None or not self.capacity_l:
            return None
        return round(current / self.capacity_l * 100, 1)

    @property
    def location(self) -> tuple[float | None, float | None]:
        """Current lat/lon of the source entity, if it reports coordinates."""
        state = self.hass.states.get(self.source_entity)
        if state is None:
            return (None, None)
        lat = to_float(state.attributes.get("latitude"))
        lon = to_float(state.attributes.get("longitude"))
        if lat is None or lon is None:
            return (None, None)
        return (lat, lon)

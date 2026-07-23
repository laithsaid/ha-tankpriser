"""Sensor platform for Tankpriser."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FUEL_TYPES,
    DOMAIN,
    FUEL_TYPES,
)
from .coordinator import TankpriserCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tankpriser sensors for a config entry."""
    coordinator: TankpriserCoordinator = hass.data[DOMAIN][entry.entry_id]
    fuel_types = entry.options.get(
        CONF_FUEL_TYPES, entry.data.get(CONF_FUEL_TYPES, [])
    )
    entities = [
        TankpriserSensor(coordinator, entry, fuel_key)
        for fuel_key in fuel_types
        if fuel_key in FUEL_TYPES
    ]
    async_add_entities(entities)


class TankpriserSensor(CoordinatorEntity[TankpriserCoordinator], SensorEntity):
    """Cheapest price for one fuel type in a configured area.

    The full per-station price list is exposed via extra state attributes so
    the bundled Lovelace card (or a template) can render every station.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: TankpriserCoordinator,
        entry: ConfigEntry,
        fuel_key: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._fuel_key = fuel_key
        display, unit = FUEL_TYPES[fuel_key]
        self._attr_name = display
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{entry.entry_id}_{fuel_key}"
        self._attr_icon = _icon_for(fuel_key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Tankpriser",
            model=f"{coordinator.area_label} · {coordinator.radius}",
            configuration_url="https://github.com/laithsaid/ha_fuel_extension",
        )

    @property
    def native_value(self) -> float | None:
        """State is the cheapest available price for this fuel."""
        data = self.coordinator.data
        if data is None:
            return None
        cheapest = data.cheapest(self._fuel_key)
        return cheapest.prices[self._fuel_key] if cheapest else None

    @property
    def extra_state_attributes(self) -> dict:
        """Full station list and summary values for this fuel."""
        data = self.coordinator.data
        if data is None:
            return {}
        stations = data.stations_for(self._fuel_key)
        prices = [s.prices[self._fuel_key] for s in stations]
        cheapest = stations[0] if stations else None
        return {
            "fuel_type": FUEL_TYPES[self._fuel_key][0],
            "fuel_key": self._fuel_key,
            "area": self.coordinator.area_label,
            "radius": self.coordinator.radius,
            "station_count": len(stations),
            "cheapest_station": cheapest.name if cheapest else None,
            "cheapest_price": cheapest.prices[self._fuel_key] if cheapest else None,
            "average_price": round(sum(prices) / len(prices), 2) if prices else None,
            "stations": [
                {
                    "name": s.name,
                    "company": s.company,
                    "postnummer": s.postnummer,
                    "city": s.city,
                    "address": s.address,
                    "price": s.prices[self._fuel_key],
                    "updated": s.updated,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "coord_approx": s.coord_approx,
                }
                for s in stations
            ],
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


def _icon_for(fuel_key: str) -> str:
    """Pick a sensible mdi icon per fuel family."""
    if fuel_key.startswith("diesel"):
        return "mdi:fuel"
    if fuel_key in ("cng", "lng", "lpg"):
        return "mdi:gas-cylinder"
    return "mdi:gas-station"

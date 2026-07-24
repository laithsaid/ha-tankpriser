"""Sensor platform for Tankpriser."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FUEL_TYPES,
    DONATE_URL,
    DOMAIN,
    FUEL_TYPES,
)
from .consumption import ConsumptionTracker
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

    # One prediction sensor per car, attached to that car's subentry device.
    for car_id, tracker in coordinator.cars.items():
        async_add_entities(
            [CarPredictionSensor(coordinator, tracker)],
            config_subentry_id=car_id,
        )


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
            configuration_url="https://github.com/laithsaid/ha-tankpriser",
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


class CarPredictionSensor(CoordinatorEntity[TankpriserCoordinator], SensorEntity):
    """Predicted days until a car needs refuelling.

    The value comes from the per-car :class:`ConsumptionTracker`; the price
    coordinator is only used for the cheapest-station tie-in. State is
    ``unknown`` until enough tanks have been learned (never a wrong guess).
    The prediction is free — an attribute carries the donation link.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gas-station-outline"

    def __init__(
        self, coordinator: TankpriserCoordinator, tracker: ConsumptionTracker
    ) -> None:
        super().__init__(coordinator)
        self._tracker = tracker
        self._attr_name = "Days until refuel"
        self._attr_unique_id = (
            f"{tracker.entry.entry_id}_{tracker.car_id}_days_until_refuel"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, tracker.car_id)},
            name=tracker.name,
            manufacturer="Tankpriser",
            model="Fuel prediction",
            configuration_url="https://github.com/laithsaid/ha-tankpriser",
        )

    async def async_added_to_hass(self) -> None:
        """Also refresh when the tracker learns a new reading."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._tracker.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> float | None:
        prediction = self._tracker.predict()
        return prediction.days_until_empty if prediction else None

    @property
    def extra_state_attributes(self) -> dict:
        tracker = self._tracker
        prediction = tracker.predict()
        fuel_key = tracker.fuel_key
        attrs: dict = {
            "car_name": tracker.name,
            "current_level_l": (
                round(tracker.current_litres, 1)
                if tracker.current_litres is not None
                else None
            ),
            "current_level_percent": tracker.current_pct,
            "tank_capacity_l": tracker.capacity_l,
            "fuel_type": FUEL_TYPES.get(fuel_key, (None,))[0] if fuel_key else None,
            "donate_url": DONATE_URL,
            # Marks this as a Tankpriser car sensor so the card can plot it.
            "is_car": True,
        }
        # The car's live position, so the map can show it (if the source
        # entity reports coordinates, e.g. a device_tracker).
        lat, lon = tracker.location
        if lat is not None and lon is not None:
            attrs["latitude"] = lat
            attrs["longitude"] = lon

        if prediction is None:
            attrs["status"] = "learning"
            return attrs

        attrs["status"] = "ready"
        attrs["avg_consumption"] = prediction.avg_consumption
        attrs["consumption_unit"] = prediction.consumption_unit
        attrs["learned_tanks"] = prediction.segments
        attrs["confidence"] = prediction.confidence
        attrs["method"] = prediction.method
        if prediction.days_until_empty is not None:
            attrs["predicted_empty"] = (
                dt_util.now() + timedelta(days=prediction.days_until_empty)
            ).isoformat()

        # Cheapest station in the area for this car's fuel, if we track prices.
        data = self.coordinator.data
        cheapest = data.cheapest(fuel_key) if (data and fuel_key) else None
        if cheapest is not None:
            attrs["cheapest_station"] = cheapest.name
            attrs["cheapest_price"] = cheapest.prices.get(fuel_key)

        return attrs


def _icon_for(fuel_key: str) -> str:
    """Pick a sensible mdi icon per fuel family."""
    if fuel_key.startswith("diesel"):
        return "mdi:fuel"
    if fuel_key in ("cng", "lng", "lpg"):
        return "mdi:gas-cylinder"
    return "mdi:gas-station"

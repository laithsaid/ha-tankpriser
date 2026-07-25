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
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.location import distance as location_distance
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FUEL_TYPES,
    DONATE_URL,
    DOMAIN,
    FUEL_TYPES,
    NEARBY_MAX_STATIONS,
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
    entities: list[SensorEntity] = [
        TankpriserSensor(coordinator, entry, fuel_key)
        for fuel_key in fuel_types
        if fuel_key in FUEL_TYPES
    ]
    # Only worth existing when there is a device to rank against; without one
    # these would duplicate the area sensors with a distance of "unknown".
    if coordinator.nearby_tracker:
        entities += [
            NearbyStationsSensor(coordinator, entry, fuel_key)
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
            # True when any station here is priced with one of your discounts —
            # tells a reader whether these are pump prices or your prices.
            "discounted": any(s.discount_ore for s in stations),
            "average_price": round(sum(prices) / len(prices), 2) if prices else None,
            "stations": [
                {
                    "name": s.name,
                    "company": s.company,
                    "postnummer": s.postnummer,
                    "city": s.city,
                    "address": s.address,
                    # What you pay, discount already applied.
                    "price": s.prices[self._fuel_key],
                    # Present only when a discount changed the price, so a
                    # template can say "16,99 -> 16,79" without guessing.
                    "list_price": s.list_prices.get(self._fuel_key),
                    "discount_ore": s.discount_ore or None,
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
            # Diagnostics: exactly which entities/attributes this car is using,
            # so a missing map pin can be traced without guessing the config.
            "source_entity": tracker.source_entity,
            "level_attribute": tracker.level_attribute or "(state)",
            "odometer_entity": tracker.odometer_entity or "(none)",
        }
        # The car's live position, so the map can show it (if the source
        # entity reports coordinates, e.g. a device_tracker).
        lat, lon = tracker.location
        if lat is not None and lon is not None:
            attrs["latitude"] = lat
            attrs["longitude"] = lon
        # The car's own picture (if any), so the map marker can use it instead
        # of a generic car glyph.
        picture = tracker.picture
        if picture:
            attrs["car_picture"] = picture

        if prediction is None:
            # Nothing to go on at all: no completed tank, and the tank in
            # progress has not yet burnt enough to imply a rate.
            attrs["status"] = "learning"
            return attrs

        # "estimating" means the number is real but leans on the tank in
        # progress, so it will move as tanks complete. The card shows it with a
        # caveat rather than hiding it — a rough answer beats none.
        attrs["status"] = "estimating" if prediction.is_early else "ready"
        attrs["basis"] = prediction.basis
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


class NearbyStationsSensor(CoordinatorEntity[TankpriserCoordinator], SensorEntity):
    """Cheapest stations around a device you nominate — phone, or car.

    This exists for the surfaces the Lovelace card cannot reach. Neither
    CarPlay nor Android Auto will let Home Assistant draw a map, so the only way
    fuel prices reach a car dashboard is as an *entity*: Android Auto shows a
    sensor's state in its driving list, and offers navigation to any entity that
    carries latitude/longitude. So this sensor puts the cheapest nearby station's
    coordinates on itself — "navigate to Cheapest Blyfri 95 nearby" then routes
    you to the actual forecourt.

    It also flattens the work a Siri Shortcut would otherwise do in Jinja: the
    ranked list, with distances, is already in the attributes.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:map-marker-distance"

    def __init__(
        self,
        coordinator: TankpriserCoordinator,
        entry: ConfigEntry,
        fuel_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._fuel_key = fuel_key
        display, unit = FUEL_TYPES[fuel_key]
        self._attr_name = f"{display} cheapest nearby"
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{entry.entry_id}_{fuel_key}_nearby"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "Tankpriser",
            manufacturer="Tankpriser",
            model="Fuel prices",
        )

    async def async_added_to_hass(self) -> None:
        """Also refresh when the tracked device moves, not only on a poll.

        The whole point is proximity: waiting for the next price refresh would
        mean the distances describe where you were half an hour ago.
        """
        await super().async_added_to_hass()
        tracker = self.coordinator.nearby_tracker
        if tracker:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [tracker], self._handle_tracker_move
                )
            )

    @callback
    def _handle_tracker_move(self, _event) -> None:
        self.async_write_ha_state()

    def _origin(self) -> tuple[float, float] | None:
        """Where 'nearby' is measured from, or None if the device has no fix."""
        state = self.hass.states.get(self.coordinator.nearby_tracker)
        if state is None:
            return None
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        if lat is None or lon is None:
            return None
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None

    def _ranked(self) -> list[dict]:
        """Stations within the radius, cheapest first, each with its distance."""
        data = self.coordinator.data
        origin = self._origin()
        if data is None or origin is None:
            return []

        radius_m = self.coordinator.nearby_radius_km * 1000.0
        out: list[dict] = []
        for station in data.stations_for(self._fuel_key):
            if station.latitude is None or station.longitude is None:
                continue
            metres = location_distance(
                origin[0], origin[1], station.latitude, station.longitude
            )
            if metres is None or metres > radius_m:
                continue
            out.append(
                {
                    "name": station.name,
                    "company": station.company,
                    "city": station.city,
                    "price": station.prices[self._fuel_key],
                    "list_price": station.list_prices.get(self._fuel_key),
                    "discount_ore": station.discount_ore or None,
                    "distance_km": round(metres / 1000.0, 1),
                    "latitude": station.latitude,
                    "longitude": station.longitude,
                    # An estimated position must not be handed to a navigator;
                    # a caller can skip these or warn.
                    "coord_approx": station.coord_approx,
                }
            )
        # Cheapest first — that is the question being asked. Distance breaks a
        # tie, because two stations at the same price are not equally useful.
        out.sort(key=lambda s: (s["price"], s["distance_km"]))
        return out[:NEARBY_MAX_STATIONS]

    @property
    def native_value(self) -> float | None:
        ranked = self._ranked()
        return ranked[0]["price"] if ranked else None

    @property
    def extra_state_attributes(self) -> dict:
        ranked = self._ranked()
        best = ranked[0] if ranked else None
        attrs: dict = {
            "fuel_type": FUEL_TYPES[self._fuel_key][0],
            "fuel_key": self._fuel_key,
            "tracked_entity": self.coordinator.nearby_tracker,
            "radius_km": self.coordinator.nearby_radius_km,
            "station_count": len(ranked),
            "stations": ranked,
        }
        if best is not None:
            attrs["cheapest_station"] = best["name"]
            attrs["cheapest_price"] = best["price"]
            attrs["distance_km"] = best["distance_km"]
            # Android Auto offers navigation to any entity carrying a location,
            # so these two attributes ARE the in-car feature. Only for a station
            # we can actually place: routing to a postnummer centre would send
            # someone confidently to the wrong side of town.
            if not best["coord_approx"]:
                attrs["latitude"] = best["latitude"]
                attrs["longitude"] = best["longitude"]
        return attrs

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

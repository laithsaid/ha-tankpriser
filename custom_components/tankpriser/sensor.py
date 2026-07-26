"""Sensor platform for Tankpriser."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Final

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
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FUEL_TYPES,
    DONATE_URL,
    DOMAIN,
    FUEL_TYPES,
    NEARBY_MAX_STATIONS,
    SPOKEN_STATIONS,
)
from .consumption import ConsumptionTracker, zone_coords
from .coordinator import TankpriserCoordinator
from .nearby import rank_nearby


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
        # The ranking for the state currently being written; None means "recompute".
        self._ranked_cache: list[dict] | None = None
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
        """Re-rank on a tracker event, and write state only if it changed.

        The tracker fires for everything, not just movement — a battery level or
        any other attribute update arrives here too. Writing unconditionally put
        a fresh copy of the whole station list into the recorder every time, and
        while driving the distances differ on every GPS fix, so the database
        grew for readings nobody would ever look at.
        """
        fresh = self._compute_ranked()
        if fresh == self._ranked_cache:
            return
        self._ranked_cache = fresh
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """New prices invalidate the ranking, so drop it before writing."""
        self._ranked_cache = None
        super()._handle_coordinator_update()

    def _origin(self) -> tuple[float, float, str] | None:
        """Where 'nearby' is measured from, with how it was found.

        The device's own coordinates first. A tracker that reports by zone —
        and a GPS one that has gone quiet while parked — puts a zone name in
        its state instead, so fall back to that zone's position rather than
        answering "no stations nearby" from a device that is simply at home.
        """
        state = self.hass.states.get(self.coordinator.nearby_tracker)
        if state is None:
            return None
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        try:
            if lat is not None and lon is not None:
                return float(lat), float(lon), "tracker"
        except (TypeError, ValueError):
            pass
        zone_lat, zone_lon = zone_coords(self.hass, state.state)
        if zone_lat is not None and zone_lon is not None:
            return zone_lat, zone_lon, f"zone:{state.state}"
        return None

    def _ranked(self) -> list[dict]:
        """The current ranking, computed at most once per state write.

        `native_value` and `extra_state_attributes` are both read on every
        write, and each used to redo the whole filter-sort-and-measure pass.
        """
        if self._ranked_cache is None:
            self._ranked_cache = self._compute_ranked()
        return self._ranked_cache

    def _compute_ranked(self) -> list[dict]:
        """Every station within the radius, cheapest first, with its distance."""
        data = self.coordinator.data
        origin = self._origin()
        if data is None or origin is None:
            return []
        # The whole country, not the configured area: the device this follows
        # drives out of the area, and ranking inside it answered "the cheapest
        # three near you" with stations near *home*. Falls back to the area
        # list only if a refresh predates the nationwide snapshot.
        pool = data.nationwide or data.stations
        return rank_nearby(
            pool,
            origin[0],
            origin[1],
            self.coordinator.nearby_radius_km * 1000.0,
            self._fuel_key,
        )

    def _spoken(self, ranked: list[dict]) -> str:
        """The three cheapest as a sentence, in Home Assistant's language."""
        language = str(getattr(self.hass.config, "language", "") or "")
        return spoken_sentence(ranked, danish=language.lower().startswith("da"))

    @property
    def native_value(self) -> float | None:
        ranked = self._ranked()
        return ranked[0]["price"] if ranked else None

    @property
    def extra_state_attributes(self) -> dict:
        ranked = self._ranked()
        best = ranked[0] if ranked else None
        origin = self._origin()
        attrs: dict = {
            "fuel_type": FUEL_TYPES[self._fuel_key][0],
            "fuel_key": self._fuel_key,
            "tracked_entity": self.coordinator.nearby_tracker,
            "radius_km": self.coordinator.nearby_radius_km,
            # Where this ranking was measured from, and when that position
            # reached Home Assistant. A phone whose app has stopped reporting
            # answers confidently about the town you left, and there was no way
            # to tell that from the outside — these three say so.
            "origin_latitude": origin[0] if origin else None,
            "origin_longitude": origin[1] if origin else None,
            "origin_source": origin[2] if origin else "none",
            "position_updated": self._position_updated(),
            # How many are in range, not how many are listed below — the list is
            # capped and a count that silently equalled the cap read as "there
            # are only 8 stations near you", which was never true.
            "station_count": len(ranked),
            "listed_count": min(len(ranked), NEARBY_MAX_STATIONS),
            # Ready to hand to "Speak Text" in a Siri Shortcut, in HA's language.
            "spoken": self._spoken(ranked),
            "stations": ranked[:NEARBY_MAX_STATIONS],
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

    def _position_updated(self) -> str | None:
        """When the tracked device last told Home Assistant anything.

        The state's own timestamp: device trackers carry no "fix time", and
        what matters here is when we last heard from the device at all. A
        template can compare it with ``now()`` to see whether an answer is
        worth trusting — see docs/IN_THE_CAR.md.
        """
        state = self.hass.states.get(self.coordinator.nearby_tracker)
        return state.last_updated.isoformat() if state is not None else None


# Spelled out because "all 3 cost" is read aloud as "all three cost" by some
# voices and "all digit three" by others; the word is unambiguous.
_COUNT_WORDS: Final = {
    True: {2: "to", 3: "tre"},
    False: {2: "two", 3: "three"},
}

# "OK Nordre Ringvej 110" -> "OK Nordre Ringvej". Trailing house number, with an
# optional letter ("12B"), and nothing else — a name ending in a digit that is
# part of the brand ("Circle K 24/7") has no leading space before the number.
_HOUSE_NUMBER: Final = re.compile(r",?\s+\d+\s*[A-Za-z]?$")


def _spoken_place(station: dict) -> str:
    """How one station is named out loud.

    The station name, minus its house number: "one hundred and ten" is three
    syllables that cannot help you choose, and the map action is what actually
    navigates. Falls back to company and city for a source that gave no name —
    ambiguous when a chain has several forecourts in one town, but better than
    a silent gap.
    """
    short = _HOUSE_NUMBER.sub("", station.get("name") or "").strip()
    if short:
        return short
    return " ".join(p for p in (station.get("company"), station.get("city")) if p)


def spoken_sentence(ranked: list[dict], danish: bool) -> str:
    """The cheapest few stations as a sentence, ready to be read aloud.

    Built here rather than left to the user's template so a Siri Shortcut is one
    line instead of a Jinja loop — and so the phrasing is right: Danish wants a
    decimal comma, and "16,79 kroner" read out beats "16.79".

    Module level and pure so it can be tested without Home Assistant.
    """
    if not ranked:
        return "Ingen stationer i nærheden." if danish else "No stations nearby."

    def number(value: float, decimals: int = 2) -> str:
        text = f"{value:.{decimals}f}"
        return text.replace(".", ",") if danish else text

    top = ranked[:SPOKEN_STATIONS]
    # A chain often prices every forecourt identically — OK does, nationally —
    # and then repeating the figure per station spends the listener's attention
    # on the one number that never varies. Say it once up front and leave each
    # station with the only thing that does differ: how far away it is.
    same_price = len(top) > 1 and len({s["price"] for s in top}) == 1
    lines: list[str] = []
    if same_price:
        count = _COUNT_WORDS[danish].get(len(top), str(len(top)))
        price = number(top[0]["price"])
        lines.append(
            f"Alle {count} koster {price} kroner."
            if danish
            else f"All {count} cost {price} kroner."
        )

    label = "Nummer" if danish else "Number"
    unit = "kilometer" if danish else "kilometres"
    for index, station in enumerate(top, start=1):
        distance = f"{number(station['distance_km'], 1)} {unit}."
        if same_price:
            tail = distance
        else:
            tail = f"{number(station['price'])} kroner, {distance}"
        lines.append(f"{label} {index}: {_spoken_place(station)}, {tail}")
    return " ".join(lines)


def _icon_for(fuel_key: str) -> str:
    """Pick a sensible mdi icon per fuel family."""
    if fuel_key.startswith("diesel"):
        return "mdi:fuel"
    if fuel_key in ("cng", "lng", "lpg"):
        return "mdi:gas-cylinder"
    return "mdi:gas-station"

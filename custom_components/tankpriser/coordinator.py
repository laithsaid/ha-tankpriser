"""DataUpdateCoordinator for Tankpriser.

Aggregates the free Danish per-station price APIs (see ``sources.py``), filters
them to the configured postnummer + radius using DAWA (see ``geo.py``), and
exposes the result to the sensor platform. One coordinator per configured area;
provider fetches are cached and shared across areas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import geo
from .const import (
    CONF_CREDENTIALS,
    CONF_EXCLUDED_STATIONS,
    CONF_FUEL_TYPES,
    CONF_POSTNUMMER,
    CONF_RADIUS,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL_MIN,
    EVENT_PRICE_UPDATED,
    CONF_SCAN_INTERVAL,
    radius_to_metres,
)
from .notifications import evaluate_and_notify
from .sources import Station, fetch_all

_LOGGER = logging.getLogger(__name__)


@dataclass
class TankpriserData:
    """Parsed result of one refresh."""

    stations: list[Station]

    def stations_for(self, fuel_key: str) -> list[Station]:
        """Return stations offering the given fuel, cheapest first."""
        matching = [s for s in self.stations if fuel_key in s.prices]
        return sorted(matching, key=lambda s: s.prices[fuel_key])

    def cheapest(self, fuel_key: str) -> Station | None:
        """Return the cheapest station for a fuel, or None."""
        ordered = self.stations_for(fuel_key)
        return ordered[0] if ordered else None


class TankpriserCoordinator(DataUpdateCoordinator[TankpriserData]):
    """Fetches and filters fuel prices for one configured area."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.entry = entry
        options = entry.options
        scan_minutes = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title or "Tankpriser",
            update_interval=timedelta(minutes=scan_minutes),
        )
        self._session = async_get_clientsession(hass)
        # Cached area resolution: (radius_m) -> set of postnumre.
        self._area_cache: tuple[int, set[str]] | None = None
        # Per-car consumption trackers (subentry_id -> ConsumptionTracker),
        # populated by __init__.py after the first refresh.
        self.cars: dict = {}

    # -- configuration helpers ---------------------------------------------
    @property
    def postnummer(self) -> str:
        """Legacy postnummer, if this entry was created the old way (else '')."""
        return str(self.entry.data.get(CONF_POSTNUMMER, "")).strip()

    @property
    def area_label(self) -> str:
        """Human label for the sensor's area."""
        return self.postnummer or "Home"

    @property
    def radius(self) -> str:
        return self.entry.options.get(
            CONF_RADIUS, self.entry.data.get(CONF_RADIUS, DEFAULT_RADIUS)
        )

    @property
    def fuel_types(self) -> list[str]:
        return self.entry.options.get(
            CONF_FUEL_TYPES, self.entry.data.get(CONF_FUEL_TYPES, [])
        )

    @property
    def excluded_stations(self) -> list[str]:
        return self.entry.options.get(CONF_EXCLUDED_STATIONS, [])

    @property
    def credentials(self) -> dict[str, str]:
        """Per-chain API keys, for the chains that require one."""
        return dict(self.entry.data.get(CONF_CREDENTIALS, {}))

    # -- area resolution ----------------------------------------------------
    async def _resolve_area(self) -> set[str]:
        """Return the set of postnumre in range, resolved once per radius.

        New entries have no postnummer and use the HA Home location; old entries
        still resolve from their stored postnummer.
        """
        radius_m = radius_to_metres(self.radius)
        if self._area_cache is not None and self._area_cache[0] == radius_m:
            return self._area_cache[1]

        if self.postnummer:
            postnumre = await geo.postnumre_within(
                self._session, self.postnummer, radius_m
            )
        else:
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude
            if lat is None or lon is None:
                _LOGGER.warning(
                    "No HA Home location set; Tankpriser cannot resolve an area"
                )
                postnumre = set()
            else:
                postnumre = await geo.postnumre_within_point(
                    self._session, lat, lon, radius_m
                )

        self._area_cache = (radius_m, postnumre)
        _LOGGER.debug(
            "Area %s (%s) -> %d postnumre",
            self.area_label,
            self.radius,
            len(postnumre),
        )
        return postnumre

    # -- fetching -----------------------------------------------------------
    async def _async_update_data(self) -> TankpriserData:
        """Fetch providers, filter to the area, fill coords, notify."""
        area = await self._resolve_area()
        all_stations = await fetch_all(self._session, self.credentials)
        if not all_stations:
            raise UpdateFailed(
                "No data returned from any fuel-price provider; will retry."
            )

        stations = [s for s in all_stations if s.postnummer in area]

        # Approximate coordinates for stations without exact ones, using the
        # centre of their postnummer so they can still appear on a map.
        await self._fill_coordinates(stations)

        excluded = {e.strip().lower() for e in self.excluded_stations if e.strip()}
        if excluded:
            stations = [
                s for s in stations if s.name.strip().lower() not in excluded
            ]

        stations.sort(key=lambda s: s.name.lower())
        data = TankpriserData(stations=stations)

        # Change detection / notifications (needs the previous snapshot).
        previous = self.data
        if previous is not None:
            try:
                await evaluate_and_notify(self.hass, self.entry, previous, data)
            except Exception:  # noqa: BLE001 - never let notify break updates
                _LOGGER.exception("Tankpriser notification handling failed")

        self.hass.bus.async_fire(
            EVENT_PRICE_UPDATED,
            {
                "entry_id": self.entry.entry_id,
                "area": self.area_label,
                "radius": self.radius,
                "station_count": len(stations),
            },
        )
        return data

    async def _fill_coordinates(self, stations: list[Station]) -> None:
        """Give coordinate-less stations their postnummer centre (approximate)."""
        missing = {s.postnummer for s in stations if s.latitude is None}
        if not missing:
            return
        centers = await geo.centers_for(self._session, missing)
        for station in stations:
            if station.latitude is None and station.postnummer in centers:
                station.latitude, station.longitude = centers[station.postnummer]
                station.coord_approx = True

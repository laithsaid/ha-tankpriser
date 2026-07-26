"""DataUpdateCoordinator for Tankpriser.

Aggregates the free Danish per-station price APIs (see ``sources.py``), filters
them to the configured postnummer + radius using DAWA (see ``geo.py``), and
exposes the result to the sensor platform. One coordinator per configured area;
provider fetches are cached and shared across areas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import geo, geocode
from .const import (
    CONF_CREDENTIALS,
    CONF_DISCOUNTS,
    CONF_NEARBY_RADIUS_KM,
    CONF_NEARBY_TRACKER,
    DEFAULT_NEARBY_RADIUS_KM,
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
from .sources import Station, apply_discounts, fetch_all

_LOGGER = logging.getLogger(__name__)


@dataclass
class TankpriserData:
    """Parsed result of one refresh."""

    stations: list[Station]
    # Every station in the country, placed, for the "cheapest nearby" sensors:
    # the device they follow can be anywhere, so an area cut around Home is the
    # wrong pool to rank against. Empty unless a nearby tracker is configured —
    # placing the whole country costs geocoding nobody else needs.
    nationwide: list[Station] = field(default_factory=list)
    # fuel_key -> cheapest-first stations, built on first use. A refresh always
    # produces a new TankpriserData, so this cannot outlive its prices.
    _by_fuel: dict[str, list[Station]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def stations_for(self, fuel_key: str) -> list[Station]:
        """Return stations offering the given fuel, cheapest first.

        The list is shared between callers — every sensor read used to redo this
        filter and sort — so treat it as read-only.
        """
        ordered = self._by_fuel.get(fuel_key)
        if ordered is None:
            matching = [s for s in self.stations if fuel_key in s.prices]
            ordered = sorted(matching, key=lambda s: s.prices[fuel_key])
            self._by_fuel[fuel_key] = ordered
        return ordered

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
        # Cached area resolution: (radius_m, origin) -> set of postnumre. The
        # origin is part of the key because moving HA's Home location changes
        # the answer, and that used to keep serving the old area until reload.
        self._area_cache: tuple[tuple[int, object], set[str]] | None = None
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
    def discounts(self) -> dict[str, int]:
        """Per-chain loyalty discount in øre/L, e.g. {"ok": 20}."""
        return dict(self.entry.options.get(CONF_DISCOUNTS, {}) or {})

    @property
    def nearby_tracker(self) -> str:
        """Entity whose position the "nearby" sensors rank against ('' = off)."""
        return str(self.entry.options.get(CONF_NEARBY_TRACKER, "") or "")

    @property
    def nearby_radius_km(self) -> float:
        return float(
            self.entry.options.get(CONF_NEARBY_RADIUS_KM, DEFAULT_NEARBY_RADIUS_KM)
        )

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
        lat = self.hass.config.latitude
        lon = self.hass.config.longitude
        origin: object = self.postnummer or (lat, lon)
        key = (radius_m, origin)
        if self._area_cache is not None and self._area_cache[0] == key:
            return self._area_cache[1]

        if self.postnummer:
            postnumre = await geo.postnumre_within(
                self._session, self.postnummer, radius_m
            )
        elif lat is None or lon is None:
            _LOGGER.warning(
                "No HA Home location set; Tankpriser cannot resolve an area"
            )
            postnumre = set()
        else:
            postnumre = await geo.postnumre_within_point(
                self._session, lat, lon, radius_m
            )

        self._area_cache = (key, postnumre)
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

        # Re-price for this driver's loyalty cards before anything reads a
        # price: cheapest-of, notifications and the card then all agree, and
        # none of them needs to know discounts exist. Done for the whole country
        # so the area list and the nearby list quote the same numbers.
        priced = apply_discounts(all_stations, self.discounts)

        excluded = {e.strip().lower() for e in self.excluded_stations if e.strip()}
        if excluded:
            priced = [s for s in priced if s.name.strip().lower() not in excluded]

        stations = [s for s in priced if s.postnummer in area]

        # The nearby sensors rank against every station in the country: they
        # follow a device that drives out of the area, and ranking within the
        # area kept offering stations at home to someone halfway to the next
        # town. Only paid for when such a sensor exists.
        nationwide = priced if self.nearby_tracker else []

        # Approximate coordinates for stations without exact ones, using the
        # centre of their postnummer so they can still appear on a map. The
        # area stations are members of `nationwide`, so filling that fills both.
        await self._fill_coordinates(nationwide or stations)

        stations.sort(key=lambda s: s.name.lower())
        data = TankpriserData(stations=stations, nationwide=nationwide)

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
        """Position the stations whose provider ships no coordinates (Q8/F24).

        Two tiers, best first: the station's own street address geocoded via
        DAWA (the actual forecourt — good enough to navigate to), then the
        centre of its postnummer as a visible placeholder.
        """
        missing = [s for s in stations if s.latitude is None]
        if not missing:
            return

        geocoder = geocode.async_get(self.hass)
        await geocoder.async_load()
        unresolved = geocoder.apply(missing)
        if unresolved:
            # Background, not awaited: a fresh install is ~240 DAWA lookups.
            # This refresh falls back to postnummer centres; the refresh that
            # `async_request_refresh` triggers when the lookups land has the
            # real coordinates.
            geocoder.async_schedule(
                self._session, unresolved, self.async_request_refresh
            )

        pending = {s.postnummer for s in stations if s.latitude is None}
        if not pending:
            return
        centers = await geo.centers_for(self._session, pending)
        for station in stations:
            if station.latitude is None and station.postnummer in centers:
                station.latitude, station.longitude = centers[station.postnummer]
                station.coord_approx = True

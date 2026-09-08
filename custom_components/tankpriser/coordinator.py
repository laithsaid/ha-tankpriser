"""DataUpdateCoordinator for Tankpriser.

One coordinator per config entry, and one entry per country. How an entry gets
its stations depends on what that country's sources can answer:

* Denmark's chains publish the whole country, so we fetch it once (shared with
  every other reader) and cut it down to the configured postnummer + radius
  using DAWA — see ``geo.py``.
* Germany's Tankerkoenig only answers about a circle, so the circle *is* the
  query. It is anchored at a fixed point rather than at whatever the phone is
  doing, because notifications and history compare one refresh with the next
  and a moving area would swap the whole station list on every drive.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import geo, geocode
from .nearby import country_for_position
from .const import (
    BASELINE_SAVE_DELAY,
    CONF_ANCHOR,
    CONF_FILLUP_ENABLED,
    CONF_COUNTRY,
    DEFAULT_COUNTRY,
    MAX_BASELINE_AGE,
    PRICE_STORAGE_KEY_PREFIX,
    PRICE_STORAGE_VERSION,
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
    DOMAIN,
    EVENT_PRICE_UPDATED,
    CONF_SCAN_INTERVAL,
    radius_to_metres,
)
from .notifications import evaluate_and_notify, evaluate_and_notify_fillup
from .sources import (
    Area,
    Station,
    apply_discounts,
    area_for,
    country_needs_area,
    country_of,
    fetch_all,
    without_hidden,
)

_LOGGER = logging.getLogger(__name__)


def credentials_of(hass: HomeAssistant) -> dict[str, str]:
    """Per-chain API keys from every configured entry.

    Integration-wide callers (the websocket command, the services) are not tied
    to one entry, so they use whatever keys any entry has; today there is only
    ever one.
    """
    creds: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        creds.update(entry.data.get(CONF_CREDENTIALS, {}) or {})
    return creds


def discounts_of(hass: HomeAssistant) -> dict[str, int]:
    """Per-chain loyalty discounts from every configured entry."""
    discounts: dict[str, int] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        discounts.update(entry.options.get(CONF_DISCOUNTS, {}) or {})
    return discounts


def exclusions_of(hass: HomeAssistant) -> set[str]:
    """Station names the user has hidden, from every configured entry.

    Hiding a station meant hiding it from the area sensor and nowhere else: the
    map and the voice answer are built from the shared pool, which never saw
    this list. So a forecourt you had told the integration you would never use
    stayed on the map, and could still be the station Siri sent you to — which
    is the one place being wrong actually costs you a detour.
    """
    hidden: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        for name in entry.options.get(CONF_EXCLUDED_STATIONS, []) or []:
            cleaned = str(name).strip().lower()
            if cleaned:
                hidden.add(cleaned)
    return hidden


def _entry_anchor(hass: HomeAssistant, entry: ConfigEntry) -> tuple[float, float] | None:
    """An entry's anchor, read without needing it to be loaded."""
    stored = entry.options.get(CONF_ANCHOR) or {}
    latitude = stored.get("latitude")
    longitude = stored.get("longitude")
    if latitude is None or longitude is None:
        latitude, longitude = hass.config.latitude, hass.config.longitude
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


def entry_for_position(
    hass: HomeAssistant,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ConfigEntry | None:
    """The entry that answers for a position, or the first one when unplaced.

    A caller that knows where the phone is must be answered by the country the
    phone is in. Before this existed the first configured entry answered for
    everywhere: with Denmark set up first, asking for cheap fuel outside
    Hamburg searched Danish stations and said there was nothing within 15 km.

    Countries are matched by their box. Along a shared border the boxes
    overlap on purpose, and the tie goes to whichever entry is anchored
    nearer — for someone who configured both, the nearer anchor is the one
    they meant.
    """
    entries = list(hass.config_entries.async_entries(DOMAIN))
    if not entries:
        return None
    if latitude is None or longitude is None:
        return entries[0]

    # The rule itself is pure and lives in nearby.py, where it is tested
    # without Home Assistant; this only shapes the entries for it.
    return country_for_position(
        [
            (
                entry,
                country_of(str(entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY))),
                _entry_anchor(hass, entry),
            )
            for entry in entries
        ],
        latitude,
        longitude,
    )


def pool_target(
    hass: HomeAssistant,
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[str, Area | None]:
    """Which country an integration-wide caller means, and which circle.

    The map and the voice service are not tied to one entry. Given a position
    the entry for that position answers; without one the first entry does. A
    country whose sources only take a circle also needs one, and the entry's
    own anchored area is the honest default — it is the pool its sensors
    already describe.
    """
    entry = entry_for_position(hass, latitude, longitude)
    if entry is None:
        return DEFAULT_COUNTRY, None
    country = str(entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY)).lower()
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if country_needs_area(country) and coordinator is not None:
        return country, coordinator.search_area
    return country, None


def entry_coordinator(
    hass: HomeAssistant,
    latitude: float | None = None,
    longitude: float | None = None,
):
    """The coordinator an integration-wide caller should ask, or None.

    Same rule as `pool_target`, and kept next to it so the two never disagree
    about which entry that is.
    """
    entry = entry_for_position(hass, latitude, longitude)
    if entry is None:
        return None
    return hass.data.get(DOMAIN, {}).get(entry.entry_id)


async def async_station_pool(
    hass: HomeAssistant,
    credentials: dict[str, str],
    discounts: dict[str, int],
    country: str = DEFAULT_COUNTRY,
    area: Area | None = None,
) -> list[Station]:
    """The stations an integration-wide caller may choose from, placed on the map.

    Shared by the map's websocket command and the ``nearby`` service so both
    quote the same prices from the same positions. For Denmark that pool is the
    whole country; for an area-scoped country it is the one circle named by
    ``area``, because no larger pool exists to draw from.

    Everything underneath is cached — the provider fetches, the geocode store,
    the postnummer centres — so a repeat call costs little more than the
    discount pass.

    Stations that still cannot be placed keep ``latitude = None``; callers drop
    them, because a station without a position can be neither mapped nor ranked
    by distance.
    """
    session = async_get_clientsession(hass)
    stations = without_hidden(
        apply_discounts(
            await fetch_all(session, credentials, country, area), discounts
        ),
        exclusions_of(hass),
    )
    if country_needs_area(country):
        # Every station from an area source arrives with exact coordinates —
        # it had to, to be found by a radius query — so there is nothing to
        # geocode, and DAWA below only knows Danish addresses anyway.
        return stations

    geocoder = geocode.async_get(hass)
    await geocoder.async_load()
    unresolved = geocoder.apply([s for s in stations if s.latitude is None])
    if unresolved:
        geocoder.async_schedule(session, unresolved)

    missing = {s.postnummer for s in stations if s.latitude is None}
    centers = await geo.centers_for(session, missing) if missing else {}
    for station in stations:
        if station.latitude is None and station.postnummer in centers:
            station.latitude, station.longitude = centers[station.postnummer]
            station.coord_approx = True
    return stations


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


def baseline_payload(stations: list[Station], now: float) -> dict:
    """What has to survive a restart for change detection to keep working.

    Only the name and the prices: those are all the notification rules read
    (`cheapest`, and the per-station map behind "any price change"). Positions,
    addresses and coordinates are re-fetched every refresh anyway, and leaving
    them out keeps the stored file small and free of anything worth redacting.
    """
    return {
        "saved": now,
        "stations": [
            {"name": station.name, "prices": dict(station.prices)}
            for station in stations
            if station.prices
        ],
    }


def stations_from_baseline(stored: object, now: float) -> list[Station] | None:
    """Rebuild the stored prices, or None if there is nothing usable.

    Deliberately forgiving: this file is read once at startup, and a corrupt or
    half-written one must cost a single missed comparison, never a failed setup.
    Anything unparseable is simply no baseline at all.
    """
    if not isinstance(stored, dict):
        return None
    try:
        age = now - float(stored.get("saved") or 0)
    except (TypeError, ValueError):
        return None
    # A negative age means the clock moved backwards (a Pi with no RTC catching
    # up over NTP is the usual cause); trusting it would compare against the
    # future, so treat it as no baseline rather than guess.
    if age < 0 or age > MAX_BASELINE_AGE:
        return None

    stations: list[Station] = []
    for record in stored.get("stations") or []:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "")
        prices: dict[str, float] = {}
        for fuel, price in (record.get("prices") or {}).items():
            try:
                prices[str(fuel)] = float(price)
            except (TypeError, ValueError):
                continue
        if name and prices:
            stations.append(
                Station(name=name, company="", postnummer="", updated="", prices=prices)
            )
    return stations or None


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
        # What the fill-up alert has already said, per car, so a car parked low
        # on the drive is mentioned once rather than every half hour. In memory
        # on purpose — see fillup.FillupMemory.
        self.fillup_memory: dict = {}
        # Last prices seen before this process started, so the first refresh
        # after a restart still has something to compare against. Used once,
        # then the live snapshot takes over.
        self._store: Store = Store(
            hass,
            PRICE_STORAGE_VERSION,
            f"{PRICE_STORAGE_KEY_PREFIX}_{entry.entry_id}",
        )
        self._restored: TankpriserData | None = None

    # -- configuration helpers ---------------------------------------------
    @property
    def postnummer(self) -> str:
        """Legacy postnummer, if this entry was created the old way (else '')."""
        return str(self.entry.data.get(CONF_POSTNUMMER, "")).strip()

    @property
    def country(self) -> str:
        """The country this entry covers. Entries predating countries are Danish."""
        return str(self.entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY)).lower()

    @property
    def anchor(self) -> tuple[float | None, float | None]:
        """The fixed point this entry searches from.

        A nominated point if there is one, else Home. Nominated matters for the
        case the anchor was added for: a Dane watching German prices over the
        border has a Home that is in the wrong country entirely.
        """
        stored = self.entry.options.get(CONF_ANCHOR) or {}
        latitude = stored.get("latitude")
        longitude = stored.get("longitude")
        if latitude is None or longitude is None:
            return self.hass.config.latitude, self.hass.config.longitude
        return float(latitude), float(longitude)

    @property
    def search_area(self) -> Area | None:
        """The circle to ask an area-scoped source about, or None if unplaced."""
        latitude, longitude = self.anchor
        if latitude is None or longitude is None:
            return None
        # Capped to what the source will serve: it silently answers a
        # smaller circle than asked for, and what we *say* we searched has to
        # stay true — the spoken "nothing within N kilometres" depends on it.
        return area_for(
            self.country, latitude, longitude, radius_to_metres(self.radius)
        )

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
    def fillup_enabled(self) -> bool:
        """Whether the "fill up now" alert is switched on for this entry."""
        return bool(self.entry.options.get(CONF_FILLUP_ENABLED, False))

    @property
    def credentials(self) -> dict[str, str]:
        """Per-chain API keys, for the chains that require one."""
        return dict(self.entry.data.get(CONF_CREDENTIALS, {}))

    # -- change-detection baseline ------------------------------------------
    async def async_restore_baseline(self) -> None:
        """Load the last prices we saw, before the first refresh runs.

        Without this a restart silently swallowed one price change: `self.data`
        started empty, so the first refresh became the new baseline and the move
        it spanned was never announced. Prices change about once a day, so that
        was most of them for anyone who restarts often.
        """
        try:
            stored = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a bad file must not block setup
            _LOGGER.debug("Could not read the stored prices", exc_info=True)
            return
        stations = stations_from_baseline(stored, time.time())
        if stations is None:
            return
        self._restored = TankpriserData(stations=stations)
        _LOGGER.debug(
            "Restored %d stations to compare this refresh against", len(stations)
        )

    def _remember_baseline(self, data: TankpriserData) -> None:
        """Persist the prices this refresh saw, for the next process to use."""
        stations = list(data.stations)
        self._store.async_delay_save(
            lambda: baseline_payload(stations, time.time()), BASELINE_SAVE_DELAY
        )

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
        """Fetch this country's prices, cut them to the area, notify."""
        if country_needs_area(self.country):
            stations, nationwide = await self._area_country_stations()
        else:
            stations, nationwide = await self._national_country_stations()

        stations.sort(key=lambda s: s.name.lower())
        data = TankpriserData(stations=stations, nationwide=nationwide)

        # Change detection / notifications (needs the previous snapshot). On the
        # first refresh of a process that is whatever the last one stored, so a
        # restart no longer eats the change it spanned.
        previous = self.data if self.data is not None else self._restored
        self._restored = None
        if previous is not None:
            try:
                await evaluate_and_notify(self.hass, self.entry, previous, data)
            except Exception:  # noqa: BLE001 - never let notify break updates
                _LOGGER.exception("Tankpriser notification handling failed")

        # Needs no previous snapshot: it compares a car against the prices in
        # front of it, not this refresh against the last one, so it is useful
        # from the very first refresh after a restart. The snapshot is handed
        # over rather than read back off the coordinator, which has not been
        # given it yet at this point in the refresh.
        try:
            await evaluate_and_notify_fillup(self.hass, self.entry, self, data)
        except Exception:  # noqa: BLE001 - never let an alert break updates
            _LOGGER.exception("Tankpriser fill-up alert failed")
        self._remember_baseline(data)

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

    def _priced(self, all_stations: list[Station]) -> list[Station]:
        """Re-price for this driver's loyalty cards, then hide what they hid.

        Prices are adjusted before anything reads one, so cheapest-of, the
        notifications and the card all agree, and none of them needs to know
        discounts exist.
        """
        if not all_stations:
            raise UpdateFailed(
                "No data returned from any fuel-price provider; will retry."
            )
        hidden = {e.strip().lower() for e in self.excluded_stations if e.strip()}
        return without_hidden(apply_discounts(all_stations, self.discounts), hidden)

    async def _national_country_stations(self) -> tuple[list[Station], list[Station]]:
        """Denmark: fetch the country once, then cut it to the postnumre in range."""
        area = await self._resolve_area()
        priced = self._priced(
            await fetch_all(self._session, self.credentials, self.country)
        )
        stations = [s for s in priced if s.postnummer in area]

        # The nearby sensors rank against every station in the country: they
        # follow a device that drives out of the area, and ranking within the
        # area kept offering stations at home to someone halfway to the next
        # town. The fill-up alert needs the same pool for the same reason — a
        # car is exactly the thing that drives out of the area — so it also
        # pays for it. Skipped entirely when neither is in use, because placing
        # the whole country costs geocoding nobody else needs.
        nationwide = priced if (self.nearby_tracker or self.fillup_enabled) else []

        # Approximate coordinates for stations without exact ones, using the
        # centre of their postnummer so they can still appear on a map. The
        # area stations are members of `nationwide`, so filling that fills both.
        await self._fill_coordinates(nationwide or stations)
        return stations, nationwide

    async def _area_country_stations(self) -> tuple[list[Station], list[Station]]:
        """Germany: one circle around the anchor is the whole query.

        The source returns exactly what is in range, with exact coordinates, so
        there is nothing left to filter and nothing to geocode. The second list
        stays empty on purpose: there is no national pool for the "nearby"
        sensors to rank against and none can be built, so they fall back to
        this circle (see sensor.py). A phone that has driven out of it is
        answered by the `nearby` service instead, which searches from where the
        phone actually is.
        """
        area = self.search_area
        if area is None:
            raise UpdateFailed(
                "No search location set; give this area an anchor under "
                "Options, or set Home Assistant's Home location."
            )
        stations = self._priced(
            await fetch_all(self._session, self.credentials, self.country, area)
        )
        return stations, []

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

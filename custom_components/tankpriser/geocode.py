"""Turn station street addresses into real coordinates (DAWA), cached on disk.

Q8 and F24 publish a street address but no coordinates. Until now those ~240
stations were pinned at the centre of their postnummer, which is useless for
navigating and misleading on the map. DAWA — the official Danish address
register, free and keyless, already used by ``geo.py`` — resolves the address
they *do* give us.

Why DAWA and not Google: Google's Geocoding API needs a billed API key, and the
Maps Platform terms forbid displaying Google-derived coordinates on a non-Google
map (this card renders Leaflet/OpenStreetMap). DAWA is also simply better at
Danish addresses — measured over all 241 Q8/F24 stations (2026-07-25), the three
passes below place every one of them: 183 on the exact house number, 45 at street
level, 13 via the fuzzy pass (provider typos like "Nr. bjertevej" ->
"Nr. Bjertvej"). A postnummer centre is still the fallback for anything a future
feed change breaks.

Results are cached in ``.storage`` and re-verified every 180 days, so a station
that is rebuilt or re-listed at a corrected address is eventually picked up
without asking DAWA the same 241 questions every restart. All lookups run in a
background task — never on the setup path. A station that appears in a price feed
for the first time is geocoded on the poll that first sees it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DAWA_BASE_URL, DOMAIN, REQUEST_HEADERS
from .sources import Station

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.geocode"

_TIMEOUT = aiohttp.ClientTimeout(total=20)
# DAWA is a free public service with no published rate limit. Four in flight
# resolves the whole country in ~15 s while staying a polite guest.
_CONCURRENCY = 4
# Ceiling per background run, so a provider that starts shipping garbage
# addresses cannot turn into thousands of requests.
_MAX_PER_RUN = 500
# A miss is remembered, but not forever: a chain fixing a typo in its feed
# should eventually be picked up.
_RETRY_AFTER = timedelta(days=30)
# Nor is a *hit* kept forever. Stations are rebuilt, renumbered and occasionally
# listed at a corrected address, so every resolved position is re-verified twice
# a year. The cached coordinates stay in use while that happens, so a re-check is
# invisible unless it actually finds something different.
_REFRESH_AFTER = timedelta(days=180)


@callback
def async_get(hass: HomeAssistant) -> Geocoder:
    """Return the shared geocoder (one cache per install)."""
    # Shares hass.data[DOMAIN] with the coordinators, which are keyed by
    # entry_id (a uuid hex) — no collision with a plain string key.
    store: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    geocoder = store.get("geocoder")
    if geocoder is None:
        geocoder = store["geocoder"] = Geocoder(hass)
    return geocoder


class Geocoder:
    """Address -> coordinates, with a disk-backed cache."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        # normalised address -> {"lat","lon","approx","ts"} | {"failed": "<date>"}
        # where "ts"/"failed" is the ISO date of the last lookup attempt.
        self._cache: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._task: asyncio.Task | None = None

    async def async_load(self) -> None:
        """Read the cache from disk (once)."""
        if self._loaded:
            return
        self._loaded = True  # set first: a failed load must not retry forever
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a corrupt cache must not break setup
            _LOGGER.warning("Could not read the Tankpriser geocode cache; rebuilding")
            return
        if isinstance(data, dict):
            self._cache = {k: v for k, v in data.items() if isinstance(v, dict)}
            _LOGGER.debug("Geocode cache loaded: %d addresses", len(self._cache))

    # -- using the cache ----------------------------------------------------
    @callback
    def apply(self, stations: list[Station]) -> list[Station]:
        """Fill in coordinates we already know; return the ones to look up.

        The returned list is both the addresses never resolved *and* the ones
        whose cached position is due its periodic re-check. A due entry keeps its
        coordinates here, so re-verifying never blanks a pin that was working.

        Pure cache work, no network: safe to call on every refresh.
        """
        todo: list[Station] = []
        for station in stations:
            if station.latitude is not None:
                continue
            entry = self._cache.get(_key(station))
            coords = _coords_of(entry)
            if coords is None:
                if _worth_retrying(entry):
                    todo.append(station)
                continue
            station.latitude, station.longitude = coords[0], coords[1]
            # An address match is the real forecourt, not a postnummer blob —
            # only the fuzzy-corrected ones stay flagged as approximate.
            station.coord_approx = coords[2]
            if _due_refresh(entry):
                todo.append(station)
        return todo

    # -- filling the cache --------------------------------------------------
    @callback
    def async_schedule(
        self,
        session: aiohttp.ClientSession,
        stations: list[Station],
        on_done: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Resolve unknown addresses in the background, then call `on_done`.

        Deliberately not awaited by callers: geocoding a fresh install is ~240
        requests, and doing that on the config-entry setup path would trip Home
        Assistant's "setup is taking over 10 seconds" warning and delay the
        sensors. The map shows postnummer centres for one refresh instead, then
        `on_done` asks the coordinator to refresh with the real coordinates.
        """
        if self._task is not None and not self._task.done():
            return
        wanted = _unique(stations)[:_MAX_PER_RUN]
        if not wanted:
            return

        async def _run() -> None:
            try:
                found = await self._async_resolve(session, wanted)
            except Exception:  # noqa: BLE001 - background task, never crash HA
                _LOGGER.exception("Tankpriser geocoding failed")
                return
            if found and on_done is not None:
                await on_done()

        self._task = self.hass.async_create_background_task(
            _run(), name="tankpriser_geocode"
        )

    async def _async_resolve(
        self, session: aiohttp.ClientSession, stations: list[Station]
    ) -> int:
        """Look up every given address. Returns how many are new or changed.

        That count is what decides whether a coordinator refresh is worth asking
        for: a re-verification pass where every station still sits where it did
        should not shake the map.
        """
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        today = date.today().isoformat()
        found = 0

        async def _one(station: Station) -> None:
            nonlocal found
            key = _key(station)
            previous = self._cache.get(key)
            async with semaphore:
                result = await self._async_lookup(session, station)
            if result is None:
                if _coords_of(previous) is not None:
                    # A re-check that could not reach DAWA (or hit a feed typo)
                    # must not drop a position that was working. Keep it, and
                    # just stop asking again until the next interval.
                    self._cache[key] = {**previous, "ts": today}
                else:
                    self._cache[key] = {"failed": today}
                return
            lat, lon, approx = result
            self._cache[key] = {"lat": lat, "lon": lon, "approx": approx, "ts": today}
            if _coords_of(previous) is None:
                found += 1  # newly placed; a re-check that agrees changes nothing
            elif (lat, lon, approx) != _coords_of(previous):
                found += 1
                _LOGGER.info(
                    "Tankpriser: %s moved to %s,%s", station.address, lat, lon
                )

        _LOGGER.debug("Geocoding %d station addresses via DAWA", len(stations))
        await asyncio.gather(*(_one(s) for s in stations))
        # One write for the whole batch.
        self._store.async_delay_save(lambda: self._cache, 5)
        _LOGGER.info(
            "Tankpriser geocoding pass done: %d of %d addresses new or changed",
            found,
            len(stations),
        )
        return found

    async def _async_lookup(
        self, session: aiohttp.ClientSession, station: Station
    ) -> tuple[float, float, bool] | None:
        """Three passes, most precise first. Returns (lat, lon, approx)."""
        parts = _split(station.address, station.postnummer)
        if parts is None:
            return None
        street, house, city = parts
        postnummer = str(station.postnummer)

        # 1. The full address. Accepted only when DAWA agrees on the house
        #    number *and* the postnummer — a near-miss here is a wrong forecourt.
        if house:
            hit = await self._async_search(
                session, f"{street} {house}, {postnummer} {city}"
            )
            if (
                hit
                and str(hit.get("postnr")) == postnummer
                and _same_house(hit.get("husnr"), house)
            ):
                return (hit["y"], hit["x"], False)

        # 2. Street + postnummer. Covers house numbers DAWA cannot parse
        #    ("1-3", "81-83") and forecourts numbered differently to the feed;
        #    still the right street, so a few tens of metres out at worst.
        hit = await self._async_search(session, f"{street}, {postnummer}")
        if hit and str(hit.get("postnr")) == postnummer:
            return (hit["y"], hit["x"], False)

        # 3. Fuzzy, to absorb provider spelling ("Hirtshalsmotovejen"). Guarded
        #    by the postnummer so a fuzzy match cannot wander to another town,
        #    and flagged approximate because the street name was corrected for us.
        hit = await self._async_search(
            session, f"{street} {house}, {postnummer} {city}", fuzzy=True
        )
        if hit and str(hit.get("postnr")) == postnummer:
            return (hit["y"], hit["x"], True)

        _LOGGER.debug("No DAWA match for %s (%s)", station.address, postnummer)
        return None

    async def _async_search(
        self, session: aiohttp.ClientSession, text: str, fuzzy: bool = False
    ) -> dict[str, Any] | None:
        """One DAWA address search; returns the best record or None.

        `struktur=mini` is the cheap projection: it already carries x/y, so no
        second request is needed to turn a match into coordinates.
        """
        params = {"q": text.strip(), "struktur": "mini", "per_side": "1"}
        if fuzzy:
            params["fuzzy"] = ""
        # A bare "fuzzy=" is how DAWA enables fuzzy matching; urlencode keeps it.
        url = f"{DAWA_BASE_URL}/adresser?{urlencode(params)}"
        try:
            async with session.get(
                url, headers=REQUEST_HEADERS, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError, TimeoutError) as err:
            _LOGGER.debug("DAWA address search failed for %r: %s", text, err)
            return None
        if not isinstance(data, list) or not data:
            return None
        record = data[0]
        # No coordinates means nothing usable, whatever else matched.
        if not isinstance(record, dict) or record.get("x") is None:
            return None
        return record


# -- helpers ----------------------------------------------------------------
def _key(station: Station) -> str:
    """Cache key: the address as the provider spells it, normalised."""
    return " ".join(f"{station.address} {station.postnummer}".lower().split())


def _unique(stations: list[Station]) -> list[Station]:
    """One entry per distinct address (chains list the same site twice)."""
    seen: set[str] = set()
    out: list[Station] = []
    for station in stations:
        key = _key(station)
        if key in seen or not station.address:
            continue
        seen.add(key)
        out.append(station)
    return out


def _coords_of(entry: dict[str, Any] | None) -> tuple[float, float, bool] | None:
    """(lat, lon, approx) from a cache entry, or None."""
    if not entry or entry.get("lat") is None:
        return None
    try:
        return (float(entry["lat"]), float(entry["lon"]), bool(entry.get("approx")))
    except (TypeError, ValueError):
        return None


def _worth_retrying(entry: dict[str, Any] | None) -> bool:
    """True for an address never tried, or last tried long enough ago."""
    if entry is None:
        return True
    failed = entry.get("failed")
    if not failed:
        return False
    return _older_than(failed, _RETRY_AFTER)


def _due_refresh(entry: dict[str, Any] | None) -> bool:
    """True when a resolved position is due its periodic re-verification.

    An entry with no timestamp predates this check (or was written by an older
    version), so it is due — that costs one pass after an upgrade and gives
    everything a date from then on.
    """
    if not entry:
        return False
    checked = entry.get("ts")
    return True if not checked else _older_than(checked, _REFRESH_AFTER)


def _older_than(stamp: Any, age: timedelta) -> bool:
    """True if an ISO date string is at least `age` old (or unparseable)."""
    try:
        when = datetime.fromisoformat(str(stamp)).date()
    except ValueError:
        return True
    return date.today() - when >= age


def _same_house(found: Any, wanted: str) -> bool:
    """Compare house numbers the way Danes write them: 1b == 1B, 01 == 1."""
    return (
        str(found or "").strip().lower().lstrip("0")
        == str(wanted).strip().lower().lstrip("0")
    )


def _split(address: str, postnummer: str) -> tuple[str, str, str] | None:
    """Split a provider address into (street, house number, city).

    Q8/F24 ship "Dronningemaen 34 Svendborg 5700 Danmark", i.e. street, house,
    city, zip, country with no separators. The house number is taken to be the
    *last* token starting with a digit, so everything before it is the street
    name and everything after it is the city — which is what DAWA wants, in the
    order it wants ("Dronningemaen 34, 5700 Svendborg").
    """
    text = re.sub(r"\s*Danmark\s*$", "", str(address or "").strip(), flags=re.I)
    # Drop the postnummer if the provider left it in the string.
    if postnummer and (idx := text.rfind(str(postnummer))) != -1:
        text = text[:idx]
    text = text.strip().strip(",").strip()
    if not text:
        return None

    tokens = text.split()
    house_idx = None
    for index, token in enumerate(tokens):
        if token[:1].isdigit():
            house_idx = index
    if house_idx is None:
        return (text, "", "")
    return (
        " ".join(tokens[:house_idx]),
        tokens[house_idx],
        " ".join(tokens[house_idx + 1 :]),
    )

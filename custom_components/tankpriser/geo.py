"""Danish geography helpers backed by DAWA (api.dataforsyningen.dk).

DAWA is the official Danish address web API: free, no key, no bot gate. We use
it for two things:

* resolve a postnummer + radius into the set of postnumre inside that circle,
  so we can filter nationwide station feeds down to the configured area;
* look up a postnummer's visual-centre coordinates, used as an approximate
  marker position for stations whose provider ships no exact coordinates.
"""

from __future__ import annotations

import logging

import aiohttp

from .const import DAWA_BASE_URL, REQUEST_HEADERS

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# How many postnumre to resolve per DAWA request. Denmark has ~1100 in total,
# so a handful of requests covers the whole country; kept well below any URL
# length limit.
_BATCH_SIZE = 100

# postnummer -> (lat, lon); DAWA data changes ~never, so cache for process life.
_CENTER_CACHE: dict[str, tuple[float, float] | None] = {}


async def _get_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url, headers=REQUEST_HEADERS, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def postnummer_center(
    session: aiohttp.ClientSession, postnummer: str
) -> tuple[float, float] | None:
    """Return (lat, lon) of a postnummer's visual centre, or None."""
    postnummer = str(postnummer).strip()
    if postnummer in _CENTER_CACHE:
        return _CENTER_CACHE[postnummer]

    center: tuple[float, float] | None = None
    try:
        data = await _get_json(session, f"{DAWA_BASE_URL}/postnumre/{postnummer}")
        center = _center_of(data)
    except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError) as err:
        _LOGGER.debug("DAWA center lookup failed for %s: %s", postnummer, err)
        # Do not cache a transient failure as "no such postnummer" — that would
        # keep the station off the map for the rest of the process's life.
        return None

    _CENTER_CACHE[postnummer] = center
    return center


def _center_of(record) -> tuple[float, float] | None:
    """Pull (lat, lon) out of a DAWA postnummer record."""
    # visueltcenter is [lon, lat] — note the order.
    vc = record.get("visueltcenter") if isinstance(record, dict) else None
    if vc and len(vc) == 2:
        return (float(vc[1]), float(vc[0]))
    return None


async def postnumre_within_point(
    session: aiohttp.ClientSession, lat: float, lon: float, radius_m: int
) -> set[str]:
    """Return the set of postnumre whose area falls within radius of a point."""
    result: set[str] = set()
    # DAWA expects the circle as lon,lat,radius — wrong order returns HTTP 400.
    url = f"{DAWA_BASE_URL}/postnumre?cirkel={lon},{lat},{radius_m}"
    try:
        data = await _get_json(session, url)
        for item in data or []:
            nr = item.get("nr")
            if nr:
                result.add(str(nr))
    except (aiohttp.ClientError, ValueError, TypeError, TimeoutError) as err:
        _LOGGER.warning(
            "DAWA radius lookup failed for %s,%s (%s m): %s", lat, lon, radius_m, err
        )
    return result


async def postnumre_within(
    session: aiohttp.ClientSession, postnummer: str, radius_m: int
) -> set[str]:
    """Return the set of postnumre whose area falls within radius of postnummer.

    Always includes the input postnummer itself. Falls back to just that
    postnummer if DAWA cannot be reached.
    """
    postnummer = str(postnummer).strip()
    result: set[str] = {postnummer}

    center = await postnummer_center(session, postnummer)
    if center is None:
        return result

    lat, lon = center
    result |= await postnumre_within_point(session, lat, lon, radius_m)
    return result


async def centers_for(
    session: aiohttp.ClientSession, postnumre: set[str]
) -> dict[str, tuple[float, float]]:
    """Resolve a set of postnumre to their centre coordinates (best effort).

    DAWA accepts many postnumre in one call (``nr=8600|8620|...``), so this
    costs one request per BATCH_SIZE rather than one per postnummer. That
    matters for the national map, where every coordinate-less station needs a
    lookup and the naive version fired hundreds of concurrent requests at a
    free public API.
    """
    unknown = sorted(p for p in postnumre if p not in _CENTER_CACHE)
    for start in range(0, len(unknown), _BATCH_SIZE):
        batch = unknown[start : start + _BATCH_SIZE]
        try:
            data = await _get_json(
                session, f"{DAWA_BASE_URL}/postnumre?nr={'|'.join(batch)}"
            )
        except (aiohttp.ClientError, ValueError, TypeError, TimeoutError) as err:
            _LOGGER.debug("DAWA batch centre lookup failed (%d): %s", len(batch), err)
            continue

        for record in data or []:
            nr = record.get("nr")
            if nr:
                _CENTER_CACHE[str(nr)] = _center_of(record)
        # Anything DAWA did not return does not exist; remember that so the
        # next refresh does not ask again.
        for nr in batch:
            _CENTER_CACHE.setdefault(nr, None)

    return {
        p: _CENTER_CACHE[p]
        for p in postnumre
        if _CENTER_CACHE.get(p) is not None
    }

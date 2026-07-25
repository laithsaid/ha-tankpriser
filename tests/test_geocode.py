"""Tests for geocode.py — address parsing and the disk-cache refresh policy.

Run with: python tests/test_geocode.py   (no pytest, no dependencies)

`geocode.py` imports Home Assistant, which is not worth installing to test pure
logic, so the functions under test are lifted out of the source with `ast` and
executed against stubs. That keeps the tests fast and dependency-free at the
cost of one indirection: they are compiled from the real file, so they cannot
drift from it, but a *renamed* function shows up as a KeyError here rather than a
quiet pass. The names are listed in WANTED below.
"""

from __future__ import annotations

import ast
import asyncio
import io
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any

SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "tankpriser",
    "geocode.py",
)

WANTED = {
    "_split",
    "_same_house",
    "_worth_retrying",
    "_due_refresh",
    "_older_than",
    "_coords_of",
    "_key",
    "_unique",
    "Geocoder",
}


def _load() -> dict[str, Any]:
    """Compile the wanted helpers out of geocode.py, with HA stubbed out."""
    tree = ast.parse(io.open(SOURCE, encoding="utf-8").read())
    nodes = [n for n in tree.body if getattr(n, "name", "") in WANTED]
    missing = WANTED - {getattr(n, "name", "") for n in nodes}
    if missing:
        raise SystemExit(f"geocode.py no longer defines: {sorted(missing)}")

    namespace: dict[str, Any] = {
        "_LOGGER": logging.getLogger("test"),
        "re": re,
        "date": date,
        "datetime": datetime,
        "timedelta": timedelta,
        "asyncio": asyncio,
        "Any": Any,
        "Awaitable": Any,
        "Callable": Any,
        "Station": object,
        "HomeAssistant": object,
        "callback": lambda fn: fn,
        "Store": lambda *a, **k: None,
        "aiohttp": type("_m", (), {"ClientSession": object})(),
        "urlencode": None,
        "DAWA_BASE_URL": "",
        "REQUEST_HEADERS": {},
        "STORE_VERSION": 1,
        "STORE_KEY": "test",
        "_TIMEOUT": None,
        "_CONCURRENCY": 4,
        "_MAX_PER_RUN": 500,
        "_RETRY_AFTER": timedelta(days=30),
        "_REFRESH_AFTER": timedelta(days=180),
    }
    # Keep annotations lazy, exactly as the module's own __future__ import does.
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(future + nodes, []), "<geocode>", "exec"), namespace)
    return namespace


NS = _load()
_split = NS["_split"]
_same_house = NS["_same_house"]
_worth_retrying = NS["_worth_retrying"]
_due_refresh = NS["_due_refresh"]
_coords_of = NS["_coords_of"]
_key = NS["_key"]
_unique = NS["_unique"]
Geocoder = NS["Geocoder"]

TODAY = date.today()


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


class FakeStation:
    """Enough of sources.Station for the geocoder."""

    def __init__(self, address: str, postnummer: str = "5700") -> None:
        self.address = address
        self.postnummer = postnummer
        self.latitude: float | None = None
        self.longitude: float | None = None
        self.coord_approx = False


class FakeStore:
    def __init__(self) -> None:
        self.saved: Any = None

    def async_delay_save(self, callback, _delay) -> None:
        self.saved = callback()


def geocoder(cache: dict, lookup=None) -> Any:
    """A Geocoder with its store and network call replaced."""
    instance = Geocoder.__new__(Geocoder)
    instance.hass = None
    instance._store = FakeStore()
    instance._cache = cache
    instance._loaded = True
    instance._task = None
    if lookup is not None:
        instance._async_lookup = lookup
    return instance


# -- address parsing --------------------------------------------------------
def test_split_real_provider_addresses() -> None:
    """Q8/F24 ship '<street> <house> <city> <zip> Danmark' with no separators."""
    cases = [
        ("Dronningemaen 34 Svendborg", "5700", ("Dronningemaen", "34", "Svendborg")),
        # sources.py normally strips the tail, but be robust to the raw form too
        ("Dronningemaen 34 Svendborg 5700 Danmark", "5700",
         ("Dronningemaen", "34", "Svendborg")),
        # motorway station: the city is two words
        ("Holbækmotorvejen 196 Springstrup n", "4300",
         ("Holbækmotorvejen", "196", "Springstrup n")),
        # a house-number range DAWA cannot parse; must still split correctly
        ("Fredensvej 1-3 Charlottenlund", "2920",
         ("Fredensvej", "1-3", "Charlottenlund")),
        ("Nyropsgade 42 København v", "1602", ("Nyropsgade", "42", "København v")),
        # a corner address, slash included
        ("Holmegårdsvej/højengen 1 Kokkedal", "2980",
         ("Holmegårdsvej/højengen", "1", "Kokkedal")),
        # no house number at all -> everything is the street
        ("Motorvejen nord Roskilde", "4000", ("Motorvejen nord Roskilde", "", "")),
        ("", "2000", None),
        ("   ", "2000", None),
    ]
    for address, postnummer, expected in cases:
        assert _split(address, postnummer) == expected, address


def test_same_house_compares_the_danish_way() -> None:
    assert _same_house("1B", "1b")
    assert _same_house("01", "1")
    assert not _same_house("2", "3")
    assert not _same_house(None, "5")


def test_key_and_unique() -> None:
    assert _key(FakeStation("Dronningemaen  34 Svendborg")) == _key(
        FakeStation("dronningemaen 34 SVENDBORG")
    )
    dupes = [
        FakeStation("A 1 By"), FakeStation("A 1 By"),
        FakeStation("B 2 By"), FakeStation(""),
    ]
    assert len(_unique(dupes)) == 2  # one per address, blanks dropped


# -- cache policy -----------------------------------------------------------
def test_refresh_predicates() -> None:
    fresh = {"lat": 1, "lon": 2, "ts": TODAY.isoformat()}
    assert _due_refresh(fresh) is False
    assert _due_refresh({"lat": 1, "lon": 2, "ts": ago(179)}) is False
    assert _due_refresh({"lat": 1, "lon": 2, "ts": ago(180)}) is True
    # No timestamp = written by an older version: re-check once, then it has one.
    assert _due_refresh({"lat": 1, "lon": 2}) is True
    assert _due_refresh({"lat": 1, "lon": 2, "ts": "nonsense"}) is True
    assert _due_refresh(None) is False and _due_refresh({}) is False

    assert _worth_retrying(None) is True
    assert _worth_retrying(fresh) is False
    assert _worth_retrying({"failed": ago(29)}) is False
    assert _worth_retrying({"failed": ago(30)}) is True
    assert _worth_retrying({"failed": "garbage"}) is True

    assert _coords_of({"lat": 55.1, "lon": 10.2, "approx": True}) == (55.1, 10.2, True)
    assert _coords_of({"failed": "x"}) is None
    assert _coords_of(None) is None
    assert _coords_of({"lat": "bad", "lon": 1}) is None


def test_apply_queues_the_right_stations() -> None:
    """A due re-check keeps its coordinates while it waits to be looked up."""
    cache = {
        "fresh 1 by 5700": {"lat": 55.1, "lon": 10.1, "approx": False,
                            "ts": TODAY.isoformat()},
        "stale 2 by 5700": {"lat": 55.2, "lon": 10.2, "approx": False, "ts": ago(200)},
        "undated 3 by 5700": {"lat": 55.3, "lon": 10.3, "approx": True},
        "failedrecent 4 by 5700": {"failed": ago(1)},
        "failedold 5 by 5700": {"failed": ago(90)},
    }
    stations = [
        FakeStation(a) for a in (
            "Fresh 1 By", "Stale 2 By", "Undated 3 By",
            "FailedRecent 4 By", "FailedOld 5 By", "Unknown 6 By",
        )
    ]
    todo = geocoder(dict(cache)).apply(stations)
    assert sorted(s.address.split()[0] for s in todo) == [
        "FailedOld", "Stale", "Undated", "Unknown",
    ]
    by_name = {s.address.split()[0]: s for s in stations}
    assert by_name["Stale"].latitude == 55.2, "a due re-check must not blank the pin"
    assert by_name["Undated"].latitude == 55.3
    assert by_name["Undated"].coord_approx is True
    assert by_name["Fresh"].latitude == 55.1
    assert by_name["FailedRecent"].latitude is None
    assert by_name["Unknown"].latitude is None


def test_failed_recheck_keeps_the_old_position() -> None:
    """DAWA being unreachable must not downgrade a station that was placed."""
    async def fails(_session, _station):
        return None

    cache = {"stale 2 by 5700": {"lat": 55.2, "lon": 10.2, "approx": False,
                                 "ts": ago(200)}}
    changed = asyncio.run(
        geocoder(cache, fails)._async_resolve(None, [FakeStation("Stale 2 By")])
    )
    entry = cache["stale 2 by 5700"]
    assert (entry["lat"], entry["ts"]) == (55.2, TODAY.isoformat())
    assert changed == 0, "an unchanged pass must not trigger a refresh"
    assert _due_refresh(entry) is False, "and it must back off, not hammer DAWA"


def test_recheck_only_refreshes_when_something_changed() -> None:
    async def same(_session, _station):
        return (55.2, 10.2, False)

    async def moved(_session, _station):
        return (56.9, 9.9, False)

    entry = {"lat": 55.2, "lon": 10.2, "approx": False, "ts": ago(200)}
    cache = {"stale 2 by 5700": dict(entry)}
    assert asyncio.run(
        geocoder(cache, same)._async_resolve(None, [FakeStation("Stale 2 By")])
    ) == 0

    cache = {"stale 2 by 5700": dict(entry)}
    assert asyncio.run(
        geocoder(cache, moved)._async_resolve(None, [FakeStation("Stale 2 By")])
    ) == 1
    assert (cache["stale 2 by 5700"]["lat"], cache["stale 2 by 5700"]["lon"]) == (56.9, 9.9)

    # A first-time resolve counts as a change: the map needs the refresh.
    cache = {}
    assert asyncio.run(
        geocoder(cache, moved)._async_resolve(None, [FakeStation("New 9 By")])
    ) == 1
    assert cache["new 9 by 5700"]["ts"] == TODAY.isoformat()


def test_failed_first_attempt_backs_off() -> None:
    async def fails(_session, _station):
        return None

    cache: dict = {}
    asyncio.run(
        geocoder(cache, fails)._async_resolve(None, [FakeStation("Hopeless 9 By")])
    )
    assert cache["hopeless 9 by 5700"] == {"failed": TODAY.isoformat()}
    assert geocoder(cache).apply([FakeStation("Hopeless 9 By")]) == [], "no retry storm"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} geocode tests passed")
    sys.exit(0)

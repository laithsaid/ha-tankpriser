"""Tests for the Go'on source (sources.parse_goon) and its personal key.

Run with: python tests/test_goon.py

Go'on is the only Danish chain behind a credential, and the only one selling 92
octane. Both of those are ways to get a wrong answer rather than no answer:

* a key that is missing or refused must leave the other chains alone, not take
  the refresh down with it;
* 92 octane is cheaper than 95 and plenty of cars must not be filled with it,
  so it is its own fuel and must never arrive under `blyfri95`.

The fixture is captured from the live feed (`tests/fixtures/goon_dk.json`) with
two hand-written stations for shapes it does not contain: a forecourt selling
nothing we model, and one with no postal code. No credential appears in it —
the key is in the request, never the response.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load() -> tuple[types.ModuleType, types.ModuleType]:
    sys.modules.setdefault(
        "aiohttp",
        types.SimpleNamespace(
            ClientSession=object,
            ClientError=Exception,
            ClientTimeout=lambda **kwargs: None,
            ClientResponseError=Exception,
        ),
    )
    if "tp" not in sys.modules:
        package = types.ModuleType("tp")
        package.__path__ = [BASE]
        sys.modules["tp"] = package
    loaded = {}
    for name in ("const", "sources"):
        if f"tp.{name}" in sys.modules:
            loaded[name] = sys.modules[f"tp.{name}"]
            continue
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["const"], loaded["sources"]


const, sources = _load()

PAYLOAD = json.loads(
    io.open(os.path.join(FIXTURES, "goon_dk.json"), encoding="utf-8").read()
)
STATIONS = sources.parse_goon(PAYLOAD)
BY_ID = {s.station_id: s for s in STATIONS}

checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


def test_92_octane_is_not_sold_as_95() -> None:
    """The reason `blyfri92` exists at all.

    92 is a few øre cheaper, so folded into `blyfri95` it would win the
    cheapest-nearby ranking at every Go'on forecourt — and send a car that
    needs 95 to a pump it must not use.
    """
    full = BY_ID["17"]
    check(full.prices["blyfri92"] == 18.16, str(full.prices))
    check(full.prices["blyfri95"] == 18.19, str(full.prices))
    check(full.prices["blyfri92"] < full.prices["blyfri95"], "92 is the cheaper one")


def test_a_station_without_92_simply_has_none() -> None:
    """15 of the 199 do not sell it; an absent fuel is not a zero."""
    check("blyfri92" not in BY_ID["25"].prices, str(BY_ID["25"].prices))
    check(BY_ID["25"].prices["blyfri95"] > 0, str(BY_ID["25"].prices))


def test_coordinates_come_from_the_feed() -> None:
    """Go'on ships exact positions, so nothing here needs geocoding — and
    nothing may be marked approximate, which is what puts a ≈ on the card."""
    for station in STATIONS:
        check(station.latitude is not None, f"{station.name} has no latitude")
        check(not station.coord_approx, f"{station.name} marked approximate")


def test_the_brand_matches_the_chain_it_is_discounted_as() -> None:
    for station in STATIONS:
        key = sources.chain_key(station.company)
        check(key == "goon", f"{station.company!r} -> {key!r}")


def test_stations_we_cannot_use_are_dropped() -> None:
    check("9001" not in BY_ID, "AdBlue-only station should be dropped")
    check("9002" not in BY_ID, "station with no postal code should be dropped")


def test_the_provider_asks_with_a_bearer_token() -> None:
    """A key in the URL ends up in logs and in aiohttp's exception text.

    Go'on takes it as an ordinary Authorization header, so that is where it
    must go — and the query string must stay empty.
    """
    sent: dict = {}

    async def fake_fetch_json(session, url, extra_headers=None, params=None):
        sent["url"] = url
        sent["headers"] = extra_headers or {}
        sent["params"] = params or {}
        return PAYLOAD

    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    try:
        stations = asyncio.run(
            sources.PROVIDERS["goon"].fetch(None, "SECRET-KEY-VALUE", None)
        )
    finally:
        sources._fetch_json = original

    check(
        sent["headers"].get("Authorization") == "Bearer SECRET-KEY-VALUE",
        str(sent["headers"]),
    )
    check(sent["params"] == {}, f"key must not ride in the query string: {sent['params']}")
    check("SECRET-KEY-VALUE" not in sent["url"], sent["url"])
    check(len(stations) == len(STATIONS), f"{len(stations)} stations")


def test_the_provider_is_declared_as_needing_a_key() -> None:
    provider = sources.PROVIDERS["goon"]
    check(provider.needs_credential, "must be declared as needing a credential")
    check(not provider.needs_area, "answers for the whole country at once")
    check(provider.country == const.COUNTRY_DK, provider.country)
    check(provider.signup_url.startswith("https://goon.nu"), provider.signup_url)
    check("30" in provider.guide, "the guide should mention the rate limit")


def test_no_key_means_this_chain_is_skipped_not_an_error() -> None:
    """`fetch_all` must leave the open chains alone when a key is missing.

    Someone who never asks Go'on for a key still has five other chains, and a
    refresh that raised here would take all of them down with it.
    """
    provider = sources.PROVIDERS["goon"]
    check(provider in sources.providers_needing_credential(), "listed as keyed")
    keyed = {p.key for p in sources.providers_needing_credential()}
    check("ok" not in keyed and "circlek" not in keyed, "open chains stay open")


def test_the_new_fuel_reaches_the_pickers() -> None:
    """A fuel nobody can select is a fuel nobody gets."""
    check("blyfri92" in const.FUEL_TYPES, "missing from FUEL_TYPES")
    check("blyfri92" in sources.fuel_types_for(const.COUNTRY_DK), "not offered for DK")
    check(
        "blyfri92" not in sources.fuel_types_for(const.COUNTRY_DE),
        "Germany does not sell it",
    )


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except AssertionError as err:
            failures += 1
            print(f"FAIL {name}: {err}")
        else:
            print(f"ok   {name}")
    print(f"{checks} checks" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

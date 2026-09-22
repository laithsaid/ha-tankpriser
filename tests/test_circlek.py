"""Tests for the Circle K / INGO source (sources.parse_circlek).

Run with: python tests/test_circlek.py

The fixture is captured verbatim from the live feed
(`tests/fixtures/circlek_dk.json`), plus two hand-written sites for shapes the
feed does not contain today — a forecourt selling nothing we model, and one
with no postal code.

Two brands arrive in one list, the catalogue has three codes meaning ordinary
diesel, and the same product is spelled differently at different sites. Each of
those is a way to get a *plausible* wrong price, so each has a test.

`sources.py` imports aiohttp and nothing from Home Assistant, so aiohttp is
stubbed and the module is imported directly — the same trick as
tests/test_germany.py.
"""

from __future__ import annotations

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
    io.open(os.path.join(FIXTURES, "circlek_dk.json"), encoding="utf-8").read()
)
STATIONS = sources.parse_circlek(PAYLOAD)
BY_ID = {s.station_id: s for s in STATIONS}

checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


def test_both_brands_come_out_of_one_feed() -> None:
    """One endpoint, two chains. The brand decides the icon and the discount."""
    check(BY_ID["10103"].company == "Circle K", BY_ID["10103"].company)
    check(BY_ID["10107"].company == "INGO", BY_ID["10107"].company)
    check(
        BY_ID["10103"].name == "Circle K Hasseris Bymidte 2",
        BY_ID["10103"].name,
    )


def test_the_brand_is_one_a_discount_can_be_matched_to() -> None:
    """`chain_key` is what a loyalty discount is applied through.

    Both brands must land on the same chain key, because the feed is one
    company's — and a name that matched nothing would silently be priced at
    list while every other chain honoured the driver's card.
    """
    for station in STATIONS:
        key = sources.chain_key(station.company)
        check(key == "circlek", f"{station.company!r} -> {key!r}")


def test_products_are_mapped_by_code_not_by_name() -> None:
    """"Benzin 95" and "Blyfri 95" are one code with two spellings."""
    ingo = BY_ID["10107"]
    motorway = BY_ID["10354"]
    check(ingo.prices["blyfri95"] == 18.19, str(ingo.prices))
    check(motorway.prices["blyfri95"] == 18.29, str(motorway.prices))


def test_the_premium_95s_are_not_sold_as_ordinary_petrol() -> None:
    """miles+ 95 and UPGRADE 95 cost 60-90 øre more than the everyday pump.

    Folding them into `blyfri95` would put the dearest petrol on the forecourt
    into the cheapest-nearby ranking and send someone there to save money.
    """
    circlek = BY_ID["10103"]
    check(circlek.prices["blyfri95"] == 18.29, str(circlek.prices))
    check(circlek.prices["blyfri95plus"] == 19.19, str(circlek.prices))
    check(BY_ID["10107"].prices["blyfri95plus"] == 18.88, str(BY_ID["10107"].prices))


def test_two_diesel_codes_at_one_site_give_one_price() -> None:
    """The catalogue lists ordinary diesel under three codes.

    A forecourt quoting two of them must not produce two answers, and the one
    that survives must be the lower — a price no pump is charging is the only
    outcome here that could send someone somewhere for nothing.
    """
    circlek = BY_ID["10103"]
    codes = {p["code"] for p in PAYLOAD["sites"][0]["fuelPrices"]}
    check({"1030928", "1030946"} <= codes, "the fixture still has both codes")
    check(circlek.prices["diesel"] == 19.69, str(circlek.prices))
    check(circlek.prices["dieselplus"] == 20.59, str(circlek.prices))


def test_a_site_with_nothing_we_model_is_dropped() -> None:
    """AdBlue is not a fuel this integration ranks, and a station with no
    price at all would show up on the map as an empty pin."""
    check("99001" not in BY_ID, "AdBlue-only site should be dropped")


def test_a_site_with_no_postal_code_is_dropped() -> None:
    """Without one there is nothing to place it by: the feed ships no
    coordinates, so the postnummer is the only geography it has."""
    check("99002" not in BY_ID, "site with no postalCode should be dropped")


def test_every_station_can_be_placed_and_identified() -> None:
    for station in STATIONS:
        check(station.postnummer.isdigit(), f"{station.name}: {station.postnummer!r}")
        check(station.station_id != "", f"{station.name} has no id")
        check(station.latitude is None, f"{station.name} invented coordinates")
        check(station.updated != "", f"{station.name} has no timestamp")


def test_identity_survives_two_forecourts_in_one_postal_code() -> None:
    """Motorway pairs share a brand and a postnummer, so the id is the key."""
    keys = {s.key for s in STATIONS}
    check(len(keys) == len(STATIONS), "two stations collapsed onto one key")
    check(BY_ID["10103"].key == "dk|10103", BY_ID["10103"].key)


def test_the_provider_is_registered_and_open() -> None:
    provider = sources.PROVIDERS["circlek"]
    check(provider.country == const.COUNTRY_DK, provider.country)
    check(not provider.needs_credential, "should need no API key")
    check(not provider.needs_area, "answers for the whole country at once")
    check(
        provider.fuels == frozenset({"blyfri95", "blyfri95plus", "diesel", "dieselplus"}),
        str(sorted(provider.fuels)),
    )


def test_the_required_header_is_sent() -> None:
    """Without `X-App-Name` the API answers 400 "App not allowed".

    It is not a credential and there is nothing to apply for, but a request
    without it fails every time — so it is checked here rather than discovered
    by a user whose Circle K prices never arrive.
    """
    sent: dict = {}

    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        sent["url"] = url
        sent["headers"] = extra_headers or {}
        return PAYLOAD

    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    try:
        import asyncio

        stations = asyncio.run(sources.PROVIDERS["circlek"].fetch(None, None, None))
    finally:
        sources._fetch_json = original

    check(sent["headers"].get("X-App-Name") == "PRICES", str(sent["headers"]))
    check(sent["url"] == const.CIRCLEK_URL, sent["url"])
    check(len(stations) == len(STATIONS), f"{len(stations)} stations")


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

"""Tests for the French source (prix des carburants) and what it replaced.

Run with: python tests/test_france.py

France used to be read from ANWB, a circle at a time, because no ANWB box
covers the country and a box over ~6 degrees answers HTTP 200 with an empty
list. It now has its own national feed — the Ministry of the Economy's
instantaneous export, which every forecourt open to the public must report to —
and that changes three things this file pins down:

* the whole country arrives in **one request**, so France is no longer
  area-scoped and has a national pool to rank against;
* each price carries **its own timestamp**, which ANWB never published;
* the coordinates are in a column called `latitude` that is **not a latitude**
  — 48.183 arrives as ``4818300`` — so the parser reads `geom` instead. Getting
  that wrong puts every French forecourt thousands of degrees north of the pole
  and is exactly the kind of failure that looks like a working integration.

The fixture is captured verbatim from the live export
(`tests/fixtures/prix_carburants_fr.json`).
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
    package = sys.modules.get("tp")
    if package is None:
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
    io.open(os.path.join(FIXTURES, "prix_carburants_fr.json"), encoding="utf-8").read()
)
STATIONS = sources.parse_prix_carburants(PAYLOAD)
BY_ID = {s.station_id: s for s in STATIONS}

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


# --- the parser ------------------------------------------------------------
def test_a_real_record_comes_out_whole() -> None:
    print("parse_prix_carburants on captured records from the live export")
    sens = BY_ID["89100001"]
    check(
        "the street and the town are the name, because that is all France publishes",
        sens.name == "84 ROUTE DE MAILLOT, Sens",
        sens.name,
    )
    check("the postal code is carried", sens.postnummer == "89100", sens.postnummer)
    check("the town is carried", sens.city == "Sens", sens.city)
    check("stations are marked French", sens.country == "fr", sens.country)
    check(
        "every fuel it sells lands on our own keys",
        sens.prices
        == {"blyfri95": 2.259, "blyfri98": 2.319, "diesel": 2.469, "e85": 0.859},
        sens.prices,
    )
    check(
        "and the price carries the day it was reported, which ANWB never did",
        sens.updated == "2026-09-18",
        sens.updated,
    )


def test_the_latitude_column_is_not_a_latitude() -> None:
    print("the column that would have put France above the pole")
    raw = PAYLOAD[0]
    check(
        "the export really does send hundred-thousandths",
        float(raw["latitude"]) > 1000,
        raw["latitude"],
    )
    sens = BY_ID["89100001"]
    check(
        "and the station stands in Burgundy, where Sens is",
        sens.latitude is not None
        and 48.1 < sens.latitude < 48.3
        and 3.2 < sens.longitude < 3.4,
        (sens.latitude, sens.longitude),
    )


def test_the_everyday_95_is_the_e10() -> None:
    print("two 95s on one French forecourt, and which is which")
    station = BY_ID["38230003"]
    check(
        "the E10 is the everyday grade and takes the everyday key",
        station.prices["blyfri95"] == 2.17,
        station.prices,
    )
    check(
        "the older E5 blend beside it is dearer and takes the premium key",
        station.prices["blyfri95plus"] == 2.22,
        station.prices,
    )
    check(
        "so the cheaper of the two is the one a default French entry ranks",
        station.prices["blyfri95"] < station.prices["blyfri95plus"],
        station.prices,
    )


def test_ethanol_is_not_cheap_petrol() -> None:
    print("E85 at a third of the price, and why it is its own fuel")
    station = BY_ID["38230003"]
    check("E85 arrives with its own key", station.prices["e85"] == 0.834, station.prices)
    check(
        "and it really is far cheaper than the petrol it must not be folded into",
        station.prices["e85"] < station.prices["blyfri95"] / 2,
        station.prices,
    )
    check("LPG arrives too", station.prices["lpg"] == 0.998, station.prices)
    check(
        "E85 is sold by the litre, so it is compared like one",
        const.FUEL_QUANTITY.get("e85") is None,
        const.FUEL_QUANTITY.get("e85"),
    )


def test_a_station_reporting_nothing_is_not_a_station() -> None:
    print("the 641 forecourts listed with no price at all")
    check(
        "a record with every price null is dropped",
        "58240003" not in BY_ID,
        sorted(BY_ID),
    )
    check("and the rest still arrive", len(STATIONS) == 2, len(STATIONS))


def test_field_shapes_the_fixture_lacks() -> None:
    print("parse_prix_carburants on the shapes a live response also sends")
    payload = [
        {
            # No geom. Rare, but a station we cannot place can be neither
            # mapped nor measured against a radius.
            "id": 1,
            "cp": "75018",
            "ville": "Paris",
            "adresse": "RUE DE LA CHAPELLE",
            "geom": None,
            "gazole_prix": 1.899,
        },
        {
            # A zero is a reporting gap, not free fuel.
            "id": 2,
            "cp": "69001",
            "ville": "Lyon",
            "adresse": "QUAI SAINT-VINCENT",
            "geom": {"lat": 45.77, "lon": 4.83},
            "gazole_prix": 0,
            "e10_prix": 1.799,
            "e10_maj": "2026-09-20T07:15:00+00:00",
        },
        # Not a record at all.
        "nonsense",
    ]
    stations = sources.parse_prix_carburants(payload)
    kept = {s.station_id: s for s in stations}
    check("a stray non-record is skipped, not fatal", len(stations) == 1, len(stations))
    check("the unplaceable station is dropped", "1" not in kept, sorted(kept))
    check(
        "and a zero is not a price anyone could pay",
        kept["2"].prices == {"blyfri95": 1.799},
        kept["2"].prices,
    )
    check(
        "the timestamp is the day, not the second",
        kept["2"].updated == "2026-09-20",
        kept["2"].updated,
    )


def test_no_brand_is_invented() -> None:
    print("the one thing ANWB did better"),
    check(
        "France publishes no brand, so none is guessed at from the address",
        all(station.company == "" for station in STATIONS),
        [s.company for s in STATIONS],
    )
    check(
        "and nothing downstream mistakes that for a Danish chain",
        sources.chain_key(STATIONS[0].company) is None,
        sources.chain_key(STATIONS[0].company),
    )


# --- the request -----------------------------------------------------------
def test_the_whole_country_is_asked_for_in_one_request() -> None:
    print("one request, and the columns it asks for")
    sent: dict = {}

    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        sent["url"] = url
        sent["params"] = dict(params or {})
        return PAYLOAD

    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    try:
        stations = asyncio.run(
            sources.PROVIDERS["prix_carburants"].fetch(None, None, None)
        )
    finally:
        sources._fetch_json = original

    check("the export endpoint is asked", sent["url"] == const.PRIX_CARBURANTS_URL, sent["url"])
    check(
        "for the columns we read and no others — the rest is 9 MB of opening hours",
        sent["params"] == {"select": const.PRIX_CARBURANTS_FIELDS},
        sent["params"],
    )
    for column in ("geom", "gazole_prix", "e85_prix", "e10_maj"):
        check(f"and {column} is among them", column in const.PRIX_CARBURANTS_FIELDS)
    check("the stations come back parsed", len(stations) == len(STATIONS), len(stations))


# --- the country -----------------------------------------------------------
def test_france_is_national_now() -> None:
    print("what changed for the rest of the integration")
    provider = sources.PROVIDERS["prix_carburants"]
    check("one provider serves France", [p.key for p in sources.providers_for("fr")] == ["prix_carburants"])
    check("it needs no key", not provider.needs_credential)
    check(
        "and no circle, which is the whole point of the move",
        not provider.needs_area and not sources.country_needs_area("fr"),
    )
    check(
        "so it declares no radius ceiling and needs no probe",
        provider.max_radius_km == 0 and provider.probe is None,
        (provider.max_radius_km, provider.probe),
    )
    check(
        "every radius is offered now, where ANWB capped France at 50 km",
        sources.radius_options("fr") == list(const.RADIUS_OPTIONS),
        sources.radius_options("fr"),
    )
    check(
        "and the radius is still measured from an anchor the dialog asks for",
        const.country_needs_anchor("fr"),
    )


def test_the_options_offered_match_the_source() -> None:
    print("the fuels a French dialog may offer")
    fuels = sources.fuel_types_for("fr")
    check(
        "exactly the six the feed publishes",
        set(fuels) == {"blyfri95", "blyfri95plus", "blyfri98", "diesel", "e85", "lpg"},
        sorted(fuels),
    )
    check(
        "no premium diesel and no CNG, because this source prices neither",
        "dieselplus" not in fuels and "cng" not in fuels,
        sorted(fuels),
    )
    check(
        "and both defaults are among them",
        set(sources.default_fuel_types("fr")) <= set(fuels),
        sources.default_fuel_types("fr"),
    )


def test_a_french_pump_is_named_the_french_way() -> None:
    print("the labels a French dialog shows")
    check("diesel is Gazole", const.fuel_label("diesel", "fr") == "Gazole (B7)")
    check("the E10 is SP95-E10", const.fuel_label("blyfri95", "fr") == "SP95-E10")
    check(
        "the E5 blend is named apart from it",
        const.fuel_label("blyfri95plus", "fr") == "SP95 (E5)",
        const.fuel_label("blyfri95plus", "fr"),
    )
    check(
        "and E85 is what a French forecourt calls it",
        const.fuel_label("e85", "fr") == "Superéthanol E85",
        const.fuel_label("e85", "fr"),
    )
    check("France quotes euro to three decimals", const.price_decimals("fr") == 3)


if __name__ == "__main__":
    test_a_real_record_comes_out_whole()
    test_the_latitude_column_is_not_a_latitude()
    test_the_everyday_95_is_the_e10()
    test_ethanol_is_not_cheap_petrol()
    test_a_station_reporting_nothing_is_not_a_station()
    test_field_shapes_the_fixture_lacks()
    test_no_brand_is_invented()
    test_the_whole_country_is_asked_for_in_one_request()
    test_france_is_national_now()
    test_the_options_offered_match_the_source()
    test_a_french_pump_is_named_the_french_way()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all France source tests passed")

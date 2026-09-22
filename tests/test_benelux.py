"""Tests for the Dutch and Belgian source (sources.parse_anwb) and the units.

Run with: python tests/test_benelux.py

ANWB is unlike every other source we read, in three ways that each cost an
assertion here:

* **The box is geography, not a border.** A Dutch box reaches into Belgium and
  Germany, and those stations belong to another source. Keeping them would put
  two prices of different ages on the same forecourt.
* **It quotes prices of zero** — 226 of them in the Netherlands the day this
  was written. A zero is a missing price, not a cheap one, and it wins every
  ranking.
* **It has no timestamps at all.** `updated` stays empty rather than being
  filled with the time we fetched, which would look like a price age.

And CNG is sold by the kilogram, which is why `price_unit` takes a fuel.

The fixture is captured from a live box (`tests/fixtures/anwb_box.json`), one
station of each shape that matters.
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
    io.open(os.path.join(FIXTURES, "anwb_box.json"), encoding="utf-8").read()
)
NL = sources.parse_anwb(PAYLOAD, const.COUNTRY_NL)
BE = sources.parse_anwb(PAYLOAD, const.COUNTRY_BE)
NL_BY_ID = {s.station_id: s for s in NL}

checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


def test_a_box_is_not_a_border() -> None:
    """The same answer holds Dutch, Belgian and German stations."""
    ids = {e["id"] for e in PAYLOAD["value"]}
    check(len(ids) == 5, f"fixture should hold five stations, has {len(ids)}")
    check(len(NL) == 3, f"three Dutch, got {len(NL)}")
    check(len(BE) == 1, f"one Belgian, got {len(BE)}")
    check(
        all(s.country == "nl" for s in NL) and all(s.country == "be" for s in BE),
        "each station is priced under the country that fetched it",
    )
    german = [s for s in NL + BE if "SELGROS" in s.name]
    check(german == [], "the German station belongs to Tankerkönig, not here")


def test_a_price_of_zero_is_a_missing_price() -> None:
    """Total Express quotes Euro 98 at 0,000.

    Kept, it is the cheapest petrol in the country by a mile, every time, for
    ever — and it would send someone to a pump that is not selling.
    """
    raw = next(e for e in PAYLOAD["value"] if e["id"] == "appitup_416")
    check(
        any(p["value"] == 0 for p in raw["prices"]),
        "the fixture still contains the zero price",
    )
    station = NL_BY_ID["appitup_416"]
    check("blyfri98" not in station.prices, str(station.prices))
    check(station.prices["blyfri95"] == 2.449, str(station.prices))


def test_nothing_pretends_to_know_when_a_price_changed() -> None:
    """No timestamp is honest. The fetch time dressed as one is not."""
    for station in NL + BE:
        check(station.updated == "", f"{station.name}: {station.updated!r}")


def test_lpg_and_cng_both_arrive() -> None:
    check(NL_BY_ID["appitup_430"].prices["lpg"] == 0.959, "LPG")
    check(NL_BY_ID["appitup_753"].prices["cng"] == 1.699, "CNG")


def test_cng_is_priced_by_the_kilogram() -> None:
    """The whole reason `price_unit` takes a fuel.

    1,70 €/kg against 2,42 €/L is not a cheaper forecourt, it is a different
    question — and anything that merely compares two numbers would call the
    compressed gas the best price on the map.
    """
    check(const.price_unit("nl") == "€/L", const.price_unit("nl"))
    check(const.price_unit("nl", "cng") == "€/kg", const.price_unit("nl", "cng"))
    check(const.price_unit("nl", "lpg") == "€/L", const.price_unit("nl", "lpg"))
    check(const.price_unit("dk", "cng") == "kr./kg", const.price_unit("dk", "cng"))
    check(const.price_unit("dk") == "kr./L", const.price_unit("dk"))


def test_both_countries_speak_euro_to_three_decimals() -> None:
    for country in ("nl", "be"):
        check(const.price_decimals(country) == 3, country)
        check(const.spoken_currency(country) == "euro", country)
        check(const.minor_unit(country) == "cent", country)


def test_a_dutch_pump_is_named_the_dutch_way() -> None:
    check(const.fuel_label("blyfri95", "nl") == "Euro 95 (E10)", "Euro 95")
    check(const.fuel_label("blyfri98", "be") == "Super 98 (E5)", "Super 98")
    check(const.fuel_label("blyfri95", "dk") == "Blyfri 95 (E10)", "still Danish at home")


def test_the_boxes_hold_the_countries_they_claim() -> None:
    """A position inside the country must pick that country's box."""
    amsterdam = (52.3676, 4.9041)
    brussels = (50.8503, 4.3517)
    check(const.country_of("nl").contains(*amsterdam), "Amsterdam is in the NL box")
    check(const.country_of("be").contains(*brussels), "Brussels is in the BE box")
    check(not const.country_of("be").contains(*amsterdam), "and not in Belgium's")


def test_a_dutch_tamoil_is_not_a_danish_oil() -> None:
    """The Danish OIL! pattern used to be a bare "oil".

    The Netherlands and Belgium are full of Tamoil and Lukoil — 359 of them in
    one box — and every one matched, which drew the Danish OIL! mark on them
    and would have handed them an OIL! loyalty discount if they were Danish.
    """
    for name in ("Tamoil", "LUKOIL", "Lukoil Venlo", "UNOIL"):
        check(sources.chain_key(name) is None, f"{name} -> {sources.chain_key(name)}")
    for name in ("OIL!", "OIL! Silkeborg"):
        check(sources.chain_key(name) == "oil", f"{name} -> {sources.chain_key(name)}")
    # The same two letters, the same trap: "ok" inside another word.
    check(sources.chain_key("Bokma") is None, "Bokma is not OK")
    check(sources.chain_key("OK Plus Silkeborg") == "ok", "but OK Plus is")


def test_stations_are_placed_and_identified() -> None:
    for station in NL + BE:
        check(station.latitude is not None, f"{station.name} has no position")
        check(not station.coord_approx, f"{station.name} marked approximate")
        check(station.station_id != "", f"{station.name} has no id")
        check(station.company != "", f"{station.name} has no company")


def test_the_providers_are_registered_open_and_national() -> None:
    for key, country in (("anwb_nl", "nl"), ("anwb_be", "be")):
        provider = sources.PROVIDERS[key]
        check(provider.country == country, f"{key}: {provider.country}")
        check(not provider.needs_credential, f"{key} should need no key")
        check(not provider.needs_area, f"{key} answers for a whole box at once")
    check(
        "lpg" in sources.fuel_types_for("nl") and "cng" in sources.fuel_types_for("nl"),
        str(sources.fuel_types_for("nl")),
    )
    check(
        "lpg" not in sources.fuel_types_for("dk"),
        "no Danish source prices LPG, so it must not be offered there",
    )


def test_each_country_asks_for_its_own_box() -> None:
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
        stations = asyncio.run(sources.PROVIDERS["anwb_be"].fetch(None, None, None))
    finally:
        sources._fetch_json = original

    check(sent["url"] == const.ANWB_URL, sent["url"])
    check(sent["params"].get("type-filter") == "FUEL_STATION", str(sent["params"]))
    box = sent["params"].get("bounding-box-filter", "")
    check(box.startswith("49.45,2.5"), f"Belgium's box, not somebody else's: {box}")
    check(len(stations) == 1, f"and only Belgian stations come back: {len(stations)}")


def test_luxembourg_is_registered_and_france_has_left() -> None:
    """The hole this closed: a pin near southern Belgium found nothing.

    Luxembourg sits wholly inside both the Belgian and the German box, and
    northern France inside the Belgian one. Both were therefore *answered* —
    confidently, by a country whose stations the parser had already filtered
    out — and both came back "no stations within 25 kilometres" with hundreds
    of forecourts under the pin. ANWB had been returning them all along: one
    Belgian box carried 480 French and 211 Luxembourgish stations that were
    parsed and thrown away.

    France has since moved to its own national source (see tests/test_france.py)
    and must not be served from here again: ANWB can only answer about a box,
    no box covers France, and asking for one silently returns nothing.
    """
    lux = sources.PROVIDERS["anwb_lu"]
    check(lux.country == "lu", f"anwb_lu: {lux.country}")
    check(not lux.needs_credential, "Luxembourg should need no key")
    check(not lux.needs_area, "Luxembourg fits one box, so it is asked for whole")

    check("anwb_fr" not in sources.PROVIDERS, "France should no longer be an ANWB box")
    french = [p.key for p in sources.providers_for("fr")]
    check(french == ["prix_carburants"], f"France is priced nationally now: {french}")
    check(
        const.COUNTRY_FR not in const.ANWB_ISO3
        and const.COUNTRY_FR not in const.ANWB_BOXES,
        "and no French geography is left behind in the ANWB tables",
    )
    check(
        not sources.country_needs_area("fr") and not sources.country_needs_area("lu"),
        "neither country is asked about a circle any more",
    )


def test_no_country_box_can_exceed_the_silent_limit() -> None:
    """ANWB answers an over-large box with 200 and an EMPTY LIST.

    No error, no status, nothing to catch — measured 2026-09-17 by growing a
    box around Paris: 6.0 x 6.0 returned 8,550 stations and 7.0 x 7.0 returned
    none. A country whose box crept over that line would look exactly like a
    country with no fuel in it, which is the failure this whole change is
    about. So the boxes are checked here rather than discovered in the field.
    """
    for country, box in const.ANWB_BOXES.items():
        lat_min, lon_min, lat_max, lon_max = box
        check(
            lat_max - lat_min <= const.ANWB_MAX_BOX_DEG,
            f"{country} box is {lat_max - lat_min:.1f} deg tall",
        )
        check(
            lon_max - lon_min <= const.ANWB_MAX_BOX_DEG,
            f"{country} box is {lon_max - lon_min:.1f} deg wide",
        )
        check(lat_min < lat_max and lon_min < lon_max, f"{country} box is inside out")


def test_the_new_boxes_hold_their_own_countries() -> None:
    lu, fr, be, de = (const.country_of(c) for c in ("lu", "fr", "be", "de"))
    luxembourg_city = (49.6117, 6.1319)
    check(lu.contains(*luxembourg_city), "Luxembourg City is in the Luxembourg box")
    # Exactly why it used to be answered by Germany: it is in their boxes too,
    # and Germany's anchor was the nearer one.
    check(be.contains(*luxembourg_city), "and in Belgium's, which is the trap")
    check(de.contains(*luxembourg_city), "and in Germany's, which is what answered")
    for name, position in (
        ("Paris", (48.8566, 2.3522)),
        ("Lyon", (45.7640, 4.8357)),
        ("Marseille", (43.2965, 5.3698)),
        ("Charleville", (49.7717, 4.7197)),
    ):
        check(fr.contains(*position), f"{name} is in the France box")
    check(not fr.contains(56.1806, 9.5107), "and Silkeborg is not")


def test_a_french_pump_is_named_the_french_way() -> None:
    check(const.fuel_label("diesel", "fr") == "Gazole (B7)", "Gazole")
    check(const.fuel_label("blyfri95", "fr") == "SP95-E10", "SP95")
    check(const.fuel_label("lpg", "fr") == "GPL", "GPL")
    check(const.fuel_label("diesel", "lu") == "Diesel (B7)", "Luxembourg keeps B7")
    for country in ("lu", "fr"):
        check(const.price_decimals(country) == 3, f"{country} decimals")
        check(const.spoken_currency(country) == "euro", f"{country} currency")


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

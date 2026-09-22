"""Tests for the Italian source (Osservaprezzi carburanti).

Run with: python tests/test_italy.py

Italy is the only source that is not JSON and the only one that takes two
requests to answer: the ministry publishes the prices and the forecourts as
separate pipe-separated files, joined on its own id. Nearly everything that
can go wrong with that pair is silent, so each one has a test here:

* **A real CSV reader is the wrong tool.** One registry row opens a double
  quote and never closes it, and `csv.reader` then swallows the next thirty-odd
  forecourts into a single field.
* **113 rows carry an extra field** — an address typed into the name with a
  pipe in it — which shifts every column after it. Read by position, those
  forecourts get a province code (`AL`) for a latitude and are lost.
* **Two prices per fuel**, self-service and served. Taking the wrong one quotes
  a price 20 cents above what most drivers pay.
* **Free-text fuel names**, 59 of them for eight products, spelled differently
  at different forecourts.
* **Placeholder prices.** 0,100 is not the cheapest petrol in Italy.

The fixtures are captured verbatim from the live files
(`tests/fixtures/mimit_*_it.csv`), picked for exactly those shapes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
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


def _fixture(name: str) -> str:
    return io.open(os.path.join(FIXTURES, name), encoding="utf-8").read()


REGISTRY = _fixture("mimit_stations_it.csv")
PRICES = _fixture("mimit_prices_it.csv")
STATIONS = sources.parse_mimit(REGISTRY, PRICES)
BY_ID = {s.station_id: s for s in STATIONS}

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


# --- the parser ------------------------------------------------------------
def test_a_real_forecourt_comes_out_whole() -> None:
    print("parse_mimit on captured rows from the live files")
    eni = BY_ID["63056"]
    check("the sign over the forecourt is the brand", eni.company == "Agip Eni", eni.company)
    check(
        "and the name locates it",
        eni.name == "Agip Eni LEONARDO SCIASCIA SN 92100",
        eni.name,
    )
    check("the town is carried", eni.city == "AGRIGENTO", eni.city)
    check("stations are marked Italian", eni.country == "it", eni.country)
    check("the ministry's own id is the identity", eni.station_id == "63056", eni.station_id)
    check(
        "it stands in Sicily, where Agrigento is",
        eni.latitude is not None and 37.2 < eni.latitude < 37.3 and 13.5 < eni.longitude < 13.7,
        (eni.latitude, eni.longitude),
    )
    check(
        "every fuel it sells lands on our own keys",
        eni.prices
        == {"blyfri95": 2.179, "diesel": 2.349, "dieselplus": 2.449, "hvo100": 2.349},
        eni.prices,
    )
    check(
        "and the date the operator last communicated a price is kept",
        eni.updated == "2026-09-17",
        eni.updated,
    )
    check(
        "Italy publishes no postal code, so none is invented",
        eni.postnummer == "",
        eni.postnummer,
    )


def test_the_self_service_price_is_the_one_quoted() -> None:
    """Self and served are two rows for one pump, 22 cents apart here."""
    print("two prices for one fuel, and which one a driver pays")
    eni = BY_ID["63056"]
    check(
        "the file really does carry both",
        "63056|Benzina|2.399|0|" in PRICES and "63056|Benzina|2.179|1|" in PRICES,
        "the fixture should hold the pair",
    )
    check("and the lower of the two is kept", eni.prices["blyfri95"] == 2.179, eni.prices)

    served_only = BY_ID["53137"]
    check(
        "a forecourt with no self-service still gets its served price",
        served_only.prices == {"blyfri95": 2.169, "diesel": 2.369},
        served_only.prices,
    )


def test_a_row_with_an_extra_field_is_still_placed() -> None:
    """113 registry rows have an address typed into the name, pipe and all."""
    print("the extra pipe that shifts every column after it")
    shifted = BY_ID["40820"]
    check(
        "the fixture still carries the broken row",
        "|STOIL SIMPLE | gestori.prezzibenzina.it|" in REGISTRY,
        "the extra field should be in the fixture",
    )
    check(
        "the coordinates are the coordinates, not a province code",
        shifted.latitude is not None and 44.8 < shifted.latitude < 45.0,
        shifted.latitude,
    )
    check("the town is the town", shifted.city == "ALESSANDRIA", shifted.city)
    check(
        "and the street is the street, not the junk in the name",
        shifted.address == "STR. PROV.LE 82 SPINETTA SALE 15122",
        shifted.address,
    )


def test_an_unbalanced_quote_does_not_eat_the_rest_of_the_file() -> None:
    """One registry row opens a double quote and never closes it."""
    print("the quote a CSV reader would never recover from")
    check(
        "the fixture still carries the unbalanced quote",
        REGISTRY.count('"') == 1,
        REGISTRY.count('"'),
    )
    quoted = BY_ID.get("46593")
    check("the forecourt on that row survives", quoted is not None, sorted(BY_ID))
    if quoted:
        check("with its own brand", quoted.company == "Q8", quoted.company)
        check(
            "and its own prices",
            quoted.prices == {"blyfri95": 2.204, "diesel": 2.399, "hvo100": 2.364},
            quoted.prices,
        )
    check(
        "and the forecourts listed after it are all still there",
        {"52970", "55136", "39620", "54776", "53137"} <= set(BY_ID),
        sorted(BY_ID),
    )


def test_a_placeholder_is_not_a_price() -> None:
    """0,100 for a 100-octane petrol would win every ranking in Italy."""
    print("the prices operators use to mean 'none'")
    placeholder = BY_ID["55136"]
    check(
        "the fixture still carries the 0,100",
        "55136|Blue Super|0.100|" in PRICES,
        "the placeholder should be in the fixture",
    )
    check(
        "it is dropped rather than sold as the cheapest fuel in the country",
        "oktan100" not in placeholder.prices,
        placeholder.prices,
    )
    check(
        "while the real prices at the same forecourt are kept",
        placeholder.prices == {"blyfri95": 2.134, "diesel": 2.319, "dieselplus": 2.419},
        placeholder.prices,
    )
    check(
        "and the band is wide enough to keep the cheapest real fuel Italy sells",
        const.MIMIT_MIN_PRICE < 0.549,
        const.MIMIT_MIN_PRICE,
    )


def test_winter_diesel_is_diesel() -> None:
    """Arctic grade costs the same as ordinary where both are sold, and at
    seven forecourts it is the only diesel there is."""
    print("Gasolio Artico, in the mountains")
    alpine = BY_ID["52970"]
    check(
        "the fixture still carries it",
        "52970|Gasolio Artico|2.299|" in PRICES,
        "the arctic row should be in the fixture",
    )
    check(
        "it is read as diesel rather than as a premium grade",
        alpine.prices == {"blyfri95": 2.119, "diesel": 2.299},
        alpine.prices,
    )
    check(
        "so the forecourt has a diesel price at all",
        "diesel" in alpine.prices and "dieselplus" not in alpine.prices,
        alpine.prices,
    )


def test_the_gases_are_told_apart() -> None:
    print("Metano, L-GNC and GNL")
    unbranded = BY_ID["39620"]
    check("Metano is CNG", unbranded.prices.get("cng") == 1.858, unbranded.prices)
    check("GPL is LPG", unbranded.prices.get("lpg") == 0.814, unbranded.prices)

    gnl = BY_ID["54776"]
    check(
        "L-GNC is the same compressed gas at the same pump",
        gnl.prices.get("cng") == 1.959,
        gnl.prices,
    )
    check(
        "but GNL is liquefied, which nothing here models, so it is left out",
        "54776|GNL|1.909|" in PRICES and 1.909 not in gnl.prices.values(),
        gnl.prices,
    )
    check(
        "CNG keeps its own denominator rather than being read as a litre",
        const.FUEL_QUANTITY.get("cng", "litres") != "litres",
        const.FUEL_QUANTITY.get("cng"),
    )


def test_an_unbranded_forecourt_is_named_as_one() -> None:
    print("Pompe Bianche — what Italy calls a station with no sign")
    unbranded = BY_ID["39620"]
    check("the brand is the sign it flies", unbranded.company == "Pompe Bianche", unbranded.company)
    check(
        "and the name still says where it is",
        unbranded.name == "Pompe Bianche VIA CIRCONVALLAZIONE 15 15011",
        unbranded.name,
    )


def test_a_forecourt_in_one_file_only_is_dropped() -> None:
    print("the registry lists forecourts that have never reported a price")
    check(
        "the fixture still carries one",
        "\n7842|" in REGISTRY and "\n7842|" not in PRICES,
        "the priceless forecourt should be in the fixture",
    )
    check("and it is not offered as a station", "7842" not in BY_ID, sorted(BY_ID))
    check("the rest arrive", len(STATIONS) == 8, len(STATIONS))


def test_field_shapes_the_fixtures_lack() -> None:
    print("parse_mimit on the shapes the live files also send")
    registry = "\n".join(
        [
            "Estrazione del 2026-09-21",
            "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine",
            # No coordinates at all: three rows in the live file are like this.
            "1|GESTORE|Q8|Stradale|Q8 UNO|VIA UNO 1|MILANO|MI||",
            "2|GESTORE|Esso|Stradale|ESSO DUE|VIA DUE 2|TORINO|TO|45.07|7.69",
            # Truncated row.
            "3|GESTORE|Shell",
        ]
    )
    prices = "\n".join(
        [
            "Estrazione del 2026-09-21",
            "idImpianto|descCarburante|prezzo|isSelf|dtComu",
            "1|Benzina|2.109|1|20/09/2026 07:00:00",
            "2|Benzina|2.129|1|20/09/2026 07:00:00",
            # A fuel nothing models, and a price that is not a number.
            "2|F101|2.379|1|20/09/2026 07:00:00",
            "2|Gasolio|nonsense|1|20/09/2026 07:00:00",
            # A price for a forecourt the registry has never heard of.
            "99|Benzina|1.999|1|20/09/2026 07:00:00",
            "2|Gasolio|2.289|1|21/09/2026 06:30:00",
        ]
    )
    stations = {s.station_id: s for s in sources.parse_mimit(registry, prices)}
    check("an unplaceable forecourt is dropped", "1" not in stations, sorted(stations))
    check("a truncated row is skipped, not fatal", "3" not in stations, sorted(stations))
    check("a price with no forecourt is dropped", "99" not in stations, sorted(stations))
    check(
        "a fuel nothing models and a price that is not a number are both left out",
        stations["2"].prices == {"blyfri95": 2.129, "diesel": 2.289},
        stations["2"].prices,
    )
    check(
        "and the newest communication is the one shown",
        stations["2"].updated == "2026-09-21",
        stations["2"].updated,
    )


# --- the request -----------------------------------------------------------
def test_two_files_are_asked_for_with_room_to_answer() -> None:
    print("the two requests one Italian refresh makes")
    calls: list[tuple] = []

    async def fake_fetch_text(session, url, params=None, timeout_s=None):
        calls.append((url, timeout_s))
        return REGISTRY if url == const.MIMIT_STATIONS_URL else PRICES

    original = sources._fetch_text
    sources._fetch_text = fake_fetch_text
    try:
        stations = asyncio.run(sources.PROVIDERS["mimit"].fetch(None, None, None))
    finally:
        sources._fetch_text = original

    check("two requests, and no more", len(calls) == 2, len(calls))
    check(
        "the registry and the prices, in that order",
        [url for url, _ in calls] == [const.MIMIT_STATIONS_URL, const.MIMIT_PRICES_URL],
        calls,
    )
    check(
        "both with longer than the shared 30 seconds, which 7.5 MB does not fit into",
        all(timeout == const.MIMIT_TIMEOUT_S for _, timeout in calls)
        and const.MIMIT_TIMEOUT_S > 30,
        calls,
    )
    check("the stations come back joined", len(stations) == len(STATIONS), len(stations))


# --- the country -----------------------------------------------------------
def test_italy_is_wired_in_as_data() -> None:
    print("what the rest of the integration learns about Italy")
    check("the country exists", "it" in const.COUNTRIES, sorted(const.COUNTRIES))

    italy = const.COUNTRIES["it"]
    check("it quotes euro", italy.unit == "€/L" and italy.spoken_currency == "euro", italy.unit)
    check("to three decimals, like its neighbours", italy.decimals == 3, italy.decimals)
    check("and a gap is cents", italy.minor_unit == "cent", italy.minor_unit)
    check("it has a Danish name to be spoken", italy.name_da == "Italien", italy.name_da)
    check("Rome is inside its box", italy.contains(41.9028, 12.4964))
    check("so is Palermo", italy.contains(38.1157, 13.3615))
    check("so is Cagliari", italy.contains(39.2238, 9.1217))
    check("and so is Bolzano", italy.contains(46.4983, 11.3548))
    check("Munich is not", not italy.contains(48.1372, 11.5756))
    check("nor Barcelona", not italy.contains(41.3874, 2.1686))


def test_the_options_offered_match_the_source() -> None:
    print("the fuels and radii the dialog may offer for Italy")
    fuels = sources.fuel_types_for("it")
    check(
        "exactly the eight products we read from the register",
        set(fuels)
        == {
            "blyfri95",
            "blyfri98",
            "oktan100",
            "diesel",
            "dieselplus",
            "hvo100",
            "lpg",
            "cng",
        },
        sorted(fuels),
    )
    check(
        "no E85 and no 92 octane, because Italy sells neither",
        "e85" not in fuels and "blyfri92" not in fuels,
        sorted(fuels),
    )
    check(
        "Italy is not asked about a circle: two files hold the country",
        not sources.country_needs_area("it"),
    )
    check(
        "so every radius is offered",
        sources.radius_options("it") == list(const.RADIUS_OPTIONS),
        sources.radius_options("it"),
    )
    check(
        "and it still needs an anchor, because that is what the radius is measured from",
        const.country_needs_anchor("it"),
    )


def test_an_italian_pump_is_named_the_italian_way() -> None:
    print("the labels an Italian dialog shows")
    check("petrol is Benzina", const.fuel_label("blyfri95", "it") == "Benzina")
    check("diesel is Gasolio", const.fuel_label("diesel", "it") == "Gasolio")
    check(
        "the premium petrols say their octane",
        const.fuel_label("oktan100", "it") == "Benzina 100 ottani"
        and const.fuel_label("blyfri98", "it") == "Benzina 98 ottani",
        const.fuel_label("oktan100", "it"),
    )
    check("CNG is Metano", const.fuel_label("cng", "it") == "Metano")
    check(
        "while Denmark is untouched by any of it",
        const.fuel_label("diesel", "dk") == "Diesel (B7)",
    )


if __name__ == "__main__":
    test_a_real_forecourt_comes_out_whole()
    test_the_self_service_price_is_the_one_quoted()
    test_a_row_with_an_extra_field_is_still_placed()
    test_an_unbalanced_quote_does_not_eat_the_rest_of_the_file()
    test_a_placeholder_is_not_a_price()
    test_winter_diesel_is_diesel()
    test_the_gases_are_told_apart()
    test_an_unbranded_forecourt_is_named_as_one()
    test_a_forecourt_in_one_file_only_is_dropped()
    test_field_shapes_the_fixtures_lack()
    test_two_files_are_asked_for_with_room_to_answer()
    test_italy_is_wired_in_as_data()
    test_the_options_offered_match_the_source()
    test_an_italian_pump_is_named_the_italian_way()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all Italy source tests passed")

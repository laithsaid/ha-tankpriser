"""Tests for the Spanish source (MITECO) and the shape of its answers.

Run with: python tests/test_spain.py

Spain is the widest answer we ask anyone for — 11,500 forecourts in one
response — and almost everything that could go wrong with it is a *quiet*
wrong answer rather than an error:

* every number is a string with a **decimal comma**, coordinates included, so
  a parser that trusted `float()` would place the whole country nowhere;
* Spain publishes more products than anyone, and two of them are traps:
  **Gasoleo B** is agricultural red diesel it is an offence to burn on the
  road, and it is usually the cheapest thing on the forecourt;
* the everyday Spanish petrol is the **E5** blend, not the E10. Reading "95 E5"
  as a premium grade the way Germany's E5 is premium would leave a Spanish
  entry's default fuel priced at the 28 forecourts that sell E10.

The fixture is captured verbatim from the live register
(`tests/fixtures/miteco_es.json`), picked for exactly those shapes.

`sources.py` imports aiohttp and nothing from Home Assistant, so the stub here
is enough to run the whole file with nothing installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import ssl
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SOURCE_FILE = os.path.join(BASE, "sources.py")


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
    io.open(os.path.join(FIXTURES, "miteco_es.json"), encoding="utf-8").read()
)
STATIONS = sources.parse_miteco(PAYLOAD)
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
    print("parse_miteco on captured records from the live register")
    repsol = BY_ID["5116"]
    check("the sign over the forecourt is the brand", repsol.company == "REPSOL", repsol.company)
    check(
        "and the name locates it",
        repsol.name == "REPSOL CR N-322, 337,8",
        repsol.name,
    )
    check("the postal code is carried", repsol.postnummer == "02006", repsol.postnummer)
    check("the town is carried", repsol.city == "Albacete", repsol.city)
    check("stations are marked Spanish", repsol.country == "es", repsol.country)
    check(
        "the register's own id is kept, because two forecourts share a postal code",
        repsol.station_id == "5116",
        repsol.station_id,
    )
    check(
        "every fuel it sells lands on our own keys",
        repsol.prices
        == {
            "blyfri95": 2.019,
            "blyfri98": 2.179,
            "diesel": 2.019,
            "dieselplus": 2.089,
        },
        repsol.prices,
    )
    check(
        "MITECO timestamps the extract and not the price, so none is invented",
        repsol.updated == "",
        repsol.updated,
    )


def test_a_decimal_comma_is_a_decimal_point() -> None:
    print("the commas that would have placed Spain nowhere")
    repsol = BY_ID["5116"]
    raw = PAYLOAD["ListaEESSPrecio"][0]
    check(
        "the register really does send commas",
        "," in raw["Latitud"] and "," in raw["Precio Gasoleo A"],
        (raw["Latitud"], raw["Precio Gasoleo A"]),
    )
    check(
        "the station is in Albacete, not off the map",
        repsol.latitude is not None
        and 38.9 < repsol.latitude < 39.0
        and -2.1 < repsol.longitude < -2.0,
        (repsol.latitude, repsol.longitude),
    )
    check("and the price is a number", repsol.prices["diesel"] == 2.019, repsol.prices)


def test_red_diesel_is_not_diesel() -> None:
    print("the cheapest price on a Spanish forecourt, and the one you may not buy")
    check(
        "a station selling only Gasoleo B is dropped entirely",
        "11072" not in BY_ID,
        sorted(BY_ID),
    )
    unbranded = BY_ID["4375"]
    check(
        "and where it is sold beside road diesel it is still not read",
        unbranded.prices == {"blyfri95": 1.849, "diesel": 1.879},
        unbranded.prices,
    )
    check(
        "so the road diesel stays dearer than the agricultural one it is sold with",
        unbranded.prices["diesel"] > 1.599,
        unbranded.prices,
    )


def test_the_everyday_petrol_is_the_everyday_key() -> None:
    print("Spain's 95 is the E5 blend, and it is not a premium grade")
    repsol = BY_ID["5245"]
    check(
        "the ordinary 95 E5 is the ordinary key",
        repsol.prices["blyfri95"] == 2.039,
        repsol.prices,
    )
    check(
        "and the Premium 95 sold beside it is the one with the premium key",
        repsol.prices["blyfri95plus"] == 2.089,
        repsol.prices,
    )
    check(
        "so the default fuels a Spanish entry starts with are priced everywhere",
        sources.default_fuel_types("es") == ["blyfri95", "diesel"],
        sources.default_fuel_types("es"),
    )


def test_the_products_we_do_not_model_stay_out() -> None:
    print("AdBlue, LNG and the rest")
    check(
        "AdBlue is not a fuel and is not priced as one",
        all("adblue" not in s.prices for s in STATIONS),
        [s.prices for s in STATIONS],
    )
    gnv = BY_ID["14612"]
    check(
        "CNG arrives, because a kilogram of gas is a real price",
        gnv.prices == {"cng": 1.890},
        gnv.prices,
    )
    check(
        "and the LNG sold at the same site does not, since nothing models it",
        "1,737" in json.dumps(PAYLOAD, ensure_ascii=False),
        "the fixture should still contain the LNG price it ignores",
    )
    check(
        "CNG keeps its own denominator rather than being read as a litre",
        const.FUEL_QUANTITY.get("cng", "litres") != "litres",
        const.FUEL_QUANTITY.get("cng"),
    )


def test_field_shapes_the_fixture_lacks() -> None:
    print("parse_miteco on the shapes a live response also sends")
    payload = {
        "ListaEESSPrecio": [
            {
                # A price of zero is a reporting gap, not free fuel.
                "IDEESS": "1",
                "Rótulo": "CEPSA",
                "Dirección": "AV DEL PUERTO, 1",
                "Municipio": "Valencia",
                "C.P.": "46022",
                "Latitud": "39,459000",
                "Longitud (WGS84)": "-0,330000",
                "Precio Gasoleo A": "0",
                "Precio Gasolina 95 E5": "1,759",
            },
            {
                # No coordinates at all. Spain places every station it lists,
                # but a station we cannot place can be neither mapped nor
                # measured against a radius.
                "IDEESS": "2",
                "Rótulo": "BP",
                "Dirección": "CL MAYOR, 2",
                "Municipio": "Toledo",
                "C.P.": "45001",
                "Latitud": "",
                "Longitud (WGS84)": "",
                "Precio Gasoleo A": "1,899",
            },
            # Not a record at all: one stray value must not take the country down.
            "nonsense",
        ]
    }
    stations = sources.parse_miteco(payload)
    kept = {s.station_id: s for s in stations}
    check("a stray non-record is skipped, not fatal", len(stations) == 1, len(stations))
    check("the unplaceable station is dropped", "2" not in kept, sorted(kept))
    check(
        "and a zero is not a price anyone could pay",
        kept["1"].prices == {"blyfri95": 1.759},
        kept["1"].prices,
    )


def test_an_unbranded_forecourt_still_has_a_name() -> None:
    print("the forecourt that signs itself with its licence number")
    unbranded = BY_ID["4375"]
    check("its own sign is its company", unbranded.company.startswith("Nº"), unbranded.company)
    check("and the name still says where it is", "AVENIDA" in unbranded.name.upper(), unbranded.name)


# --- the request -----------------------------------------------------------
def test_the_whole_country_is_asked_for_once_with_room_to_answer() -> None:
    print("one request, and a timeout sized for 12 MB that is not gzipped")
    sent: dict = {}

    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        sent["url"] = url
        sent["params"] = dict(params or {})
        sent["timeout_s"] = timeout_s
        sent["ssl_context"] = ssl_context
        return PAYLOAD

    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    try:
        stations = asyncio.run(sources.PROVIDERS["miteco"].fetch(None, None, None))
    finally:
        sources._fetch_json = original

    check("the register's own endpoint is asked", sent["url"] == const.MITECO_URL, sent["url"])
    check("with nothing to filter by, because it answers for everywhere", sent["params"] == {}, sent["params"])
    check(
        "and longer than the shared 30 seconds, which 12 MB does not fit into",
        sent["timeout_s"] == const.MITECO_TIMEOUT_S and const.MITECO_TIMEOUT_S > 30,
        sent["timeout_s"],
    )
    check("the stations come back parsed", len(stations) == len(STATIONS), len(stations))
    check(
        "and it carries its own TLS cipher list, which this host insists on",
        sent["ssl_context"] is not None,
        sent["ssl_context"],
    )


def test_the_spanish_host_needs_its_own_cipher_list() -> None:
    """The register began resetting OpenSSL's default ClientHello mid-handshake
    on 2026-09-22 — no status, nothing to retry into. Measured 12 attempts per
    configuration: the default failed 12/12, this list succeeded 12/12.

    What matters as much as the list is what it does NOT change: the
    certificate is still verified and the hostname still checked, so this buys
    a connection and gives up nothing about who we are willing to talk to.
    """
    print("the one source that cannot use the default TLS settings")
    context = sources._MITECO_SSL
    check("a context was built", context is not None, context)
    if context is None:
        return
    check(
        "certificates are still verified",
        context.verify_mode == ssl.CERT_REQUIRED,
        context.verify_mode,
    )
    check("and the hostname still checked", context.check_hostname is True, context.check_hostname)
    check(
        "nothing anonymous or unencrypted is on offer",
        all(
            "aNULL" not in cipher["description"] and "eNULL" not in cipher["description"]
            for cipher in context.get_ciphers()
        ),
        "a null cipher slipped into the list",
    )
    check(
        "the list is the documented one",
        const.MITECO_CIPHERS == "HIGH:!aNULL:!eNULL",
        const.MITECO_CIPHERS,
    )
    check(
        "and it is Spain's alone: no other source carries one",
        sum(1 for line in io.open(SOURCE_FILE, encoding="utf-8") if "ssl_context=_" in line) == 1,
        "another provider has quietly taken a cipher list",
    )


# --- the area ---------------------------------------------------------------
def test_a_country_is_cut_down_by_distance_not_by_postnummer() -> None:
    """The cut every country but Denmark uses, and the bug it fixed.

    A whole-country source has to be narrowed to the area the entry covers.
    Denmark does it by asking DAWA which postnumre fall inside the circle —
    and DAWA is a Danish register, so a Dutch entry asked it about Amsterdam,
    was handed the empty answer it deserved, and listed no stations at all
    while holding 3,900 of them. Spain would have inherited exactly that.
    """
    print("the circle a whole-country source is cut down to")
    madrid = sources.Area(40.4168, -3.7038, 10_000)

    def at(lat, lon, sid):
        return sources.Station(
            name=sid, company="REPSOL", postnummer="28001", updated="",
            latitude=lat, longitude=lon, prices={"diesel": 1.7},
            station_id=sid, country="es",
        )

    pool = [
        at(40.4200, -3.7000, "near"),      # a few hundred metres away
        at(40.4700, -3.6900, "edge"),      # ~6 km
        at(41.6561, -0.8773, "zaragoza"),  # 270 km
        sources.Station(  # placed by nobody: the register listed no position
            name="unplaced", company="BP", postnummer="28002", updated="",
            prices={"diesel": 1.7}, station_id="unplaced", country="es",
        ),
    ]
    kept = {s.station_id for s in sources.stations_within(pool, madrid)}
    check("what is inside the circle is kept", kept == {"near", "edge"}, sorted(kept))
    check("Zaragoza is not within 10 km of Madrid", "zaragoza" not in kept, sorted(kept))
    check(
        "and a station with no position is not silently called nearby",
        "unplaced" not in kept,
        sorted(kept),
    )

    wide = {s.station_id for s in sources.stations_within(pool, sources.Area(40.4168, -3.7038, 400_000))}
    check("a wide enough circle reaches Zaragoza", "zaragoza" in wide, sorted(wide))
    check(
        "Spain is cut this way rather than by postal code",
        not const.country_of("es").postal_areas and const.country_of("dk").postal_areas,
    )
    check(
        "as are the countries that were quietly empty before it",
        not any(const.country_of(c).postal_areas for c in ("nl", "be", "lu", "fr")),
    )


# --- the country -----------------------------------------------------------
def test_spain_is_wired_in_as_data() -> None:
    print("what the rest of the integration learns about Spain")
    check("the country exists", "es" in const.COUNTRIES, sorted(const.COUNTRIES))

    spain = const.COUNTRIES["es"]
    check("it quotes euro", spain.unit == "€/L" and spain.spoken_currency == "euro", spain.unit)
    check("to three decimals, like its neighbours", spain.decimals == 3, spain.decimals)
    check("and a gap is cents", spain.minor_unit == "cent", spain.minor_unit)
    check("it has a Danish name to be spoken", spain.name_da == "Spanien", spain.name_da)
    check("Madrid is inside its box", spain.contains(40.4168, -3.7038))
    check("so is Barcelona", spain.contains(41.3874, 2.1686))
    check("so is Palma", spain.contains(39.5696, 2.6502))
    # The islands are 1,800 km out and are the reason the box is this wide.
    check("and so is Santa Cruz de Tenerife", spain.contains(28.4636, -16.2518))
    check("Bordeaux is not", not spain.contains(44.8378, -0.5792))
    check("nor Rome", not spain.contains(41.9028, 12.4964))
    # The cost of reaching the Canaries, stated rather than hidden: a box this
    # wide claims Lisbon and Rabat too. It is the France arrangement — a box is
    # not a border — and it resolves the same way, by the nearest forecourt.
    check("Lisbon is inside it, deliberately", spain.contains(38.7223, -9.1393))
    check("and so is Rabat", spain.contains(34.0209, -6.8416))


def test_the_options_offered_match_the_source() -> None:
    print("the fuels and radii the dialog may offer for Spain")
    fuels = sources.fuel_types_for("es")
    check(
        "exactly the eight products we read from the register",
        set(fuels)
        == {
            "blyfri95",
            "blyfri95plus",
            "blyfri98",
            "diesel",
            "dieselplus",
            "hvo100",
            "lpg",
            "cng",
        },
        sorted(fuels),
    )
    check(
        "no 92 octane, because Spain does not sell one",
        "blyfri92" not in fuels and "e85" not in fuels,
        sorted(fuels),
    )
    check(
        "Spain is not asked about a circle: one response holds the country",
        not sources.country_needs_area("es"),
    )
    check(
        "so every radius is offered",
        sources.radius_options("es") == list(const.RADIUS_OPTIONS),
        sources.radius_options("es"),
    )
    check(
        "and it still needs an anchor, because that is what the radius is measured from",
        const.country_needs_anchor("es"),
    )


def test_a_spanish_pump_is_named_the_spanish_way() -> None:
    print("the labels a Spanish dialog shows")
    check("diesel is Gasóleo A", const.fuel_label("diesel", "es") == "Gasóleo A")
    check(
        "and the 95 says which blend it is",
        const.fuel_label("blyfri95", "es") == "Gasolina 95 E5",
        const.fuel_label("blyfri95", "es"),
    )
    check(
        "while Denmark is untouched by any of it",
        const.fuel_label("blyfri95", "dk") == "Blyfri 95 (E10)",
    )


if __name__ == "__main__":
    test_a_real_record_comes_out_whole()
    test_a_decimal_comma_is_a_decimal_point()
    test_red_diesel_is_not_diesel()
    test_the_everyday_petrol_is_the_everyday_key()
    test_the_products_we_do_not_model_stay_out()
    test_field_shapes_the_fixture_lacks()
    test_an_unbranded_forecourt_still_has_a_name()
    test_the_whole_country_is_asked_for_once_with_room_to_answer()
    test_the_spanish_host_needs_its_own_cipher_list()
    test_a_country_is_cut_down_by_distance_not_by_postnummer()
    test_spain_is_wired_in_as_data()
    test_the_options_offered_match_the_source()
    test_a_spanish_pump_is_named_the_spanish_way()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all Spain source tests passed")

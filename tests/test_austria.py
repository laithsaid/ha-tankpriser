"""Tests for the Austrian source (E-Control) and the shape of its answers.

Run with: python tests/test_austria.py

Austria is the first source whose limit is a *count* rather than a distance,
and that is what most of this file is about. E-Control answers with at most ten
stations, cheapest first, for one fuel, and no parameter raises any of those
three — `radius`, `limit` and `maxResults` were all sent live and all ignored,
and a repeated `fuelType` is accepted and then answered for the first one only.
So the tests below pin down what we do about it: three requests per circle,
merged by id; open forecourts only, because a closed one would take a slot from
one you could drive to; and a radius that can only be applied after the fact.

`sources.py` imports aiohttp and nothing from Home Assistant, so the stub here
is enough to run the whole file with nothing installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    loaded = {}
    for name in ("const", "sources"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["const"], loaded["sources"]


const, sources = _load()

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


# --- the parser ------------------------------------------------------------
def test_parses_a_real_response() -> None:
    print("parse_econtrol on a captured Vienna response")
    with open(
        os.path.join(FIXTURES, "econtrol_vienna.json"), encoding="utf-8"
    ) as handle:
        payload = json.load(handle)

    stations = sources.parse_econtrol(payload, "DIE")
    check(
        "the station that reported no price is dropped",
        len(stations) == 4 and len(payload) == 5,
        f"{len(stations)} of {len(payload)}",
    )

    first = stations[0]
    check("the forecourt's own name becomes the company", first.company == "NNB BRUNNENTANKSTELLE", first.company)
    check(
        "the name locates the forecourt",
        first.name == "NNB BRUNNENTANKSTELLE Brunnengasse 4",
        first.name,
    )
    check("the postal code is carried", first.postnummer == "1160", first.postnummer)
    check("the town is carried", first.city == "WIEN", first.city)
    check("coordinates are exact", round(first.latitude, 5) == 48.20462, first.latitude)
    check("the provider's id is kept", first.station_id == "1135", first.station_id)
    check("stations are marked Austrian", first.country == "at", first.country)
    check("the price lands on our own fuel key", first.prices == {"diesel": 2.195}, first.prices)
    check(
        "E-Control publishes no timestamp, so none is invented",
        first.updated == "",
        first.updated,
    )


def test_field_shapes_the_fixture_lacks() -> None:
    print("parse_econtrol on the shapes a live response also sends")
    payload = [
        {
            # The prices array carries the fuel we did NOT ask about. It
            # should never happen, and reading it would quietly file a diesel
            # price under petrol, so the parser matches on fuelType rather
            # than trusting the position.
            "id": 1,
            "name": "Fremd",
            "location": {"address": "Hauptstr. 1", "postalCode": "4020", "city": "Linz",
                         "latitude": 48.3, "longitude": 14.3},
            "open": True,
            "prices": [{"fuelType": "DIE", "amount": 1.899, "label": "Diesel"}],
        },
        {
            # A reporting gap as a zero rather than an absence.
            "id": 2,
            "name": "Null",
            "location": {"address": "Dorfstr. 2", "postalCode": "5020", "city": "Salzburg",
                         "latitude": 47.8, "longitude": 13.0},
            "open": True,
            "prices": [{"fuelType": "SUP", "amount": 0, "label": "Super 95"}],
        },
        {
            # No name at all, and closed. Both are survivable.
            "id": 3,
            "name": "",
            "location": {"address": "Feldweg 3", "postalCode": "6020", "city": "Innsbruck",
                         "latitude": 47.2, "longitude": 11.4},
            "open": False,
            "prices": [{"fuelType": "SUP", "amount": 1.789, "label": "Super 95"}],
        },
        # Not a record at all. A list with a stray string in it must not take
        # the whole refresh down.
        "nonsense",
    ]

    stations = sources.parse_econtrol(payload, "SUP")
    kept = {s.station_id for s in stations}
    check("a price for another fuel is not read as ours", "1" not in kept, sorted(kept))
    check("a zero is not a price anyone could pay", "2" not in kept, sorted(kept))
    check("a nameless forecourt still arrives", "3" in kept, sorted(kept))
    check("a stray non-record is skipped, not fatal", len(stations) == 1, len(stations))

    nameless = stations[0]
    check("and it is given a name rather than none", nameless.name.startswith("Tankstelle"), nameless.name)
    check("closed is carried through as closed", nameless.is_open is False, nameless.is_open)
    check("the Austrian petrol key is ours, not theirs", nameless.prices == {"blyfri95": 1.789}, nameless.prices)


# --- the request -----------------------------------------------------------
def test_one_request_per_fuel_and_open_only() -> None:
    print("the requests E-Control actually receives")
    session = _FakeSession([])
    asyncio.run(sources.fetch_econtrol(session, None, sources.Area(48.2082, 16.3738, 25_000)))

    check("one request per fuel, and no more", len(session.calls) == 3, len(session.calls))
    asked = [call[1].get("fuelType") for call in session.calls]
    check("all three Austrian fuels are asked for", asked == ["SUP", "DIE", "GAS"], asked)

    params = session.calls[0][1]
    check(
        "closed forecourts are excluded, because a slot spent on one is wasted",
        params["includeClosed"] == "false",
        params,
    )
    check("the circle's centre is what is sent", params["latitude"] == "48.208200", params)
    check(
        "and the radius is not, because no parameter carries it",
        not {"radius", "limit", "maxResults"} & set(params),
        sorted(params),
    )


def test_the_same_station_keeps_both_its_prices() -> None:
    print("three answers, merged by the station's own id")

    def record(fuel: str, amount: float) -> dict:
        return {
            "id": 99,
            "name": "eni",
            "location": {"address": "Ring 1", "postalCode": "1010", "city": "Wien",
                         "latitude": 48.2, "longitude": 16.37},
            "open": True,
            "prices": [{"fuelType": fuel, "amount": amount, "label": fuel}],
        }

    session = _FakeSession(None)
    session.payloads = {
        "SUP": [record("SUP", 1.962)],
        "DIE": [record("DIE", 2.242)],
        "GAS": [record("GAS", 1.500)],
    }
    stations = asyncio.run(
        sources.fetch_econtrol(session, None, sources.Area(48.2, 16.37, 10_000))
    )
    check("the forecourt is one station, not three", len(stations) == 1, len(stations))
    check(
        "carrying every fuel it sells",
        stations[0].prices == {"blyfri95": 1.962, "diesel": 2.242, "cng": 1.5},
        stations[0].prices,
    )


def test_the_radius_is_applied_to_what_came_back() -> None:
    print("the circle E-Control was not told about")

    def record(sid: int, lat: float, lon: float) -> dict:
        return {
            "id": sid,
            "name": "OMV",
            "location": {"address": "Weg 1", "postalCode": "1010", "city": "Wien",
                         "latitude": lat, "longitude": lon},
            "open": True,
            "prices": [{"fuelType": "GAS", "amount": 1.799, "label": "CNG"}],
        }

    session = _FakeSession(None)
    session.payloads = {
        "SUP": [],
        "DIE": [],
        # Vienna, and Wiener Neustadt 50 km south — which is exactly what a
        # live CNG query answered, because the pumps are rare enough that
        # E-Control widens until it has ten of them.
        "GAS": [record(1, 48.2100, 16.3700), record(2, 47.8149, 16.2497)],
    }
    stations = asyncio.run(
        sources.fetch_econtrol(session, None, sources.Area(48.2082, 16.3738, 10_000))
    )
    kept = {s.station_id for s in stations}
    check("the station inside the circle is kept", kept == {"1"}, sorted(kept))
    check(
        "and the far one is dropped, since nothing in the request excluded it",
        "2" not in kept,
        sorted(kept),
    )

    wide = asyncio.run(
        sources.fetch_econtrol(session, None, sources.Area(48.2082, 16.3738, 60_000))
    )
    check(
        "a wide enough circle keeps both",
        {s.station_id for s in wide} == {"1", "2"},
        sorted(s.station_id for s in wide),
    )


def test_no_area_means_no_request() -> None:
    print("an area-scoped source with nowhere to look")
    session = _FakeSession([])
    try:
        asyncio.run(sources.fetch_econtrol(session, None, None))
    except ValueError:
        check("asking about nowhere raises rather than guessing", True)
    else:
        check("asking about nowhere raises rather than guessing", False)
    check("and nothing was requested", session.calls == [], session.calls)


def test_a_bad_payload_is_a_failure_not_an_empty_answer() -> None:
    print("a payload that is not a list of stations")
    session = _FakeSession({"error": "nope"})
    try:
        asyncio.run(sources.fetch_econtrol(session, None, sources.Area(48.2, 16.37, 10_000)))
    except ValueError:
        check("an object where a list belongs is an error", True)
    else:
        check("an object where a list belongs is an error", False)


# --- the country -----------------------------------------------------------
def test_austria_is_wired_in_as_data() -> None:
    print("what the rest of the integration learns about Austria")
    check("the country exists", "at" in const.COUNTRIES, sorted(const.COUNTRIES))

    austria = const.COUNTRIES["at"]
    check("it quotes euro", austria.unit == "€/L" and austria.spoken_currency == "euro", austria.unit)
    check("to three decimals, like its neighbours", austria.decimals == 3, austria.decimals)
    check("and a gap is cents", austria.minor_unit == "cent", austria.minor_unit)
    check("it has a Danish name to be spoken", austria.name_da == "Østrig", austria.name_da)
    check("Vienna is inside its box", austria.contains(48.2082, 16.3738))
    # A box is not a border, and this one is honest about it: Austria reaches
    # west to Vorarlberg and north to the Czech line, so any rectangle holding
    # both also holds Munich. That is the Luxembourg arrangement rather than a
    # fault — a country only joins an answer when it has a forecourt in reach,
    # and E-Control has none within a Bavarian radius.
    check("Munich falls inside it too, deliberately", austria.contains(48.1372, 11.5756))
    check("Prague does not", not austria.contains(50.0755, 14.4378))
    check("nor Zurich", not austria.contains(47.3769, 8.5417))
    check("nor Venice", not austria.contains(45.4408, 12.3155))


def test_the_options_offered_match_the_source() -> None:
    print("the fuels and radii the dialog may offer for Austria")
    fuels = sources.fuel_types_for("at")
    check(
        "exactly the three fuels E-Control publishes",
        set(fuels) == {"blyfri95", "diesel", "cng"},
        sorted(fuels),
    )
    check(
        "no Super 98 is offered, because Austria does not publish one",
        "blyfri98" not in fuels and "dieselplus" not in fuels,
        sorted(fuels),
    )
    check("Austria needs a circle to search", sources.country_needs_area("at"))
    check(
        "and every radius is offered, because the cap is a count and not a distance",
        sources.radius_options("at") == list(const.RADIUS_OPTIONS),
        sources.radius_options("at"),
    )


def test_cng_is_not_quoted_per_litre() -> None:
    print("the one Austrian fuel that is not sold by the litre")
    check(
        "CNG keeps its own denominator",
        const.FUEL_QUANTITY.get("cng", "litres") != "litres",
        const.FUEL_QUANTITY.get("cng"),
    )


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type=None) -> object:
        return self._payload


class _FakeSession:
    """Just enough of aiohttp.ClientSession to record what was requested.

    `payloads` keyed by fuel exists because Austria is the only source that
    asks the same URL three times and must get three different answers.
    """

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.payloads: dict[str, object] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.payloads:
            return _FakeResponse(self.payloads.get(params.get("fuelType"), []))
        return _FakeResponse(self._payload)


if __name__ == "__main__":
    test_parses_a_real_response()
    test_field_shapes_the_fixture_lacks()
    test_one_request_per_fuel_and_open_only()
    test_the_same_station_keeps_both_its_prices()
    test_the_radius_is_applied_to_what_came_back()
    test_no_area_means_no_request()
    test_a_bad_payload_is_a_failure_not_an_empty_answer()
    test_austria_is_wired_in_as_data()
    test_the_options_offered_match_the_source()
    test_cng_is_not_quoted_per_litre()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all Austria source tests passed")

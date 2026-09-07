"""Tests for the German source (Tankerkoenig) and the area-scoped plumbing.

Run with: python tests/test_germany.py

`sources.py` has no Home Assistant imports, only aiohttp, which is stubbed here
so the test needs nothing installed. Two different things are checked:

* the parser, against a fixture captured verbatim from a real response
  (`tests/fixtures/tankerkoenig_list.json`), plus the field shapes that
  fixture happens not to contain — a fuel a station does not sell, a postal
  code with a leading zero, an unbranded forecourt;
* the plumbing that an area-scoped provider needs and a national one never
  did — one cache entry per circle, a key that never reaches a log, and no
  request at all when nobody named a circle.
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
    print("parse_tankerkoenig on a captured response")
    with open(
        os.path.join(FIXTURES, "tankerkoenig_list.json"), encoding="utf-8"
    ) as handle:
        payload = json.load(handle)

    stations = sources.parse_tankerkoenig(payload)
    check("every station in the response is kept", len(stations) == 5, len(stations))

    first = stations[0]
    check("brand becomes the company", first.company == "TotalEnergies", first.company)
    check(
        "the name locates the forecourt",
        first.name == "TotalEnergies Margarete-Sommer-Str. 2",
        first.name,
    )
    check("the town is carried", first.city == "Berlin", first.city)
    check("coordinates are exact", first.latitude == 52.530831, first.latitude)
    check("the provider's id is kept", first.station_id.count("-") == 4, first.station_id)
    check("opening state is carried", first.is_open is True, first.is_open)
    check("stations are marked German", first.country == "de", first.country)
    check(
        "the three German fuels map onto our keys",
        set(first.prices) == {"blyfri95", "blyfri95plus", "diesel"},
        sorted(first.prices),
    )


def test_field_shapes_the_fixture_lacks() -> None:
    print("parse_tankerkoenig on the shapes a live response also sends")
    payload = {
        "ok": True,
        "stations": [
            {
                # Dresden: the postal code arrives as a number, so its leading
                # zero is already gone by the time we see it.
                "id": "a", "brand": "ARAL", "name": "Aral Tankstelle",
                "street": "Bautzner Str.", "houseNumber": "1", "place": "Dresden",
                "postCode": 1099, "lat": 51.07, "lng": 13.78,
                "diesel": 1.699, "e5": 1.899, "e10": 1.839, "isOpen": True,
            },
            {
                # An independent: no brand at all, and no E5 on the forecourt.
                "id": "b", "brand": "", "name": "Freie Tankstelle Meier",
                "street": "Dorfstr.", "houseNumber": "3", "place": "Kleinort",
                "postCode": 49661, "lat": 52.7, "lng": 7.9,
                "diesel": 1.649, "e5": False, "e10": 1.789, "isOpen": False,
            },
            {
                # A reporting gap: zeroes are not a price anyone could pay.
                "id": "c", "brand": "Shell", "name": "Shell",
                "street": "Hauptstr.", "houseNumber": "9", "place": "Nirgendwo",
                "postCode": 10115, "lat": 52.5, "lng": 13.4,
                "diesel": 0, "e5": 0, "e10": 0, "isOpen": True,
            },
        ],
    }
    stations = sources.parse_tankerkoenig(payload)

    check("a station with no usable price is dropped", len(stations) == 2, len(stations))
    dresden, independent = stations
    check(
        "a leading zero is restored on the postal code",
        dresden.postnummer == "01099",
        dresden.postnummer,
    )
    check(
        "three decimals survive the parse",
        dresden.prices["blyfri95"] == 1.839,
        dresden.prices["blyfri95"],
    )
    check(
        "an unbranded station falls back to its own name",
        independent.company == "Freie Tankstelle Meier",
        independent.company,
    )
    check(
        "a fuel the station does not sell is absent, not zero",
        "blyfri95plus" not in independent.prices,
        independent.prices,
    )
    check("a closed station says so", independent.is_open is False, independent.is_open)
    check(
        "identity is the provider's id, not brand+postcode",
        dresden.key == "de|a",
        dresden.key,
    )


def test_a_failure_arrives_as_http_200() -> None:
    print("Tankerkoenig reports errors with ok:false and a 200")
    session = _FakeSession(
        {"status": "error", "ok": False,
         "message": "Key existiert nicht oder ist deaktiviert"}
    )
    raised = ""
    try:
        asyncio.run(sources.fetch_tankerkoenig(session, "k" * 36, sources.Area(52.5, 13.4, 25_000)))
    except sources.ProviderAuthError as err:
        raised = f"auth:{err}"
    except Exception as err:  # noqa: BLE001
        raised = f"other:{type(err).__name__}"
    check(
        "a key that is not activated yet is an auth failure",
        raised.startswith("auth:"),
        raised,
    )

    session = _FakeSession({"ok": False, "message": "Radius zu gross"})
    raised = ""
    try:
        asyncio.run(sources.fetch_tankerkoenig(session, "k" * 36, sources.Area(52.5, 13.4, 25_000)))
    except sources.ProviderAuthError:
        raised = "auth"
    except ValueError:
        raised = "value"
    check("any other refusal is not blamed on the key", raised == "value", raised)


def test_the_radius_is_capped_and_the_key_is_a_parameter() -> None:
    print("the request Tankerkoenig actually receives")
    session = _FakeSession({"ok": True, "stations": []})
    asyncio.run(
        sources.fetch_tankerkoenig(session, "secret-key-000000", sources.Area(52.5, 13.4, 50_000))
    )
    params = session.calls[-1][1]
    check("the 25 km cap is honoured", params["rad"] == "25", params["rad"])
    check("the key travels as a parameter", params["apikey"] == "secret-key-000000", params)
    check("the URL itself carries no key", "apikey" not in session.calls[-1][0], session.calls[-1][0])


# --- the area plumbing -----------------------------------------------------
def test_one_cache_entry_per_circle() -> None:
    print("the provider cache keys on the circle, not just the provider")
    provider = sources.PROVIDERS["tankerkoenig"]
    berlin = sources.Area(52.5200, 13.4050, 25_000)
    jitter = sources.Area(52.5203, 13.4048, 25_000)   # the same fix, moments later
    hamburg = sources.Area(53.5511, 9.9937, 25_000)

    check(
        "a national provider still has exactly one slot",
        sources._cache_key(sources.PROVIDERS["ok"], None) == "ok@",
        sources._cache_key(sources.PROVIDERS["ok"], None),
    )
    check(
        "a metre of GPS jitter reuses the cached answer",
        sources._cache_key(provider, berlin) == sources._cache_key(provider, jitter),
    )
    check(
        "another city is a different question",
        sources._cache_key(provider, berlin) != sources._cache_key(provider, hamburg),
    )
    check(
        "so is the same centre at a different radius",
        sources._cache_key(provider, berlin)
        != sources._cache_key(provider, sources.Area(52.52, 13.405, 5_000)),
    )


def test_countries_do_not_mix() -> None:
    print("providers are selected by country")
    dk = {p.key for p in sources.providers_for("dk")}
    de = {p.key for p in sources.providers_for("de")}
    check("Denmark keeps its chains", "ok" in dk and "q8" in dk, dk)
    check("Germany is Tankerkoenig alone", de == {"tankerkoenig"}, de)
    check("and it is not offered to Denmark", "tankerkoenig" not in dk, dk)


def test_no_area_means_no_request() -> None:
    print("an area provider is skipped rather than guessed at")
    calls: list[str] = []

    async def _spy(session, credential=None, area=None):
        calls.append(str(area))
        return []

    original = sources.PROVIDERS["tankerkoenig"]
    sources.PROVIDERS["tankerkoenig"] = _replace_fetch(original, _spy)
    try:
        creds = {"tankerkoenig": "k" * 36}
        asyncio.run(sources.fetch_all(_FakeSession({}), creds, country="de"))
        check("no circle, no request", calls == [], calls)

        sources.invalidate_cache("tankerkoenig")
        asyncio.run(
            sources.fetch_all(
                _FakeSession({}), creds, country="de",
                area=sources.Area(52.5, 13.4, 25_000),
            )
        )
        check("a circle produces exactly one request", len(calls) == 1, calls)
    finally:
        sources.PROVIDERS["tankerkoenig"] = original
        sources.invalidate_cache("tankerkoenig")


def test_the_key_never_reaches_a_log() -> None:
    print("redaction of the credential in error text")
    key = "1234abcd-5678-90ef-1234-567890abcdef"
    err = (
        "Cannot connect to host creativecommons.tankerkoenig.de "
        f"[url=https://creativecommons.tankerkoenig.de/json/list.php?apikey={key}]"
    )
    cleaned = sources.redact(err, key)
    check("the key is gone", key not in cleaned, cleaned)
    check("the rest of the message survives", "Cannot connect" in cleaned, cleaned)
    check("no credential is a no-op", sources.redact("plain", None) == "plain")


def test_a_danish_discount_cannot_reach_a_german_price() -> None:
    print("loyalty discounts stop at the border")
    german_shell = sources.Station(
        name="Shell Hauptstr. 9", company="Shell", postnummer="10115",
        updated="", prices={"diesel": 1.699}, country="de",
    )
    danish_shell = sources.Station(
        name="Shell Vejlevej 1", company="Shell", postnummer="7000",
        updated="", prices={"diesel": 12.49},
    )
    out = sources.apply_discounts([german_shell, danish_shell], {"shell": 30})
    check("the German price is untouched", out[0].prices["diesel"] == 1.699, out[0].prices)
    check("the Danish one still gets its 30 øre", out[1].prices["diesel"] == 12.19, out[1].prices)


# --- what the dialog offers matches what the source can deliver ------------
def test_the_options_offered_match_the_source() -> None:
    print("the per-country UI tables agree with the providers behind them")
    cap = sources.max_radius_km("de")
    check("Germany has a radius ceiling", cap == 25, cap)

    offered = [const.radius_to_metres(r) / 1000 for r in sources.radius_options("de")]
    check(
        "no radius is offered that the source would silently shrink",
        max(offered) <= cap,
        offered,
    )
    check(
        "the default asks for the whole circle",
        const.radius_to_metres(sources.default_radius("de")) / 1000 == cap,
        sources.default_radius("de"),
    )
    check(
        "Denmark keeps its wider choice",
        sources.max_radius_km("dk") == 0 and "50 km" in sources.radius_options("dk"),
    )

    # The fuels the dialog lists must be the fuels a price can ever arrive for,
    # or a user ticks one and gets a sensor that is unavailable forever.
    deliverable = set(sources._TK_PRODUCT_MAP.values())
    check(
        "Germany lists exactly the three fuels MTS-K publishes",
        set(sources.fuel_types_for("de")) == deliverable,
        sorted(sources.fuel_types_for("de")),
    )
    check(
        "each one has a German name in the picker",
        [const.fuel_label(k, "de") for k in sources.fuel_types_for("de")]
        == ["Super E10", "Super E5", "Diesel"],
        [const.fuel_label(k, "de") for k in sources.fuel_types_for("de")],
    )
    check(
        "and the Danish names are untouched",
        const.fuel_label("blyfri95", "dk") == "Blyfri 95 (E10)",
        const.fuel_label("blyfri95", "dk"),
    )
    check(
        "the pre-ticked fuels are ones Germany sells",
        set(sources.default_fuel_types("de")) <= deliverable,
        sources.default_fuel_types("de"),
    )


def test_which_countries_need_a_circle() -> None:
    print("country_needs_area")
    check("Denmark does not", sources.country_needs_area("dk") is False)
    check("Germany does", sources.country_needs_area("de") is True)
    check(
        "and a country we do not support needs nothing",
        sources.country_needs_area("SE") is False,
    )


# --- helpers ---------------------------------------------------------------
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
    """Just enough of aiohttp.ClientSession to record what was requested."""

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return _FakeResponse(self._payload)


def _replace_fetch(provider, fetch):
    """A copy of a Provider with a different fetcher (it is frozen)."""
    import dataclasses

    return dataclasses.replace(provider, fetch=fetch)


if __name__ == "__main__":
    test_parses_a_real_response()
    test_field_shapes_the_fixture_lacks()
    test_a_failure_arrives_as_http_200()
    test_the_radius_is_capped_and_the_key_is_a_parameter()
    test_one_cache_entry_per_circle()
    test_countries_do_not_mix()
    test_no_area_means_no_request()
    test_the_key_never_reaches_a_log()
    test_a_danish_discount_cannot_reach_a_german_price()
    test_the_options_offered_match_the_source()
    test_which_countries_need_a_circle()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all Germany source tests passed")

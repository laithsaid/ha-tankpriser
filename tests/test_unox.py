"""Tests for the Uno-X source (sources.parse_unox) and its OAuth credential.

Run with: python tests/test_unox.py

Uno-X is the only source that will not take a credential at all. Everything
else hands over a key — in a header, or, for Tankerkoenig, in the query string.
This one wants OAuth 2.0 *client credentials*: a client_id and client_secret
posted to a token endpoint, traded for a JWT that lives 900 seconds, and it is
the JWT that the data call carries. That gains two new ways to be wrong, and
both are silent:

* the secret must never leave the token request — not into the data call, not
  into a URL, and not into a log line when the token endpoint says no;
* the token must be reused. Uno-X allows **one request per key per 30 seconds**,
  and a token fetched per refresh would spend half that budget re-asking for a
  token that had not expired.

The fixture is three stations captured from the live feed on 2026-09-21
(`tests/fixtures/unox_dk.json`) plus three hand-written ones for shapes it does
not contain: a forecourt selling nothing we model, one with no postal code, and
one with a pump reporting 0 alongside a petrol product under a name the map does
not list. The live half carries the shapes that would fail quietly — coordinates
as strings with a Danish decimal comma, a timestamp separated by a space instead
of a T, and the real `Blyfri 92` the documentation never mentions. No credential
appears in it: the key is in the request, never in the response.

Where the live feed and the official PDF (v1.0, 01.01.2026) disagree, the feed
wins and the test says so — `data` not `Data`, and `fullAddress` not
`addressHouseNumber`.
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


class _FakeResponseError(Exception):
    """Stand-in for aiohttp.ClientResponseError, which carries a status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _load() -> tuple[types.ModuleType, types.ModuleType]:
    sys.modules.setdefault(
        "aiohttp",
        types.SimpleNamespace(
            ClientSession=object,
            ClientError=Exception,
            ClientTimeout=lambda **kwargs: None,
            ClientResponseError=_FakeResponseError,
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
    io.open(os.path.join(FIXTURES, "unox_dk.json"), encoding="utf-8").read()
)
STATIONS = sources.parse_unox(PAYLOAD)
BY_ID = {s.station_id: s for s in STATIONS}

CREDENTIAL = "tankpriser-client:s3cr3t-value-nobody-should-see"

checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


# -- the payload ------------------------------------------------------------
def test_coordinates_survive_the_danish_decimal_comma() -> None:
    """Uno-X ships coordinates as strings written "55,568269773969".

    `float()` rejects that outright, so a parser that trusted it would place
    every Uno-X forecourt by its postnummer — the centre of a postal district,
    up to several kilometres from the pump — while showing no sign of trouble.
    """
    station = BY_ID["2"]
    check(abs(station.latitude - 55.568269773969) < 1e-9, str(station.latitude))
    check(abs(station.longitude - 9.707150459289) < 1e-9, str(station.longitude))
    for other in STATIONS:
        check(other.latitude is not None, f"{other.name} has no latitude")
        check(not other.coord_approx, f"{other.name} marked approximate")


def test_the_timestamp_is_a_date_not_a_clock() -> None:
    """`lastUpdated` separates date from time with a space, not a T.

    Split on "T" alone it would come through whole — and `updated` is picked
    with max() across a station's products, so "2026-09-20 06:12:04" would sort
    above "2026-09-21" and report the older price as the newest one.
    """
    check(BY_ID["2"].updated == "2026-09-21", BY_ID["2"].updated)
    check(sources._short_date("2026-09-21 05:48:19.400000") == "2026-09-21", "space")
    check(sources._short_date("2026-09-21T05:48:19Z") == "2026-09-21", "T")
    check(sources._short_date("2026-09-21") == "2026-09-21", "bare date")
    check(sources._short_date(None) == "", "nothing")


def test_products_land_on_the_right_fuel() -> None:
    station = BY_ID["2"]
    check(station.prices["blyfri95"] == 17.69, str(station.prices))
    check(station.prices["oktan100"] == 19.89, str(station.prices))
    check(station.prices["diesel"] == 19.19, str(station.prices))
    check("blyfri92" not in station.prices, "this one sells no 92")


def test_92_octane_is_not_sold_as_95() -> None:
    """The documentation lists only Benzin and Diesel, and names 95 and 100.

    One forecourt in the live feed — Terndrup, 9575 — sells **Blyfri 92**, at 3
    øre under the 95 standing next to it. Folded into `blyfri95` it would win
    that station's ranking outright and send a car that needs 95 to a pump it
    must not use, which is the whole reason `blyfri92` is its own fuel. Go'on
    was not the only chain selling it after all.
    """
    terndrup = BY_ID["1832"]
    check(terndrup.prices["blyfri92"] == 17.66, str(terndrup.prices))
    check(terndrup.prices["blyfri95"] == 17.69, str(terndrup.prices))
    check(terndrup.prices["blyfri92"] < terndrup.prices["blyfri95"], "92 is cheaper")


def test_the_street_line_is_read_from_the_name_the_feed_uses() -> None:
    """`fullAddress` live, `addressHouseNumber` in the documentation.

    Reading only the documented name emptied `address` at all 279 forecourts and
    said nothing about it: `stationName` still filled the label, so the list
    looked right while the line under each name — and what a navigator is handed
    — was blank.
    """
    check(BY_ID["2"].address == "Vejlevej 141", repr(BY_ID["2"].address))
    check(BY_ID["1832"].address == "Hadsundvej 43", repr(BY_ID["1832"].address))
    for station in STATIONS:
        check(station.address != "", f"{station.name} has no street line")
    documented = {
        "stationId": 7,
        "stationName": "Efter dokumentationen",
        "brand": "Uno-X",
        "address": {
            "addressHouseNumber": "Vejlevej 141",
            "postalCode": "7000",
            "city": "Fredericia",
            "coordinates": {"latitude": "55,5", "longitude": "9,7"},
        },
        "products": [
            {"fuelType": "Diesel", "productName": "Diesel", "price": 19.19,
             "lastUpdated": "2026-09-21 11:15:38.080000"}
        ],
    }
    parsed = sources.parse_unox({"data": [documented]})
    check(parsed[0].address == "Vejlevej 141", "the documented name still works")


def test_an_unlisted_petrol_name_falls_back_to_its_octane() -> None:
    """A pump renamed on the forecourt would otherwise vanish without a word.

    Not hypothetical: this is what placed the real Blyfri 92 before its name was
    in the map at all. `octane` is its own field on every benzin product.
    """
    check(BY_ID["9003"].prices["blyfri95"] == 17.95, str(BY_ID["9003"].prices))


def test_a_pump_that_has_not_reported_is_not_a_free_litre() -> None:
    check("diesel" not in BY_ID["9003"].prices, str(BY_ID["9003"].prices))


def test_a_station_without_100_simply_has_none() -> None:
    """20 of the 279 do not sell it; an absent fuel is not a zero."""
    check("oktan100" not in BY_ID["68"].prices, str(BY_ID["68"].prices))
    check(BY_ID["68"].prices["blyfri95"] > 0, str(BY_ID["68"].prices))


def test_stations_we_cannot_use_are_dropped() -> None:
    check("9001" not in BY_ID, "AdBlue-only station should be dropped")
    check("9002" not in BY_ID, "station with no postal code should be dropped")
    check(len(STATIONS) == 4, f"{len(STATIONS)} stations")


def test_the_brand_matches_the_chain_it_is_discounted_as() -> None:
    """A station that called itself the wrong thing would be priced with
    somebody else's loyalty card."""
    for station in STATIONS:
        key = sources.chain_key(station.company)
        check(key == "unox", f"{station.company!r} -> {key!r}")


def test_either_case_of_the_data_key_is_accepted() -> None:
    """The live feed says `data`; the documentation's table says `Data`.

    The fixture is the live shape, so the capitalised one is what needs proving
    here — and one letter's case is not worth an empty chain either way.
    """
    capitalised = {"Data": PAYLOAD["data"]}
    check(len(sources.parse_unox(capitalised)) == len(STATIONS), "capital 'Data'")
    check(sources.parse_unox({}) == [], "an empty payload is no stations")


# -- the credential ---------------------------------------------------------
def _fake_session(token_payload, data_payload, log: dict):
    """A session that records what the token and data calls were given."""

    class _Resp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            if self.status >= 400:
                raise _FakeResponseError(self.status)

        async def json(self, content_type=None):
            return self._payload

    class _Session:
        def post(self, url, data=None, headers=None, timeout=None):
            log["token_url"] = url
            log["token_headers"] = headers or {}
            log["token_body"] = data or {}
            log["token_calls"] = log.get("token_calls", 0) + 1
            return _Resp(token_payload)

    return _Session()


def _run_fetch(token_payload, data_payload, log, credential=CREDENTIAL):
    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        log["data_url"] = url
        log["data_headers"] = extra_headers or {}
        log["data_params"] = params or {}
        log["data_calls"] = log.get("data_calls", 0) + 1
        if isinstance(data_payload, Exception):
            raise data_payload
        return data_payload

    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    sources._UNOX_TOKENS.clear()
    try:
        return asyncio.run(
            sources.PROVIDERS["unox"].fetch(
                _fake_session(token_payload, data_payload, log), credential, None
            )
        )
    finally:
        sources._fetch_json = original
        sources._UNOX_TOKENS.clear()


TOKEN_OK = {
    "access_token": "JWT_ACCESS_TOKEN",
    "expires_in": 900,
    "token_type": "Bearer",
    "scope": "apigw profile",
}


def test_the_secret_is_spent_on_the_token_and_never_travels_again() -> None:
    """The client secret buys a token and then stays home.

    A secret that also rode along on the data call would reach one more server,
    one more log and one more exception string for no gain at all.
    """
    log: dict = {}
    stations = _run_fetch(TOKEN_OK, PAYLOAD, log)

    check(log["token_url"] == const.UNOX_TOKEN_URL, log["token_url"])
    check(log["token_body"] == {"grant_type": "client_credentials"}, str(log["token_body"]))
    check(
        log["token_headers"]["Authorization"]
        == "Basic dGFua3ByaXNlci1jbGllbnQ6czNjcjN0LXZhbHVlLW5vYm9keS1zaG91bGQtc2Vl",
        str(log["token_headers"]["Authorization"]),
    )
    check(log["data_headers"] == {"Authorization": "Bearer JWT_ACCESS_TOKEN"},
          str(log["data_headers"]))
    check(log["data_params"] == {}, f"nothing may ride in the query string: {log['data_params']}")
    check(CREDENTIAL not in log["data_url"], log["data_url"])
    check("s3cr3t-value-nobody-should-see" not in str(log["data_headers"]), "secret leaked")
    check(len(stations) == len(STATIONS), f"{len(stations)} stations")


def test_a_live_token_is_reused_rather_than_bought_again() -> None:
    """One request per key per 30 seconds is the whole budget. Two fetches
    inside a token's 900-second life must cost one token, not two."""
    log: dict = {}

    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        log["data_calls"] = log.get("data_calls", 0) + 1
        return PAYLOAD

    session = _fake_session(TOKEN_OK, PAYLOAD, log)
    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    sources._UNOX_TOKENS.clear()
    try:
        asyncio.run(sources.fetch_unox(session, CREDENTIAL))
        asyncio.run(sources.fetch_unox(session, CREDENTIAL))
    finally:
        sources._fetch_json = original
        sources._UNOX_TOKENS.clear()

    check(log["token_calls"] == 1, f"{log['token_calls']} token requests for two fetches")
    check(log["data_calls"] == 2, f"{log['data_calls']} data requests")


def test_an_expiring_token_is_replaced_before_it_dies_in_flight() -> None:
    """Held for its lifetime *minus* a margin, so a request can never leave
    with a token that expires on the way."""
    log: dict = {}
    short = {**TOKEN_OK, "expires_in": const.UNOX_TOKEN_MARGIN_S}

    async def fake_fetch_json(
        session, url, extra_headers=None, params=None, timeout_s=None, ssl_context=None
    ):
        return PAYLOAD

    session = _fake_session(short, PAYLOAD, log)
    original = sources._fetch_json
    sources._fetch_json = fake_fetch_json
    sources._UNOX_TOKENS.clear()
    try:
        asyncio.run(sources.fetch_unox(session, CREDENTIAL))
        asyncio.run(sources.fetch_unox(session, CREDENTIAL))
    finally:
        sources._fetch_json = original
        sources._UNOX_TOKENS.clear()

    check(log["token_calls"] == 2, "a token with no life left must not be reused")


def test_half_a_key_is_a_credential_error_not_a_network_one() -> None:
    """Someone who pasted only the client id should be told to look at the key,
    not at their internet connection."""
    for half in ("just-a-client-id", "client_id:", ":only-a-secret", "", "  "):
        raised = None
        try:
            sources._unox_basic(half)
        except sources.ProviderAuthError as err:
            raised = err
        check(raised is not None, f"{half!r} should be rejected as a bad credential")
        check("client_id:client_secret" in str(raised), str(raised))


def _refusing_session(status: int):
    """A token endpoint that answers one status and no body."""

    class _Resp:
        def __init__(self) -> None:
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            raise AssertionError("a refusal must be caught by status, not raised")

        async def json(self, content_type=None):
            return {}

    class _Session:
        def post(self, url, data=None, headers=None, timeout=None):
            return _Resp()

    return _Session()


def test_a_refused_pair_reads_as_a_bad_key() -> None:
    """Keycloak says 401 to an unknown client and 400 to a wrong secret. Both
    are the same thing to whoever is looking at the dialog, and neither is
    something a retry will fix."""
    for status in (400, 401, 403):
        sources._UNOX_TOKENS.clear()
        raised = None
        try:
            asyncio.run(sources._unox_token(_refusing_session(status), CREDENTIAL))
        except sources.ProviderAuthError as err:
            raised = err
        check(raised is not None, f"HTTP {status} should be a ProviderAuthError")
        check(str(status) in str(raised), str(raised))
    check(sources._UNOX_TOKENS == {}, "nothing to cache from a refusal")


def test_a_token_endpoint_that_answers_without_a_token() -> None:
    """A 200 with no access_token is still a key that does not work."""
    log: dict = {}
    raised = None
    try:
        _run_fetch({"expires_in": 900}, PAYLOAD, log)
    except sources.ProviderAuthError as err:
        raised = err
    check(raised is not None, "no token in the response must not pass silently")


def test_a_rejected_token_is_thrown_away_not_replayed() -> None:
    """A token refused although our clock says it is good means the key was
    revoked. Kept, it would be replayed every ten minutes for ever."""
    log: dict = {}
    raised = None
    try:
        _run_fetch(TOKEN_OK, _FakeResponseError(401), log)
    except sources.ProviderAuthError as err:
        raised = err
    check(raised is not None, "a 401 on the data call must be a ProviderAuthError")
    check(sources._UNOX_TOKENS == {}, "the dead token must not be kept")


def test_the_secret_is_redacted_from_anything_logged() -> None:
    """`_fetch_provider` runs every failure through `redact` before logging it.

    The stored credential is a pair, but a token endpoint reports the id and
    the secret separately — so blanking only the joined form would blank
    nothing at all.
    """
    message = (
        "401 for client tankpriser-client with secret "
        "s3cr3t-value-nobody-should-see"
    )
    out = sources.redact(message, CREDENTIAL)
    check("s3cr3t-value-nobody-should-see" not in out, out)
    check("tankpriser-client" not in out, out)
    check(sources.redact(message, None) == message, "no credential, no change")


# -- how the rest of the integration sees it --------------------------------
def test_the_provider_is_declared_as_needing_a_key() -> None:
    provider = sources.PROVIDERS["unox"]
    check(provider.needs_credential, "must be declared as needing a credential")
    check(provider.auth.mode == sources.AUTH_OAUTH, provider.auth.mode)
    check(provider.auth.headers(CREDENTIAL) == {}, "nothing to put in a header")
    check(provider.auth.params(CREDENTIAL) == {}, "and nothing in the query string")
    check(not provider.needs_area, "answers for the whole country at once")
    check(provider.country == const.COUNTRY_DK, provider.country)
    check(provider.signup_url.startswith("https://unoxmobility.dk"), provider.signup_url)
    check("client_id:client_secret" in provider.guide, "the guide must say the format")
    check("30" in provider.guide, "the guide should mention the rate limit")
    check(not provider.experimental, "verified against the live feed 2026-09-21")


def test_no_key_means_this_chain_is_skipped_not_an_error() -> None:
    """Someone who never asks Uno-X for a key still has six other Danish
    chains, and a refresh that raised here would take all of them down."""
    keyed = {p.key for p in sources.providers_needing_credential()}
    check("unox" in keyed, "listed as keyed")
    check("ok" not in keyed and "circlek" not in keyed, "open chains stay open")


def test_denmark_is_still_readable_without_any_key() -> None:
    """Uno-X is approved by a human and can take days. Adding it must not make
    a Danish entry impossible to set up in the meantime — which it would, if
    Denmark were treated the way Germany is."""
    open_dk = [p for p in sources.providers_for(const.COUNTRY_DK) if not p.needs_credential]
    check(len(open_dk) >= 5, f"{len(open_dk)} open Danish chains")
    only_source = [p for p in sources.providers_for(const.COUNTRY_DE)]
    check(all(p.needs_credential for p in only_source), "Germany really does need one")


def test_it_offers_no_fuel_it_cannot_price() -> None:
    """A fuel in the picker that no price arrives for is a sensor that sits
    unavailable for ever."""
    provider = sources.PROVIDERS["unox"]
    check(
        provider.fuels == frozenset({"blyfri92", "blyfri95", "oktan100", "diesel"}),
        str(provider.fuels),
    )
    for key in provider.fuels:
        check(key in const.FUEL_TYPES, f"{key} is not a modelled fuel")
        check(key in sources.fuel_types_for(const.COUNTRY_DK), f"{key} not offered for DK")


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

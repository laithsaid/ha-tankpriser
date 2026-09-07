"""Fuel-price providers.

A provider is one country's per-station price API, normalized into a common
``Station`` record and cached briefly so that many readers share one fetch.

Providers come in two shapes, because the sources do:

* ``SCOPE_NATIONAL`` — the whole country in one response (every Danish chain,
  which the 2026 price-transparency law made free and keyless). We fetch it
  once and the coordinator filters it by postnummer afterwards.
* ``SCOPE_AREA`` — the API only answers about a circle you name, so the area
  is an *input* to the fetch (Germany's Tankerkoenig, capped at 25 km). There
  is no nationwide response to filter, and each distinct circle is its own
  cache entry and its own request against the provider's rate limit.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Final

import aiohttp

from .const import (
    CHAINS,
    COUNTRY_DE,
    COUNTRY_DK,
    DEFAULT_FUEL_TYPES,
    DEFAULT_RADIUS,
    FUEL_TYPES,
    MAX_DISCOUNT_ORE,
    MAX_STALE_AGE,
    OIL_FUELTYPES,
    OIL_URL,
    OK_URL,
    PROVIDER_CACHE_TTL,
    Q8_URL,
    RADIUS_OPTIONS,
    REQUEST_HEADERS,
    SHELL_URL,
    TANKERKOENIG_MAX_RADIUS_KM,
    TANKERKOENIG_URL,
    radius_to_metres,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_POSTNR_RE = re.compile(r"\b(\d{4})\b")


@dataclass
class Station:
    """A single fuel station and its prices, normalized across providers."""

    name: str
    company: str
    postnummer: str
    updated: str
    city: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    coord_approx: bool = False
    # normalized fuel key -> price (kr., float). This is the price you PAY:
    # once a discount is configured for the chain it is already subtracted here,
    # so every consumer — cheapest-of, notifications, the card — agrees without
    # having to know about discounts.
    prices: dict[str, float] = field(default_factory=dict)
    # The pump price, kept so the card can show "16,99 -> 16,79". Empty when no
    # discount applies, i.e. when it would be identical to `prices`.
    list_prices: dict[str, float] = field(default_factory=dict)
    # Discount actually applied, in øre/L (0 = none).
    discount_ore: int = 0
    # The provider's own id for this station, when it publishes one. Preferred
    # for identity: two forecourts of the same brand can share a postal code,
    # and in Germany they routinely do.
    station_id: str = ""
    # Whether the station is serving right now. None = the provider does not
    # say, which is every Danish chain — absent, not closed.
    is_open: bool | None = None
    # Which country's rules this station is priced under: it decides the
    # currency, the decimals shown and whether a loyalty discount in øre could
    # possibly apply. Set by the parser, never guessed downstream.
    country: str = COUNTRY_DK

    @property
    def key(self) -> str:
        """Stable identity for change detection / de-duplication."""
        if self.station_id:
            return f"{self.country}|{self.station_id}".lower()
        return f"{self.company}|{self.name}|{self.postnummer}".lower()


# -- provider product -> normalized fuel key --------------------------------
# Q8/F24 identify products by a stable numeric id; Shell by product name.
_Q8_PRODUCT_MAP: dict[str, str] = {
    "2": "blyfri95",       # GoEasy 95 E10
    "1": "blyfri95plus",   # GoEasy 95 Extra E5
    "6": "diesel",         # GoEasy Diesel
    "8": "dieselplus",     # GoEasy Diesel Extra
    "5": "hvo100",         # Neste MY (HVO100)
    # skipped: "14" AdBlue, "9" HPC (EV charging)
}
_SHELL_PRODUCT_MAP: dict[str, str] = {
    "Blyfri 95": "blyfri95",
    "V-Power": "blyfri98",
    "FuelSave Diesel": "diesel",
    "V-Power Diesel": "dieselplus",
}
_OK_PRODUCT_MAP: dict[str, str] = {
    "Blyfri 95": "blyfri95",
    "Oktan 100": "oktan100",
    "Svovlfri Diesel": "diesel",
}


def chain_key(company: str) -> str | None:
    """Map a station's free-text company to a chain key from ``CHAINS``.

    Providers spell themselves inconsistently ("Q8 Service", "OK Plus",
    "Shell/7-Eleven"), and one endpoint serves two chains (Q8 and F24), so the
    provider that fetched a station cannot answer this — only its own text can.
    """
    text = company or ""
    for key, _label, pattern in CHAINS:
        if re.search(pattern, text, re.I):
            return key
    return None


def apply_discounts(
    stations: list[Station], discounts: dict[str, int]
) -> list[Station]:
    """Return stations priced as *you* pay them, given per-chain øre discounts.

    New objects, never mutation: the parsed stations live in the shared provider
    cache, so subtracting in place would compound the discount on every refresh
    and leak one area's loyalty card into another's prices.
    """
    if not discounts:
        return stations

    out: list[Station] = []
    for station in stations:
        if station.discount_ore:
            # Already priced for this driver. Only reachable if a caller hands
            # us its own output, but subtracting twice would quietly invent a
            # price no pump ever charged, so refuse rather than trust callers.
            out.append(station)
            continue
        if station.country != COUNTRY_DK:
            # Discounts are configured in øre off a Danish pump price. A German
            # Shell would otherwise match the "shell" pattern and have 20 øre
            # subtracted from a euro price — a number no pump ever charged.
            out.append(station)
            continue
        key = chain_key(station.company)
        ore = int(discounts.get(key) or 0) if key else 0
        ore = max(0, min(ore, MAX_DISCOUNT_ORE))
        if not ore or not station.prices:
            out.append(station)
            continue
        krone = ore / 100.0
        out.append(
            replace(
                station,
                # A discount can never make fuel free; round to the øre so the
                # numbers stay printable.
                # Three decimals, not two: Danish prices carry two, so this is
                # a no-op for them, but rounding is a lossy step and hard-coding
                # a country's precision here is how a 1,719 becomes a 1,72.
                prices={
                    fuel: round(max(0.01, price - krone), 3)
                    for fuel, price in station.prices.items()
                },
                list_prices=dict(station.prices),
                discount_ore=ore,
            )
        )
    return out


def without_hidden(stations: list[Station], hidden: set[str]) -> list[Station]:
    """Drop the stations whose names the user hid.

    Matched the way the options dialog stores them — the whole name, case- and
    space-insensitively — because that dialog offers the names it discovered,
    so an exact match is what a user picking from it will get.
    """
    if not hidden:
        return stations
    return [s for s in stations if s.name.strip().lower() not in hidden]


def _to_float(value) -> float | None:
    """Coerce a provider price (number or string) to float, else None."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _short_date(iso: str | None) -> str:
    """Trim an ISO timestamp to its date part for display."""
    if not iso:
        return ""
    return str(iso).split("T", 1)[0]


def _extract_postnummer(text: str) -> str:
    """Return the last 4-digit group in an address (the Danish postnummer)."""
    matches = _POSTNR_RE.findall(text or "")
    return matches[-1] if matches else ""


# -- Q8 + F24 ---------------------------------------------------------------
def parse_q8(payload: dict) -> list[Station]:
    """Parse the shared Q8/F24 GetStationPrices payload."""
    stations: list[Station] = []
    records = (payload or {}).get("data", {}).get("stationsPrices", []) or []
    for rec in records:
        address = str(rec.get("address", "")).strip()
        postnummer = _extract_postnummer(address)
        if not postnummer:
            continue

        brand = str(rec.get("stationName", "")).strip() or "Q8"
        # Human-friendly location: address minus the trailing "<zip> Danmark".
        location = re.sub(r"\s*\b\d{4}\b\s+Danmark\s*$", "", address).strip()

        prices: dict[str, float] = {}
        newest = ""
        for product in rec.get("products", []) or []:
            key = _Q8_PRODUCT_MAP.get(str(product.get("productId")))
            if key is None:
                continue
            price = _to_float(product.get("price"))
            if price is None:
                continue
            prices[key] = price
            changed = _short_date(product.get("priceChangeDate"))
            newest = max(newest, changed)

        if not prices:
            continue

        stations.append(
            Station(
                name=f"{brand} {location}".strip(),
                company=brand,
                postnummer=postnummer,
                address=location,
                updated=newest,
                prices=prices,
            )
        )
    return stations


# -- Shell ------------------------------------------------------------------
def parse_shell(payload: list) -> list[Station]:
    """Parse the Shell prices array (ships exact coordinates)."""
    stations: list[Station] = []
    for rec in payload or []:
        postnummer = str(rec.get("postalCode", "")).strip()
        if not postnummer:
            continue

        street = str(rec.get("street", "")).strip()
        house = str(rec.get("houseNumber") or "").strip()
        city = str(rec.get("city", "")).strip()
        location = " ".join(p for p in (street, house) if p).strip()

        lat = lon = None
        coords = rec.get("coordinates") or {}
        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))

        prices: dict[str, float] = {}
        newest = ""
        for product in rec.get("prices", []) or []:
            key = _SHELL_PRODUCT_MAP.get(str(product.get("productName")).strip())
            if key is None:
                continue
            price = _to_float(product.get("price"))
            if price is None:
                continue
            prices[key] = price
            newest = max(newest, _short_date(product.get("lastUpdated")))

        if not prices:
            continue

        brand = str(rec.get("brand", "Shell")).strip() or "Shell"
        stations.append(
            Station(
                name=f"{brand} {location}".strip() or brand,
                company=brand,
                postnummer=postnummer,
                city=city,
                address=location,
                latitude=lat,
                longitude=lon,
                updated=newest,
                prices=prices,
            )
        )
    return stations


# -- OK ---------------------------------------------------------------------
def parse_ok(payload: dict) -> list[Station]:
    """Parse the OK fuel-prices payload (ships exact coordinates)."""
    stations: list[Station] = []
    for rec in (payload or {}).get("items", []) or []:
        postnummer = str(rec.get("postal_code", "")).strip()
        if not postnummer:
            continue

        street = str(rec.get("street", "")).strip()
        house = str(rec.get("house_number") or "").strip()
        city = str(rec.get("city", "")).strip()
        location = " ".join(p for p in (street, house) if p).strip()

        coords = rec.get("coordinates") or {}
        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))

        prices: dict[str, float] = {}
        for product in rec.get("prices", []) or []:
            key = _OK_PRODUCT_MAP.get(str(product.get("product_name")).strip())
            if key is None:
                continue
            price = _to_float(product.get("price"))
            if price is not None:
                prices[key] = price

        if not prices:
            continue

        stations.append(
            Station(
                name=f"OK {location}".strip() or "OK",
                company="OK",
                postnummer=postnummer,
                city=city,
                address=location,
                latitude=lat,
                longitude=lon,
                updated=_short_date(rec.get("last_updated_time")),
                prices=prices,
            )
        )
    return stations


# -- OIL! -------------------------------------------------------------------
def _parse_gps(gps: str) -> tuple[float | None, float | None]:
    """Parse OIL!'s '55.2739 N, 9.9074 E' into (lat, lon)."""
    lat = lon = None
    for part in str(gps or "").split(","):
        tokens = part.strip().split()
        if len(tokens) != 2:
            continue
        value = _to_float(tokens[0])
        if value is None:
            continue
        hemi = tokens[1].upper()
        if hemi in ("N", "S"):
            lat = -value if hemi == "S" else value
        elif hemi in ("E", "W"):
            lon = -value if hemi == "W" else value
    return lat, lon


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    extra_headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
) -> object:
    """GET one JSON document.

    Query parameters are passed separately rather than formatted into `url`, so
    a credential among them is escaped correctly and stays out of any string we
    build ourselves. It still reaches aiohttp's exception text — see `redact`.
    """
    headers = {**REQUEST_HEADERS, **(extra_headers or {})}
    async with session.get(
        url, headers=headers, params=dict(params or {}), timeout=_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def fetch_oil(
    session: aiohttp.ClientSession,
    credential: str | None = None,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch OIL!: one request per sold fuel type, merged by station_id."""
    merged: dict[str, Station] = {}
    for fueltype, key in OIL_FUELTYPES.items():
        payload = await _fetch_json(session, f"{OIL_URL}?fuelType={fueltype}")
        for rec in payload or []:
            price = _to_float(rec.get(fueltype))
            if price is None:
                continue
            sid = str(rec.get("station_id"))
            station = merged.get(sid)
            if station is None:
                address = str(rec.get("address", "")).strip()
                postnummer = _extract_postnummer(address)
                if not postnummer:
                    continue
                lat, lon = _parse_gps(rec.get("gps"))
                station = Station(
                    name=str(rec.get("station_name", "OIL!")).strip() or "OIL!",
                    company="OIL!",
                    postnummer=postnummer,
                    address=address,
                    latitude=lat,
                    longitude=lon,
                    updated=_short_date(rec.get("updated")),
                    prices={},
                )
                merged[sid] = station
            station.prices[key] = price
    return list(merged.values())


# -- Tankerkoenig (Germany) -------------------------------------------------
# The free consumer feed of the Bundeskartellamt's MTS-K, to which every German
# station must report a price change within five minutes. Three fuels, exact
# coordinates on every record, and a hard 25 km cap on each query — hence
# SCOPE_AREA. Licensed CC BY 4.0; the attribution lives in the README.
_TK_PRODUCT_MAP: dict[str, str] = {
    "e10": "blyfri95",       # Super E10 — the same 95/E10 sold in Denmark
    "e5": "blyfri95plus",    # Super E5
    "diesel": "diesel",
}


def _postcode_de(value) -> str:
    """Normalize a German postal code to five digits.

    Tankerkoenig sends ``postCode`` as a *number*, so every code east of about
    Dresden loses its leading zero on the wire: 01067 arrives as 1067.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return f"{int(text):05d}" if text.isdigit() else text


def parse_tankerkoenig(payload: dict) -> list[Station]:
    """Parse a Tankerkoenig ``list.php`` response into stations."""
    stations: list[Station] = []
    for rec in (payload or {}).get("stations", []) or []:
        prices: dict[str, float] = {}
        for field_name, key in _TK_PRODUCT_MAP.items():
            price = _to_float(rec.get(field_name))
            # A fuel the station does not sell comes back as `false` or null,
            # and a reporting gap as 0.0 — neither is a price you could pay.
            if price is None or price <= 0:
                continue
            prices[key] = price
        if not prices:
            continue

        brand = str(rec.get("brand") or "").strip()
        # Independents report an empty brand; their `name` is all they have.
        label = brand or str(rec.get("name") or "").strip() or "Tankstelle"
        street = str(rec.get("street") or "").strip()
        house = str(rec.get("houseNumber") or "").strip()
        location = " ".join(part for part in (street, house) if part).strip()

        stations.append(
            Station(
                name=f"{label} {location}".strip() or label,
                company=label,
                postnummer=_postcode_de(rec.get("postCode")),
                city=str(rec.get("place") or "").strip(),
                address=location,
                latitude=_to_float(rec.get("lat")),
                longitude=_to_float(rec.get("lng")),
                # list.php carries no timestamp. Reporting is mandatory within
                # five minutes of a change, so "now" is nearer the truth than
                # any date we could invent here.
                updated="",
                prices=prices,
                station_id=str(rec.get("id") or ""),
                is_open=rec.get("isOpen") if isinstance(rec.get("isOpen"), bool) else None,
                country=COUNTRY_DE,
            )
        )
    return stations


# -- provider registry ------------------------------------------------------
# Adding a source is meant to be a *data* change: append one Provider below and
# write its parser. Everything else — the options dialog, the how-to text, the
# credential storage, validation and diagnostics redaction — is driven from
# these fields, so no UI or translation edits are needed.

AUTH_NONE: Final = "none"
# The credential travels in a header. Preferred: headers stay out of URLs, and
# URLs reach logs, proxies and error strings.
AUTH_KEY: Final = "key"
# The credential travels in the query string, because the API accepts it
# nowhere else (Tankerkoenig). Everything logged on this path must go through
# `redact` first — see `_fetch_provider`.
AUTH_QUERY: Final = "query"

# How much of a country one fetch covers.
SCOPE_NATIONAL: Final = "national"   # one response holds every station
SCOPE_AREA: Final = "area"           # the API only answers about a circle


class ProviderAuthError(Exception):
    """The provider refused the credential.

    Separate from a transport failure so the options dialog can say "that key
    is wrong" instead of "cannot connect" — which matters most for a key that
    is merely *not activated yet*, the state every new Tankerkoenig key starts
    in and the one a user is most likely to be staring at.
    """


@dataclass(frozen=True)
class Area:
    """The circle an area-scoped provider is asked about."""

    latitude: float
    longitude: float
    radius_m: int

    @property
    def radius_km(self) -> float:
        return self.radius_m / 1000.0

    @property
    def cache_key(self) -> str:
        """Cache identity: the circle rounded onto a ~1 km grid.

        Rounded, because the positions we search from are live GPS fixes and an
        exact key would miss on every one of them — against a budget of about
        one request a minute, a cache that never hits is no cache at all. The
        cost is that the answer may be centred up to ~1 km from where you
        asked, which can only matter for a station sitting exactly on the rim
        of the radius.
        """
        return f"{self.latitude:.2f}/{self.longitude:.2f}/{self.radius_m}"


@dataclass(frozen=True)
class Auth:
    """How one provider wants its credential presented."""

    mode: str = AUTH_NONE
    # Header shape, for AUTH_KEY. Per-provider because every API differs:
    # Azure APIM wants Ocp-Apim-Subscription-Key, most others want a Bearer.
    header: str = "Authorization"
    template: str = "Bearer {key}"
    # Query parameter name, for AUTH_QUERY.
    param: str = "apikey"

    @property
    def required(self) -> bool:
        return self.mode != AUTH_NONE

    def headers(self, credential: str | None) -> dict[str, str]:
        """Auth headers for a request (empty unless this source uses them)."""
        if not credential or self.mode != AUTH_KEY:
            return {}
        return {self.header: self.template.format(key=credential)}

    def params(self, credential: str | None) -> dict[str, str]:
        """Auth query parameters (empty unless this source uses them)."""
        if not credential or self.mode != AUTH_QUERY:
            return {}
        return {self.param: credential}


AUTH_OPEN: Final = Auth()
# Tankerkoenig takes the key only as `?apikey=` — no header form exists.
AUTH_TANKERKOENIG: Final = Auth(AUTH_QUERY, param="apikey")


def redact(text: object, credential: str | None) -> str:
    """Return ``text`` with the credential blanked out.

    Anything derived from an AUTH_QUERY request can carry the key: aiohttp puts
    the full URL into its exception strings, so ``str(err)`` alone would leak a
    user's key into the Home Assistant log the first time the network hiccups.
    """
    out = str(text)
    if credential and len(credential) >= 8:
        out = out.replace(credential, "***")
    return out


@dataclass(frozen=True)
class Provider:
    """One country's fuel-price API."""

    key: str
    name: str
    # async (session, credential | None, area | None) -> list[Station]
    fetch: Callable[
        [aiohttp.ClientSession, str | None, "Area | None"],
        Awaitable[list[Station]],
    ]
    country: str = COUNTRY_DK
    scope: str = SCOPE_NATIONAL
    auth: Auth = AUTH_OPEN
    # The normalized fuel keys this source can ever return — its product map,
    # not a hand-kept list. The options dialog offers the union of these for a
    # country, so it can never offer a fuel that no price will arrive for.
    fuels: frozenset = frozenset()
    # Largest radius the API will answer (km); 0 means it has no such limit.
    # The options dialog uses it to stop offering radii the source cannot serve.
    max_radius_km: int = 0
    # A circle known to contain stations, used to test a credential when the
    # user's own location is in another country — the dialog is asking "is this
    # key valid", not "is there fuel near you".
    probe: "Area | None" = None
    # Shown in the options dialog. `guide` is markdown; keep it to numbered
    # steps that tell the user exactly where to click.
    signup_url: str = ""
    guide: str = ""
    # True while the parser is written against documentation rather than a
    # real response, so the UI can warn instead of silently returning nothing.
    experimental: bool = False

    @property
    def needs_credential(self) -> bool:
        """Whether this source refuses to answer without a key."""
        return self.auth.required

    @property
    def needs_area(self) -> bool:
        """Whether a fetch is meaningless without a circle to ask about."""
        return self.scope == SCOPE_AREA


def _one_shot(url: str, parser, auth: Auth = AUTH_OPEN):
    """Fetcher: one GET returning a payload the parser turns into Stations."""
    async def _fetch(
        session: aiohttp.ClientSession,
        credential: str | None = None,
        area: "Area | None" = None,
    ) -> list[Station]:
        return parser(
            await _fetch_json(
                session,
                url,
                extra_headers=auth.headers(credential),
                params=auth.params(credential),
            )
        )
    return _fetch


async def fetch_tankerkoenig(
    session: aiohttp.ClientSession,
    credential: str | None = None,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch every station in one circle from Tankerkoenig.

    One request per circle, and the circle is capped at 25 km by the API — see
    ``TANKERKOENIG_MAX_RADIUS_KM``. Failures arrive as HTTP 200 with
    ``ok: false``, so the status code alone would report every one of them as a
    success and the parser would simply find no stations.
    """
    if area is None:
        raise ValueError("Tankerkoenig can only be asked about an area")
    radius_km = min(area.radius_km, TANKERKOENIG_MAX_RADIUS_KM)
    payload = await _fetch_json(
        session,
        TANKERKOENIG_URL,
        params={
            "lat": f"{area.latitude:.6f}",
            "lng": f"{area.longitude:.6f}",
            "rad": f"{radius_km:g}",
            "sort": "dist",
            "type": "all",
            **AUTH_TANKERKOENIG.params(credential),
        },
    )
    if not isinstance(payload, dict):
        raise ValueError("Tankerkoenig returned an unexpected payload")
    if not payload.get("ok"):
        message = str(payload.get("message") or "unknown error")
        # "Key existiert nicht oder ist deaktiviert" is what a brand-new key
        # says until a human at Tankerkoenig activates it, so this branch is
        # the normal first experience rather than an edge case.
        if "key" in message.lower():
            raise ProviderAuthError(message)
        raise ValueError(f"Tankerkoenig: {message}")
    return parse_tankerkoenig(payload)


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            "ok",
            "OK",
            _one_shot(OK_URL, parse_ok),
            fuels=frozenset(_OK_PRODUCT_MAP.values()),
        ),
        Provider(
            "q8",
            "Q8 / F24",
            _one_shot(Q8_URL, parse_q8),
            fuels=frozenset(_Q8_PRODUCT_MAP.values()),
        ),
        Provider(
            "shell",
            "Shell",
            _one_shot(SHELL_URL, parse_shell),
            fuels=frozenset(_SHELL_PRODUCT_MAP.values()),
        ),
        Provider("oil", "OIL!", fetch_oil, fuels=frozenset(OIL_FUELTYPES.values())),
        # Denmark: chains that require a personal credential go here once we
        # have one to test against — e.g. Go'on (apply at goon.nu), Circle
        # K/INGO (fueldkapi@circlekeurope.com) and Uno-X (bearer token). Each
        # needs only auth=Auth(AUTH_KEY, ...), signup_url and guide text.
        Provider(
            "tankerkoenig",
            "Tankerkönig (MTS-K)",
            fetch_tankerkoenig,
            country=COUNTRY_DE,
            scope=SCOPE_AREA,
            auth=AUTH_TANKERKOENIG,
            fuels=frozenset(_TK_PRODUCT_MAP.values()),
            max_radius_km=TANKERKOENIG_MAX_RADIUS_KM,
            # Berlin Mitte: somewhere a valid key is guaranteed to find fuel.
            probe=Area(52.5200, 13.4050, 5_000),
            signup_url="https://creativecommons.tankerkoenig.de/#register",
            guide=(
                "1. Open the signup page and enter your name and e-mail.\n"
                "2. Confirm that you are not an oil company, a station "
                "operator or an IT supplier to either — they are barred from "
                "this data.\n"
                "3. The key arrives by e-mail but does **not** work yet: "
                "someone at Tankerkönig activates it by hand, which can take "
                "days.\n"
                "4. Paste it here once the activation mail arrives. If it is "
                "refused, it is almost always still waiting for that."
            ),
        ),
    )
}


def providers_needing_credential() -> list[Provider]:
    """Sources the user must supply a key for, in display order."""
    return [p for p in PROVIDERS.values() if p.needs_credential]


def providers_for(country: str) -> list[Provider]:
    """Every provider serving one country, in display order."""
    return [p for p in PROVIDERS.values() if p.country == country]


def country_needs_area(country: str) -> bool:
    """Whether this country can only be asked about a circle.

    True for Germany and false for Denmark, but stated as a question about the
    *sources* rather than a list of countries, so adding Austria (also
    area-scoped) needs no second place to remember.
    """
    providers = providers_for(country)
    return bool(providers) and all(p.needs_area for p in providers)


def max_radius_km(country: str) -> int:
    """The tightest radius ceiling among a country's sources (0 = none)."""
    caps = [p.max_radius_km for p in providers_for(country) if p.max_radius_km]
    return min(caps) if caps else 0


def fuel_types_for(country: str) -> list[str]:
    """The fuels a country's sources can actually price, in display order.

    Derived from the sources rather than listed per country, so a country can
    never offer a fuel in its dialog that no price will ever arrive for — the
    sensor for it would simply sit unavailable forever.
    """
    available: set[str] = set()
    for provider in providers_for(country):
        available |= provider.fuels
    return [key for key in FUEL_TYPES if key in available]


def default_fuel_types(country: str) -> list[str]:
    """Sensible pre-ticked fuels: petrol and diesel, where they are sold."""
    available = fuel_types_for(country)
    chosen = [key for key in DEFAULT_FUEL_TYPES if key in available]
    return chosen or available[:1]


def radius_options(country: str) -> list[str]:
    """Radii this country's sources can serve, trimmed to their ceiling.

    Offering 50 km where the source caps at 25 would quietly answer a smaller
    circle than the one the user chose.
    """
    cap_km = max_radius_km(country)
    if not cap_km:
        return list(RADIUS_OPTIONS)
    return [r for r in RADIUS_OPTIONS if radius_to_metres(r) <= cap_km * 1000]


def default_radius(country: str) -> str:
    """The radius to start with.

    Where the source charges one request per circle whatever its size, that is
    the largest circle it will serve — a smaller one saves nothing and finds
    less. Elsewhere it is the familiar 10 km.
    """
    options = radius_options(country)
    if country_needs_area(country) and options:
        return options[-1]
    return DEFAULT_RADIUS if DEFAULT_RADIUS in options else options[-1]


def area_for(country: str, latitude: float, longitude: float, radius_m: int) -> Area:
    """The circle to actually ask about: what was wanted, capped at what the
    sources will serve.

    Capped here rather than only inside the fetch, so that the radius we cache
    under and the radius we could quote back to the user are the one the source
    really answered. Asking for 50 km and being handed 25 twice would otherwise
    look like two different questions.
    """
    cap_km = max_radius_km(country)
    if cap_km:
        radius_m = min(radius_m, cap_km * 1000)
    return Area(latitude, longitude, radius_m)


# "provider@area" -> (fetched_at_monotonic, stations, credential_fingerprint).
# National providers use an empty area part, so there is exactly one entry for
# them; an area provider gets one entry per circle asked about.
_CACHE: dict[str, tuple[float, list[Station], str]] = {}
# One lock per provider, not per circle: it also serializes two different
# circles of the same source, which is what keeps a rate-limited API from
# seeing a burst it never agreed to.
_LOCKS: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in PROVIDERS}


def _cache_key(provider: Provider, area: "Area | None") -> str:
    """Cache slot for one provider's answer about one circle."""
    return f"{provider.key}@{area.cache_key if area else ''}"


def _fingerprint(credential: str | None) -> str:
    """Short digest of a credential, so the cache can tell when it changed
    without holding a second copy of the secret."""
    if not credential:
        return ""
    return hashlib.sha256(credential.encode()).hexdigest()[:16]


def invalidate_cache(key: str | None = None) -> None:
    """Drop cached responses (all, or one provider) — e.g. after a key change,
    so a corrected credential takes effect immediately instead of after the
    10-minute TTL. For an area provider this drops every circle it holds."""
    if key is None:
        _CACHE.clear()
        return
    for cached in [k for k in _CACHE if k.split("@", 1)[0] == key]:
        _CACHE.pop(cached, None)


async def _fetch_provider(
    session: aiohttp.ClientSession,
    provider: Provider,
    credential: str | None = None,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch one provider, honouring the shared TTL cache."""
    cache_key = _cache_key(provider, area)
    async with _LOCKS[provider.key]:
        cached = _CACHE.get(cache_key)
        fresh = cached and (time.monotonic() - cached[0]) < PROVIDER_CACHE_TTL
        if fresh and cached[2] == _fingerprint(credential):
            return cached[1]
        try:
            stations = await provider.fetch(session, credential, area)
            _CACHE[cache_key] = (
                time.monotonic(),
                stations,
                _fingerprint(credential),
            )
            _LOGGER.debug(
                "Fetched %d stations from %s", len(stations), provider.key
            )
            return stations
        # TimeoutError must be listed explicitly: aiohttp raises the builtin
        # (an OSError), which is neither a ClientError nor a ValueError, so
        # without it one slow chain would abort the whole refresh instead of
        # degrading to the other chains. ProviderAuthError is caught for the
        # same reason — a key that expired costs its own source, not all of
        # them — and every message goes through `redact` first, because an
        # AUTH_QUERY failure carries the key inside the URL it reports.
        except (
            aiohttp.ClientError,
            ValueError,
            TimeoutError,
            ProviderAuthError,
        ) as err:
            reason = redact(err, credential)
            if cached is None:
                _LOGGER.warning("Provider %s failed: %s", provider.key, reason)
                return []
            age = time.monotonic() - cached[0]
            if age > MAX_STALE_AGE:
                # Serving prices this old is worse than serving none: the user
                # cannot tell they are stale, and may drive to a bad price.
                _LOGGER.warning(
                    "Provider %s failed and its cached data is %.0f min old; "
                    "dropping it: %s",
                    provider.key,
                    age / 60,
                    reason,
                )
                _CACHE.pop(cache_key, None)
                return []
            _LOGGER.warning(
                "Provider %s failed, using cached data from %.0f min ago: %s",
                provider.key,
                age / 60,
                reason,
            )
            return cached[1]


async def fetch_all(
    session: aiohttp.ClientSession,
    credentials: Mapping[str, str] | None = None,
    country: str = COUNTRY_DK,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch every usable provider for one country and combine the stations.

    Sources that need a credential are skipped silently when none is
    configured, so an unconfigured source simply contributes nothing. So are
    area-scoped sources when no area is given: there is no "everything" for
    them to return, and inventing a circle would spend one of a small number of
    permitted requests on a place nobody asked about.
    """
    creds = credentials or {}
    active = [
        p
        for p in providers_for(country)
        if (not p.needs_credential or creds.get(p.key))
        and (not p.needs_area or area is not None)
    ]
    results = await asyncio.gather(
        *(
            _fetch_provider(
                session, p, creds.get(p.key), area if p.needs_area else None
            )
            for p in active
        )
    )
    combined: list[Station] = []
    for stations in results:
        combined.extend(stations)
    return combined


async def validate_credential(
    session: aiohttp.ClientSession,
    key: str,
    credential: str,
    area: "Area | None" = None,
) -> int:
    """Try a credential and return the station count it yields.

    Raises the underlying aiohttp/ValueError/ProviderAuthError so the config
    flow can tell "rejected" from "unreachable". Bypasses the cache: the point
    is to test *this* key right now.
    """
    provider = PROVIDERS[key]
    invalidate_cache(key)
    probe = (area or provider.probe) if provider.needs_area else None
    stations = await provider.fetch(session, credential, probe)
    return len(stations)

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
import base64
import hashlib
import logging
import re
import ssl
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
    ANWB_BOXES,
    ANWB_ISO3,
    ANWB_URL,
    CIRCLEK_HEADERS,
    CIRCLEK_URL,
    COUNTRY_AT,
    COUNTRY_BE,
    COUNTRY_ES,
    COUNTRY_FR,
    COUNTRY_LU,
    COUNTRY_NL,
    MITECO_CIPHERS,
    MITECO_TIMEOUT_S,
    MITECO_URL,
    PRIX_CARBURANTS_FIELDS,
    PRIX_CARBURANTS_URL,
    GOON_URL,
    Q8_URL,
    RADIUS_OPTIONS,
    REQUEST_HEADERS,
    SHELL_URL,
    ECONTROL_URL,
    TANKERKOENIG_MAX_RADIUS_KM,
    TANKERKOENIG_URL,
    UNOX_TOKEN_MARGIN_S,
    UNOX_TOKEN_URL,
    UNOX_URL,
    radius_to_metres,
)
from .nearby import haversine_m

_LOGGER = logging.getLogger(__name__)


def _ssl_with_ciphers(ciphers: str) -> "ssl.SSLContext | None":
    """A verifying TLS context offering one particular cipher list.

    Built here, at import, rather than per request: creating a context reads
    the system trust store from disk, which is exactly the blocking call that
    does not belong in Home Assistant's event loop. `None` on failure, which
    the caller reads as "use the default" — a server we cannot reach is a
    better outcome than an integration that will not load.
    """
    try:
        context = ssl.create_default_context()
        context.set_ciphers(ciphers)
        return context
    except (ssl.SSLError, OSError):  # pragma: no cover - environment-specific
        _LOGGER.warning("Could not build a TLS context for ciphers %s", ciphers)
        return None


# See MITECO_CIPHERS: the Spanish register resets OpenSSL's default hello.
_MITECO_SSL = _ssl_with_ciphers(MITECO_CIPHERS)

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
# Circle K and INGO share one feed and one catalogue, keyed by a numeric code.
# Map by `code`, never by `displayName`: the same product arrives as "MILES 95"
# at one site and "miles 95" at the next, and "Benzin 95" and "Blyfri 95" are
# one code with two spellings.
#
# Both brands sell an everyday 95 and a premium 95 under their own names; the
# premium ones keep the 95 in their name, so they are the E5 "Extra" grade
# rather than a 98. Three codes mean ordinary diesel because the catalogue has
# never been tidied — see `parse_circlek` for what happens when two of them
# turn up at one forecourt.
# ANWB names a fuel the same way in both countries, so one map serves the
# Netherlands and Belgium. AUTOGAS is LPG and is sold by the litre; CNG is not
# (see FUEL_QUANTITY) and is kept out of every litre comparison.
_ANWB_PRODUCT_MAP: dict[str, str] = {
    "EURO95": "blyfri95",
    "EURO98": "blyfri98",
    "DIESEL": "diesel",
    "DIESEL_SPECIAL": "dieselplus",
    "AUTOGAS": "lpg",
    "CNG": "cng",
}
_GOON_PRODUCT_MAP: dict[str, str] = {
    "Blyfri 92": "blyfri92",
    "Blyfri 95": "blyfri95",
    "Diesel": "diesel",
}
# Uno-X names its products the way the pump does. Matched case-insensitively
# with the spacing collapsed. Verified against the live feed 2026-09-21: four
# product names across 279 forecourts, and these are all of them.
_UNOX_PRODUCT_MAP: dict[str, str] = {
    "blyfri 95 e10": "blyfri95",
    "blyfri 95": "blyfri95",
    # One station sells it — Terndrup, 9575 — and it is 3 øre under the 95 at
    # the same pumps, so folded into `blyfri95` it would win that forecourt's
    # ranking outright. See `blyfri92` in FUEL_TYPES; Go'on was not the only
    # chain selling it after all.
    "blyfri 92": "blyfri92",
    "blyfri 100 e5": "oktan100",
    "blyfri 100": "oktan100",
    "diesel": "diesel",
    "diesel b7": "diesel",
    "hvo100": "hvo100",
    "hvo 100": "hvo100",
}
# Fallback for petrol only. Uno-X publishes `octane` as its own field for every
# benzin product — the documentation lists it among the four things the API is
# there to deliver — so a pump renamed on the forecourt still lands on the right
# fuel instead of vanishing. That is not hypothetical: the first live run found
# a product the documentation never mentions, "Blyfri 92", and this is what
# placed it correctly. There is no equivalent for diesel: "Diesel" and a premium
# diesel share a fuelType and nothing else tells them apart, so folding the two
# together would have one silently overwrite the other where both are sold.
_UNOX_OCTANE_MAP: dict[str, str] = {
    "92": "blyfri92",
    "95": "blyfri95",
    "98": "blyfri98",
    "100": "oktan100",
}
_CIRCLEK_PRODUCT_MAP: dict[str, str] = {
    "1030921": "blyfri95",      # miles 95 (Circle K)
    "592327": "blyfri95",       # Benzin 95 / Blyfri 95 (INGO)
    "1030941": "blyfri95plus",  # miles+ 95
    "1030971": "blyfri95plus",  # UPGRADE 95 (INGO)
    "1030928": "diesel",        # miles diesel
    "797325": "diesel",         # Diesel (INGO)
    "1030946": "diesel",        # Diesel
    "1030876": "dieselplus",    # miles+ diesel
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
    """Trim a timestamp to its date part for display.

    The separator is not always a ``T``: Uno-X writes ``2025-11-26 11:21:13``
    with a space, which splitting on ``T`` alone would leave whole — and a
    station's `updated` is compared with ``max()`` against its siblings, so one
    stray clock time would sort as the newest thing on the forecourt.
    """
    if not iso:
        return ""
    return str(iso).strip().replace("T", " ").split(" ", 1)[0]


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


# -- ANWB (Netherlands, Belgium, Luxembourg) --------------------------------------------
def parse_anwb(payload: dict, country: str) -> list[Station]:
    """Parse one ANWB bounding-box answer, keeping only `country`'s stations.

    The box is geography, not a border: a Dutch box reaches into Germany and
    Belgium, and those stations are somebody else's source to price. Keeping
    them would put two prices of different ages on the same forecourt.

    Two things this feed does that no other one does:

    * **It quotes prices of zero.** 226 of them in the Netherlands as this was
      written — Texaco, Total Express, a Shell. A zero is not a cheap price, it
      is a missing one, and it would win every ranking and send somebody to a
      pump that is not selling.
    * **It carries no timestamp at all.** `updated` is left empty rather than
      filled with the time we happened to fetch, which would look like a price
      age and is not one.
    """
    wanted = ANWB_ISO3.get(country, "")
    stations: list[Station] = []
    for rec in (payload or {}).get("value", []) or []:
        address = rec.get("address") or {}
        if str(address.get("iso3CountryCode", "")).upper() != wanted:
            continue

        coords = rec.get("coordinates") or {}
        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))
        if lat is None or lon is None:
            continue

        prices: dict[str, float] = {}
        for product in rec.get("prices", []) or []:
            key = _ANWB_PRODUCT_MAP.get(str(product.get("fuelType", "")).strip())
            if key is None:
                continue
            price = _to_float(product.get("value"))
            if price is None or price <= 0:
                continue
            prices[key] = price

        if not prices:
            continue

        name = str(rec.get("title", "")).strip()
        stations.append(
            Station(
                name=name,
                # ANWB gives no separate brand, and the title is how the sign
                # reads ("Shell Souburg", "TinQ Emmen"), so the first word is
                # the chain — which is all `chain_key` ever looks at.
                company=name.split(" ")[0] if name else "",
                postnummer=str(address.get("postalCode", "")).strip(),
                city=str(address.get("city", "")).strip(),
                address=str(address.get("streetAddress", "")).strip(),
                latitude=lat,
                longitude=lon,
                updated="",
                prices=prices,
                station_id=str(rec.get("id", "")).strip(),
                country=country,
            )
        )
    return stations


def anwb_fetcher(country: str):
    """Fetcher for one country: one box, asked for whole and cached.

    Every country still read from ANWB fits inside one box — see
    ``ANWB_MAX_BOX_DEG`` for the limit and what happens above it. France did
    not fit and used to be asked about a circle here instead; it now has its
    own national source and this fetcher has one shape again.
    """
    box = ANWB_BOXES[country]

    async def _fetch(
        session: aiohttp.ClientSession,
        credential: str | None = None,
        area: "Area | None" = None,
    ) -> list[Station]:
        payload = await _fetch_json(
            session,
            ANWB_URL,
            params={
                "type-filter": "FUEL_STATION",
                "bounding-box-filter": ",".join(f"{v:g}" for v in box),
            },
        )
        return parse_anwb(payload, country)

    return _fetch


# -- Go'on ------------------------------------------------------------------
def parse_goon(payload: dict) -> list[Station]:
    """Parse the Go'on pump-price payload (ships exact coordinates).

    The only Danish source behind a personal key, and the only one selling 92
    octane — see `blyfri92` in `FUEL_TYPES` for why that is its own fuel and
    not a cheap Blyfri 95.
    """
    stations: list[Station] = []
    for rec in (payload or {}).get("stations", []) or []:
        postnummer = str(rec.get("postalCode", "")).strip()
        if not postnummer:
            continue

        street = str(rec.get("street", "")).strip()
        house = str(rec.get("houseNumber") or "").strip()
        location = " ".join(p for p in (street, house) if p).strip()
        city = str(rec.get("city", "")).strip()

        coords = rec.get("coordinates") or {}
        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))

        prices: dict[str, float] = {}
        newest = ""
        for product in rec.get("prices", []) or []:
            key = _GOON_PRODUCT_MAP.get(str(product.get("productName", "")).strip())
            if key is None:
                continue
            price = _to_float(product.get("price"))
            if price is None:
                continue
            prices[key] = price
            newest = max(newest, _short_date(product.get("lastUpdated")))

        if not prices:
            continue

        brand = str(rec.get("brand", "")).strip() or "Go'on"
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
                station_id=str(rec.get("stationId", "")).strip(),
            )
        )
    return stations


# -- Uno-X ------------------------------------------------------------------
# The only source that will not take a credential at all: Uno-X wants OAuth 2.0
# client credentials, so the key is exchanged for a 900-second JWT before every
# fetch and the JWT is what the data call carries. See `fetch_unox`.
def parse_unox(payload: dict) -> list[Station]:
    """Parse the Uno-X pump-price payload (ships exact coordinates).

    Two shapes here are unique among our sources and both would fail quietly:

    * the coordinates are **strings with a Danish decimal comma** —
      ``"55,568269773969"`` — which ``float()`` rejects outright, so every
      station would fall back to its postnummer instead of standing where it is;
    * ``lastUpdated`` separates date from time with a **space**, not a ``T``.
      Handled in `_short_date`, which every source now shares.
    """
    # The live feed answers `{success, data, dataCount, metaData}` — lowercase
    # `data`, although the documentation's table calls it `Data`. Both are read,
    # since one letter's case is not worth an empty chain either way.
    records = (payload or {}).get("data")
    if records is None:
        records = (payload or {}).get("Data")
    stations: list[Station] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        address = rec.get("address") or {}
        postnummer = str(address.get("postalCode") or "").strip()
        if not postnummer:
            continue

        coords = address.get("coordinates") or {}
        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))

        prices: dict[str, float] = {}
        newest = ""
        for product in rec.get("products") or []:
            if not isinstance(product, dict):
                continue
            name = " ".join(str(product.get("productName") or "").split()).lower()
            key = _UNOX_PRODUCT_MAP.get(name)
            if key is None and str(product.get("fuelType") or "").strip().lower() in (
                "benzin",
                "petrol",
            ):
                key = _UNOX_OCTANE_MAP.get(str(product.get("octane") or "").strip())
            if key is None:
                continue
            price = _to_float(product.get("price"))
            # A product listed without a price is a pump that has not reported,
            # not a free litre.
            if price is None or price <= 0:
                continue
            prices[key] = price
            newest = max(newest, _short_date(product.get("lastUpdated")))

        if not prices:
            continue

        brand = str(rec.get("brand") or "").strip() or "Uno-X"
        # `stationName` is the forecourt's own name ("Fredericia Vejlevej") and
        # says more than the street line does; the address is the fallback.
        label = str(rec.get("stationName") or "").strip()
        # `fullAddress` live, `addressHouseNumber` in the documentation. Reading
        # only the documented name cost every station its street line, and
        # silently: `stationName` covers the label, so the names looked right
        # while `address` — what the card prints under the name, and what a
        # navigator is handed — was empty at all 279 forecourts.
        location = str(
            address.get("fullAddress") or address.get("addressHouseNumber") or ""
        ).strip()
        stations.append(
            Station(
                name=f"{brand} {label or location}".strip(),
                company=brand,
                postnummer=postnummer,
                city=str(address.get("city") or "").strip(),
                address=location,
                latitude=lat,
                longitude=lon,
                updated=newest,
                prices=prices,
                station_id=str(rec.get("stationId") or "").strip(),
            )
        )
    return stations


# One live token per credential, shared by every fetch. Keyed by fingerprint,
# so the secret is not held a second time — see `_fingerprint`.
# fingerprint -> (token, valid_until_monotonic)
_UNOX_TOKENS: dict[str, tuple[str, float]] = {}


def _unox_basic(credential: str) -> str:
    """Turn a stored "client_id:client_secret" into a Basic auth header.

    One field holding both halves, written exactly as the official
    documentation writes it for ``curl -u``. A pair with a half missing is a
    credential error and not a transport one: telling someone who pasted only
    the client id that we "cannot connect" would send them to their router.
    """
    client_id, separator, client_secret = str(credential or "").strip().partition(":")
    if not separator or not client_id.strip() or not client_secret.strip():
        raise ProviderAuthError(
            "Uno-X needs both halves of the key, written as client_id:client_secret"
        )
    pair = f"{client_id.strip()}:{client_secret.strip()}".encode()
    return "Basic " + base64.b64encode(pair).decode()


async def _unox_token(session: aiohttp.ClientSession, credential: str) -> str:
    """Return a live bearer token, fetching one only when the last has aged out.

    Worth caching for more than politeness: Uno-X allows **one request per key
    per 30 seconds**, and a token fetched per refresh would spend half of that
    budget re-asking for a token that was still good.
    """
    fingerprint = _fingerprint(credential)
    cached = _UNOX_TOKENS.get(fingerprint)
    if cached and time.monotonic() < cached[1]:
        return cached[0]
    _UNOX_TOKENS.pop(fingerprint, None)

    async with session.post(
        UNOX_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={**REQUEST_HEADERS, "Authorization": _unox_basic(credential)},
        timeout=_TIMEOUT,
    ) as resp:
        # Keycloak answers an unknown client_id with 401 and a wrong secret with
        # 400 `invalid_client`. Both mean the same thing to whoever is looking
        # at the dialog, and neither is something a retry will fix.
        if resp.status in (400, 401, 403):
            raise ProviderAuthError(
                f"Uno-X refused the client credentials (HTTP {resp.status})"
            )
        resp.raise_for_status()
        payload = await resp.json(content_type=None)

    token = str((payload or {}).get("access_token") or "").strip()
    if not token:
        raise ProviderAuthError("Uno-X returned no access token")
    lifetime = _to_float((payload or {}).get("expires_in")) or 900.0
    _UNOX_TOKENS[fingerprint] = (
        token,
        time.monotonic() + max(lifetime - UNOX_TOKEN_MARGIN_S, 0.0),
    )
    return token


async def fetch_unox(
    session: aiohttp.ClientSession,
    credential: str | None = None,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch every Uno-X station: a token first, then the one data call."""
    if not credential:
        raise ProviderAuthError("Uno-X needs a client_id:client_secret key")
    token = await _unox_token(session, credential)
    try:
        payload = await _fetch_json(
            session, UNOX_URL, extra_headers={"Authorization": f"Bearer {token}"}
        )
    except aiohttp.ClientResponseError as err:
        if getattr(err, "status", None) in (401, 403):
            # Refused although our clock says it is still good: the key was
            # revoked, or the two clocks disagree. Drop it, so the next refresh
            # asks for a new token instead of replaying this one every ten
            # minutes until somebody notices.
            _UNOX_TOKENS.pop(_fingerprint(credential), None)
            raise ProviderAuthError("Uno-X refused the access token") from err
        raise
    return parse_unox(payload)


# -- Circle K / INGO --------------------------------------------------------
def parse_circlek(payload: dict) -> list[Station]:
    """Parse the shared Circle K / INGO country feed (no coordinates).

    Two brands arrive in one list and are told apart by the site name, which is
    what `chain_key` and the card's icons both key off — and what a discount is
    configured against, so a station that called itself the wrong thing would be
    priced with somebody else's loyalty card.
    """
    stations: list[Station] = []
    for rec in (payload or {}).get("sites", []) or []:
        address = rec.get("address") or {}
        postnummer = str(address.get("postalCode", "")).strip()
        if not postnummer:
            continue

        raw_name = str(rec.get("name", "")).strip()
        brand = "INGO" if raw_name.upper().startswith("INGO") else "Circle K"
        street = str(address.get("street", "")).strip()
        city = str(address.get("city", "")).strip()

        prices: dict[str, float] = {}
        newest = ""
        for product in rec.get("fuelPrices", []) or []:
            key = _CIRCLEK_PRODUCT_MAP.get(str(product.get("code", "")).strip())
            if key is None:
                continue
            price = _to_float(product.get("price"))
            if price is None:
                continue
            # Three codes mean ordinary diesel and a forecourt often lists two
            # of them. Every one of the 207 sites doing that today quotes the
            # same figure twice, so this is a tie-break and not a claim — but
            # if they ever diverge, the lower one is the one a driver can act
            # on, and it can never promise a price no pump is charging.
            if key not in prices or price < prices[key]:
                prices[key] = price
            newest = max(newest, _short_date(product.get("lastUpdated")))

        if not prices:
            continue

        stations.append(
            Station(
                name=f"{brand} {street}".strip() or brand,
                company=brand,
                postnummer=postnummer,
                city=city,
                address=street,
                updated=newest,
                prices=prices,
                # Two forecourts of one brand share a postal code often enough
                # here — the motorway pairs — so identity comes from their id.
                station_id=str(rec.get("id", "")).strip(),
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
    timeout_s: float | None = None,
    ssl_context: "ssl.SSLContext | None" = None,
) -> object:
    """GET one JSON document.

    Query parameters are passed separately rather than formatted into `url`, so
    a credential among them is escaped correctly and stays out of any string we
    build ourselves. It still reaches aiohttp's exception text — see `redact`.

    `timeout_s` is for the one source that needs longer than the shared 30
    seconds: Spain answers with 12 MB and does not gzip it, which is about ten
    seconds on a good line and several times that on a bad one.
    """
    headers = {**REQUEST_HEADERS, **(extra_headers or {})}
    timeout = _TIMEOUT if timeout_s is None else aiohttp.ClientTimeout(total=timeout_s)
    # Only passed when a source needs its own — see `_ssl_with_ciphers`. The
    # default is aiohttp's, which is what every other source uses.
    extra = {"ssl": ssl_context} if ssl_context is not None else {}
    async with session.get(
        url, headers=headers, params=dict(params or {}), timeout=timeout, **extra
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



# -- E-Control (Austria) ----------------------------------------------------
# The regulator's own Spritpreisrechner feed, behind the official app. Keyless,
# and area-scoped for a reason no radius can fix: see ECONTROL_MAX_RESULTS.
#
# Austria publishes exactly three fuels. There is no Super 98 and no premium
# diesel in this feed, so neither is offered for an Austrian entry — the
# options dialog builds its list from `Provider.fuels`, which is this map.
_AT_PRODUCT_MAP: dict[str, str] = {
    "SUP": "blyfri95",   # "Super 95" — the same 95 sold as E10 elsewhere
    "DIE": "diesel",
    "GAS": "cng",        # per kilogram, like the Dutch feed's CNG
}


def parse_econtrol(payload, fuel_key: str) -> list[Station]:
    """Parse one E-Control response — one fuel's worth of stations.

    About half of the ten records carry no price at all: E-Control lists the
    forecourt whether or not it has reported for that fuel, and an unreported
    fuel arrives as an empty ``prices`` list rather than as a zero. Those are
    dropped here. It is not a fault and not a closure — verified live, the
    priceless records come back open — it is simply a station that has not
    reported, and a station with no price is nothing we can rank.
    """
    stations: list[Station] = []
    for rec in payload or []:
        if not isinstance(rec, dict):
            continue
        price: float | None = None
        for entry in rec.get("prices") or []:
            if str(entry.get("fuelType") or "").strip().upper() != fuel_key:
                continue
            price = _to_float(entry.get("amount"))
            break
        if price is None or price <= 0:
            continue

        location = rec.get("location") or {}
        address = str(location.get("address") or "").strip()
        # E-Control publishes no brand field, only the forecourt's own name.
        # For the chains that is the brand already ("BP", "Shell Austria");
        # for an independent it is all there is ("TANKEnergie Ringgarage").
        label = str(rec.get("name") or "").strip() or "Tankstelle"

        stations.append(
            Station(
                name=f"{label} {address}".strip() or label,
                company=label,
                postnummer=str(location.get("postalCode") or "").strip(),
                city=str(location.get("city") or "").strip(),
                address=address,
                latitude=_to_float(location.get("latitude")),
                longitude=_to_float(location.get("longitude")),
                # No timestamp anywhere in the record — not per price, not per
                # station. Same silence as ANWB; better shown as nothing than
                # as a time we invented.
                updated="",
                prices={_AT_PRODUCT_MAP[fuel_key]: price},
                station_id=str(rec.get("id") or ""),
                is_open=rec.get("open") if isinstance(rec.get("open"), bool) else None,
                country=COUNTRY_AT,
            )
        )
    return stations


async def fetch_econtrol(
    session: aiohttp.ClientSession,
    credential: str | None = None,
    area: "Area | None" = None,
) -> list[Station]:
    """Fetch every Austrian fuel around one point, merged by station id.

    One request per fuel, because a repeated ``fuelType`` is accepted and then
    silently answered for the first one only — so three requests per circle,
    against a source that costs nothing and caps itself at ten stations. The
    shared provider cache is what keeps a corridor of three circles from
    spending nine of them twice in the same minute.

    The circle is advisory. Nothing in the request says how far to look, so
    the radius the user chose can only be applied to what comes back, which
    the coordinator does for every area-scoped source alike.
    """
    if area is None:
        raise ValueError("E-Control can only be asked about an area")
    merged: dict[str, Station] = {}
    for fuel_key in _AT_PRODUCT_MAP:
        payload = await _fetch_json(
            session,
            ECONTROL_URL,
            params={
                "latitude": f"{area.latitude:.6f}",
                "longitude": f"{area.longitude:.6f}",
                "fuelType": fuel_key,
                # Open forecourts only, and this one is not a preference.
                # With ten slots to spend, a closed station takes the place of
                # one you could drive to: asked about Vienna at closing time,
                # `includeClosed=true` answered with four shut forecourts and
                # one open at 2,309, while `false` answered 2,195 — a station
                # the first list did not mention at all. Germany can afford to
                # carry closed stations because its circle is not rationed.
                "includeClosed": "false",
            },
        )
        if not isinstance(payload, list):
            raise ValueError("E-Control returned an unexpected payload")
        for station in parse_econtrol(payload, fuel_key):
            # The one source whose answer has to be cut down afterwards. Every
            # other area-scoped provider is told the radius and honours it, so
            # the coordinator can take what arrives as "what is in range";
            # E-Control is told nothing and widens until it has ten, which
            # reaches a long way for a fuel with few pumps. Asked about Graz it
            # offered a CNG station in Wiener Neustadt, 150 km off. Without
            # this, a 5 km Austrian area would list it.
            if station.latitude is None or station.longitude is None:
                continue
            away = haversine_m(
                area.latitude, area.longitude, station.latitude, station.longitude
            )
            if away > area.radius_m:
                continue
            existing = merged.get(station.key)
            if existing is None:
                merged[station.key] = station
                continue
            existing.prices.update(station.prices)
    return list(merged.values())

# -- prix-carburants (France) -----------------------------------------------
# The French government's own instantaneous feed: every forecourt open to the
# public, nationwide, in one request. See PRIX_CARBURANTS_URL for why the
# export endpoint and why the column list.
#
# The columns are flat — one price and one timestamp per fuel — which is the
# opposite of every other source here and much easier to read. What it costs is
# that a fuel is a *column name*, so this map is read against the record rather
# than against a list of products.
_FR_PRODUCT_MAP: dict[str, str] = {
    # The everyday French 95 is the E10, sold at about 6,800 forecourts; the
    # older E5 blend is still beside it at 2,800 and is the dearer of the two,
    # which is why it takes the "plus" key rather than the plain one.
    "e10": "blyfri95",
    "sp95": "blyfri95plus",
    "sp98": "blyfri98",
    "gazole": "diesel",
    "e85": "e85",
    "gplc": "lpg",
}


def parse_prix_carburants(payload: list) -> list[Station]:
    """Parse the French national export into stations.

    Three things worth knowing, each of which would fail quietly:

    * **Read `geom`, not `latitude`.** The `latitude` column is an integer in
      hundred-thousandths of a degree — 48.183 arrives as ``4818300`` — so a
      parser that trusted the name would put every French forecourt several
      thousand degrees north of the pole. `geom` is the same position as a
      proper ``{lat, lon}`` pair.
    * **There is no brand.** The dataset has no enseigne column and no second
      dataset carries one, so `company` is left empty rather than guessed at
      from the address. The card shows such a station in grey with no chain
      icon, which is honest; inventing "TOTAL" from a street name is not.
    * **A price of zero is a missing price**, as it is everywhere else.
    """
    stations: list[Station] = []
    for rec in payload or []:
        if not isinstance(rec, dict):
            continue
        geom = rec.get("geom") or {}
        lat = _to_float(geom.get("lat"))
        lon = _to_float(geom.get("lon"))
        if lat is None or lon is None:
            continue

        prices: dict[str, float] = {}
        newest = ""
        for column, key in _FR_PRODUCT_MAP.items():
            price = _to_float(rec.get(f"{column}_prix"))
            if price is None or price <= 0:
                continue
            prices[key] = price
            newest = max(newest, _short_date(rec.get(f"{column}_maj")))

        if not prices:
            continue

        address = str(rec.get("adresse") or "").strip()
        city = str(rec.get("ville") or "").strip()
        stations.append(
            # The street and the town, because that is all France publishes to
            # name a forecourt with. Both, not just the street: "84 route de
            # Maillot" is a dozen different places and "84 route de Maillot,
            # Sens" is one.
            Station(
                name=", ".join(part for part in (address, city) if part),
                company="",
                postnummer=str(rec.get("cp") or "").strip(),
                city=city,
                address=address,
                latitude=lat,
                longitude=lon,
                updated=newest,
                prices=prices,
                station_id=str(rec.get("id") or "").strip(),
                country=COUNTRY_FR,
            )
        )
    return stations


# -- MITECO (Spain) ---------------------------------------------------------
# The Spanish register of retail fuel prices, kept by the Ministry for the
# Ecological Transition and reported to by every forecourt selling to the
# public. Keyless, and the whole country in one response — see MITECO_URL for
# the two things about that response that bite.
#
# Keyed by the column name, which is a Spanish sentence with an accent in it.
# Spain publishes more products than anyone: the ones left out are AdBlue and
# hydrogen (not motor fuel we model), Gasoleo B (agricultural red diesel, which
# it is an offence to burn on the road), and the biofuel blends sold at a few
# dozen forecourts, none of which is the same thing as the HVO we do model.
_ES_PRODUCT_MAP: dict[str, str] = {
    "Precio Gasolina 95 E5": "blyfri95",
    "Precio Gasolina 95 E5 Premium": "blyfri95plus",
    "Precio Gasolina 98 E5": "blyfri98",
    "Precio Gasoleo A": "diesel",
    "Precio Gasoleo Premium": "dieselplus",
    "Precio Diésel Renovable": "hvo100",
    "Precio Gases licuados del petróleo": "lpg",
    # Per kilogram, like the Dutch and Austrian CNG — see FUEL_QUANTITY.
    "Precio Gas Natural Comprimido": "cng",
}


def parse_miteco(payload: dict) -> list[Station]:
    """Parse the Spanish national price register into stations.

    Everything numeric here is a string with a decimal comma, coordinates
    included, which `_to_float` already handles. Everything absent is an empty
    string rather than null, which it also handles: a station that does not
    sell a fuel has ``""`` in that column, not a zero.
    """
    stations: list[Station] = []
    for rec in (payload or {}).get("ListaEESSPrecio", []) or []:
        if not isinstance(rec, dict):
            continue
        lat = _to_float(rec.get("Latitud"))
        lon = _to_float(rec.get("Longitud (WGS84)"))
        if lat is None or lon is None:
            continue

        prices: dict[str, float] = {}
        for column, key in _ES_PRODUCT_MAP.items():
            price = _to_float(rec.get(column))
            if price is None or price <= 0:
                continue
            prices[key] = price

        if not prices:
            continue

        # `Rótulo` is the sign over the forecourt — REPSOL, CEPSA, BALLENOIL —
        # and an unbranded station signs itself with its own licence number,
        # which is as much of a name as it has.
        brand = " ".join(str(rec.get("Rótulo") or "").split()).strip()
        address = str(rec.get("Dirección") or "").strip()
        city = str(rec.get("Municipio") or "").strip()
        stations.append(
            Station(
                name=f"{brand} {address}".strip() or address or brand,
                company=brand,
                postnummer=str(rec.get("C.P.") or "").strip(),
                city=city,
                address=address,
                latitude=lat,
                longitude=lon,
                # One `Fecha` covers the whole extract and says when the list
                # was built, not when a price moved. Shown as nothing rather
                # than as today's date on a price that may be a week old.
                updated="",
                prices=prices,
                station_id=str(rec.get("IDEESS") or "").strip(),
                country=COUNTRY_ES,
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
# The credential is never sent at all: it is a client_id/secret pair traded for
# a short-lived token first, and the token is what travels (Uno-X). `headers`
# and `params` stay empty for this mode — the provider's own fetcher does the
# exchange, because only it knows the token endpoint. Declared as a mode all
# the same, so `needs_credential` is true and the dialog still asks.
AUTH_OAUTH: Final = "oauth"

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
# Go'on wants an ordinary bearer token, which is the Auth default. It answers
# 401 without a header and 403 with a key it does not know; the options dialog
# reads both as "that key is wrong", which is exactly right.
AUTH_GOON: Final = Auth(AUTH_KEY)
# Uno-X: OAuth 2.0 client credentials — see `fetch_unox`.
AUTH_UNOX: Final = Auth(AUTH_OAUTH)


def redact(text: object, credential: str | None) -> str:
    """Return ``text`` with the credential blanked out.

    Anything derived from an AUTH_QUERY request can carry the key: aiohttp puts
    the full URL into its exception strings, so ``str(err)`` alone would leak a
    user's key into the Home Assistant log the first time the network hiccups.
    """
    out = str(text)
    if not credential:
        return out
    # Also each colon-separated part: a client-credentials pair is stored as
    # "client_id:client_secret", and a token endpoint that rejects it reports
    # the id and the secret separately — the joined form it would match on
    # never appears in the error at all.
    for secret in (credential, *credential.split(":")):
        if len(secret) >= 8:
            out = out.replace(secret, "***")
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


def _one_shot(
    url: str,
    parser,
    auth: Auth = AUTH_OPEN,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout_s: float | None = None,
    ssl_context: "ssl.SSLContext | None" = None,
):
    """Fetcher: one GET returning a payload the parser turns into Stations.

    `headers` are constant and public — Circle K wants `X-App-Name: PRICES` from
    everyone and refuses the request without it. `params` are the same kind of
    thing: France's feed wants the list of columns to return, which is not a
    choice a caller makes. A credential goes through `auth` instead, which
    knows never to put it in a URL.
    """
    async def _fetch(
        session: aiohttp.ClientSession,
        credential: str | None = None,
        area: "Area | None" = None,
    ) -> list[Station]:
        return parser(
            await _fetch_json(
                session,
                url,
                extra_headers={**(headers or {}), **auth.headers(credential)},
                params={**(params or {}), **auth.params(credential)},
                timeout_s=timeout_s,
                ssl_context=ssl_context,
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
        Provider(
            "circlek",
            "Circle K / INGO",
            _one_shot(CIRCLEK_URL, parse_circlek, headers=CIRCLEK_HEADERS),
            fuels=frozenset(_CIRCLEK_PRODUCT_MAP.values()),
        ),
        Provider(
            "goon",
            "Go'on",
            _one_shot(GOON_URL, parse_goon, auth=AUTH_GOON),
            auth=AUTH_GOON,
            fuels=frozenset(_GOON_PRODUCT_MAP.values()),
            signup_url="https://goon.nu/faa-adgang-til-api/",
            guide=(
                "1. Open the signup page, enter an e-mail address and submit. "
                "The name/company field is optional.\n"
                "2. The key arrives by return mail within a few minutes — it "
                "is issued automatically, with nobody to wait for. Check spam "
                "if it does not, or write to info@goongruppen.dk.\n"
                "3. Paste it here.\n"
                "4. If saving says it cannot connect, wait half a minute and "
                "try once more: Go'on allows one request per key per 30 "
                "seconds, and testing a key twice in quick succession trips "
                "that limit even when the key is perfectly good."
            ),
        ),
        Provider(
            "unox",
            "Uno-X",
            fetch_unox,
            auth=AUTH_UNOX,
            # What the live feed actually prices, counted 2026-09-21 over 279
            # forecourts: blyfri95 279, diesel 279, oktan100 259, blyfri92 1.
            # Not the documented set — the documentation never mentions 92 —
            # and not the whole octane map either, because a fuel in the picker
            # that no price ever arrives for is a sensor that sits unavailable
            # for ever.
            fuels=frozenset({"blyfri92", "blyfri95", "oktan100", "diesel"}),
            signup_url="https://unoxmobility.dk/privat/braendstofpriser#pris-api",
            guide=(
                "1. Apply on the signup page, or write to info@unox.dk with "
                "the subject *Adgang til pris-API* (phone +45 70 12 56 78, "
                "weekdays 08-16).\n"
                "2. A human at Uno-X has to approve it, so this one does not "
                "arrive by return mail the way Go'on's does.\n"
                "3. What arrives is a **pair**: a client id and a client "
                "secret. Paste them here as one line, joined by a colon — "
                "`client_id:client_secret` — exactly as the Uno-X "
                "documentation writes it for `curl -u`.\n"
                "4. If saving says it cannot connect, wait half a minute and "
                "try once more: Uno-X allows one request per key per 30 "
                "seconds, and testing a key twice in quick succession trips "
                "that limit even when the key is perfectly good."
            ),
        ),
        # Denmark is complete: OK, Q8/F24, Shell, OIL! and Circle K / INGO are
        # open, Go'on and Uno-X are keyed, and every chain the 2026 price
        # transparency law covers is read.
        Provider(
            "anwb_nl",
            "ANWB (Netherlands)",
            anwb_fetcher(COUNTRY_NL),
            country=COUNTRY_NL,
            fuels=frozenset(_ANWB_PRODUCT_MAP.values()),
        ),
        Provider(
            "anwb_be",
            "ANWB (Belgium)",
            anwb_fetcher(COUNTRY_BE),
            country=COUNTRY_BE,
            fuels=frozenset(_ANWB_PRODUCT_MAP.values()),
        ),
        Provider(
            "anwb_lu",
            "ANWB (Luxembourg)",
            anwb_fetcher(COUNTRY_LU),
            country=COUNTRY_LU,
            fuels=frozenset(_ANWB_PRODUCT_MAP.values()),
        ),
        # France answered from ANWB until 0.23.0, a circle at a time, because
        # no single ANWB box covers the country. It has its own national feed
        # now — keyless, the whole country in one request, and the only French
        # source of the two with a timestamp on each price.
        Provider(
            "prix_carburants",
            "Prix des carburants (France)",
            _one_shot(
                PRIX_CARBURANTS_URL,
                parse_prix_carburants,
                params={"select": PRIX_CARBURANTS_FIELDS},
            ),
            country=COUNTRY_FR,
            fuels=frozenset(_FR_PRODUCT_MAP.values()),
        ),
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
        # Austria. One Country, one Provider, a parser — no other file learns
        # that it exists, which is the whole point of the country model.
        Provider(
            "econtrol",
            "E-Control (Spritpreisrechner)",
            fetch_econtrol,
            country=COUNTRY_AT,
            scope=SCOPE_AREA,
            fuels=frozenset(_AT_PRODUCT_MAP.values()),
            # No kilometre ceiling to declare: the cap is ten stations, not a
            # distance, so every radius the dialog offers is servable in the
            # only sense this source understands.
            max_radius_km=0,
            # Vienna Mitte: somewhere Austria is guaranteed to sell fuel.
            probe=Area(48.2082, 16.3738, 5_000),
        ),
        # Spain: the widest single answer we ask anyone for — 11,500 forecourts
        # and 12 MB of them, which is why this one carries a timeout of its own.
        Provider(
            "miteco",
            "MITECO (Spain)",
            _one_shot(
                MITECO_URL,
                parse_miteco,
                timeout_s=MITECO_TIMEOUT_S,
                ssl_context=_MITECO_SSL,
            ),
            country=COUNTRY_ES,
            fuels=frozenset(_ES_PRODUCT_MAP.values()),
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


def stations_within(stations: list[Station], area: "Area") -> list[Station]:
    """The stations that really are inside a circle.

    How a whole-country source is cut down to an area everywhere except
    Denmark, which cuts by postnummer instead — see ``Country.postal_areas``.

    A station the source could not place is dropped rather than kept: it can be
    neither mapped nor ranked by distance, so there is no sense in which it is
    "within" anything, and keeping it would put a station of unknown position
    into a list whose whole claim is that everything in it is in range.
    """
    return [
        station
        for station in stations
        if station.latitude is not None
        and station.longitude is not None
        and haversine_m(
            area.latitude, area.longitude, station.latitude, station.longitude
        )
        <= area.radius_m
    ]


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

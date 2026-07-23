"""Fuel-price providers.

Each provider is a free, no-auth Danish per-station price API (mandated since
2026). We fetch each one nationwide, normalize it into a common ``Station``
record, and cache the result briefly so that many configured areas share a
single fetch per provider. Geographic filtering happens later in the
coordinator using the station's postnummer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

import aiohttp

from .const import (
    OIL_FUELTYPES,
    OIL_URL,
    OK_URL,
    PROVIDER_CACHE_TTL,
    Q8_URL,
    REQUEST_HEADERS,
    SHELL_URL,
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
    # normalized fuel key -> price (kr., float)
    prices: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity for change detection / de-duplication."""
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
) -> object:
    headers = {**REQUEST_HEADERS, **(extra_headers or {})}
    async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def fetch_oil(
    session: aiohttp.ClientSession, credential: str | None = None
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


# -- provider registry ------------------------------------------------------
# Adding a chain is meant to be a *data* change: append one Provider below and
# write its parser. Everything else — the options dialog, the how-to text, the
# credential storage, validation and diagnostics redaction — is driven from
# these fields, so no UI or translation edits are needed.

AUTH_NONE: Final = "none"
AUTH_KEY: Final = "key"


@dataclass(frozen=True)
class Provider:
    """One fuel chain's price API."""

    key: str
    name: str
    # async (session, credential | None) -> list[Station]
    fetch: Callable[
        [aiohttp.ClientSession, str | None], Awaitable[list[Station]]
    ]
    auth: str = AUTH_NONE
    # How the credential is sent. Both are per-chain because every API differs:
    # Azure APIM wants Ocp-Apim-Subscription-Key, most others want a Bearer.
    auth_header: str = "Authorization"
    auth_template: str = "Bearer {key}"
    # Shown in the options dialog. `guide` is markdown; keep it to numbered
    # steps that tell the user exactly where to click.
    signup_url: str = ""
    guide: str = ""
    # True while the parser is written against documentation rather than a
    # real response, so the UI can warn instead of silently returning nothing.
    experimental: bool = False

    @property
    def needs_credential(self) -> bool:
        """Whether this chain refuses to answer without a key."""
        return self.auth != AUTH_NONE

    def headers(self, credential: str | None) -> dict[str, str]:
        """Auth headers for a request (never put the key in the URL — the
        provider fetch path logs URLs at debug level)."""
        if not credential or not self.needs_credential:
            return {}
        return {self.auth_header: self.auth_template.format(key=credential)}


def _one_shot(url: str, parser):
    """Fetcher: one GET returning a payload the parser turns into Stations."""
    async def _fetch(
        session: aiohttp.ClientSession, credential: str | None = None
    ) -> list[Station]:
        return parser(await _fetch_json(session, url))
    return _fetch


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider("ok", "OK", _one_shot(OK_URL, parse_ok)),
        Provider("q8", "Q8 / F24", _one_shot(Q8_URL, parse_q8)),
        Provider("shell", "Shell", _one_shot(SHELL_URL, parse_shell)),
        Provider("oil", "OIL!", fetch_oil),
        # Chains that require a personal credential go here once we have one to
        # test against — e.g. Go'on (apply at goon.nu), Circle K/INGO
        # (fueldkapi@circlekeurope.com) and Uno-X (bearer token). Each needs
        # only: auth=AUTH_KEY, the header shape, signup_url and guide text.
    )
}


def providers_needing_credential() -> list[Provider]:
    """Chains the user must supply a key for, in display order."""
    return [p for p in PROVIDERS.values() if p.needs_credential]


# key -> (fetched_at_monotonic, stations, credential_fingerprint)
_CACHE: dict[str, tuple[float, list[Station], str]] = {}
_LOCKS: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in PROVIDERS}


def _fingerprint(credential: str | None) -> str:
    """Short digest of a credential, so the cache can tell when it changed
    without holding a second copy of the secret."""
    if not credential:
        return ""
    return hashlib.sha256(credential.encode()).hexdigest()[:16]


def invalidate_cache(key: str | None = None) -> None:
    """Drop cached responses (all, or one provider) — e.g. after a key change,
    so a corrected credential takes effect immediately instead of after the
    10-minute TTL."""
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


async def _fetch_provider(
    session: aiohttp.ClientSession,
    provider: Provider,
    credential: str | None = None,
) -> list[Station]:
    """Fetch one provider, honouring the shared TTL cache."""
    async with _LOCKS[provider.key]:
        cached = _CACHE.get(provider.key)
        fresh = cached and (time.monotonic() - cached[0]) < PROVIDER_CACHE_TTL
        if fresh and cached[2] == _fingerprint(credential):
            return cached[1]
        try:
            stations = await provider.fetch(session, credential)
            _CACHE[provider.key] = (
                time.monotonic(),
                stations,
                _fingerprint(credential),
            )
            _LOGGER.debug(
                "Fetched %d stations from %s", len(stations), provider.key
            )
            return stations
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.warning("Provider %s failed: %s", provider.key, err)
            # Fall back to stale cache if we have one, else empty.
            return cached[1] if cached else []


async def fetch_all(
    session: aiohttp.ClientSession,
    credentials: Mapping[str, str] | None = None,
) -> list[Station]:
    """Fetch every usable provider concurrently and combine the stations.

    Chains that need a credential are skipped silently when none is configured,
    so an unconfigured chain simply contributes nothing.
    """
    creds = credentials or {}
    active = [
        p
        for p in PROVIDERS.values()
        if not p.needs_credential or creds.get(p.key)
    ]
    results = await asyncio.gather(
        *(_fetch_provider(session, p, creds.get(p.key)) for p in active)
    )
    combined: list[Station] = []
    for stations in results:
        combined.extend(stations)
    return combined


async def validate_credential(
    session: aiohttp.ClientSession, key: str, credential: str
) -> int:
    """Try a credential and return the station count it yields.

    Raises the underlying aiohttp/ValueError so the config flow can tell
    "rejected" (401/403) apart from "unreachable". Bypasses the cache: the
    point is to test *this* key right now.
    """
    provider = PROVIDERS[key]
    invalidate_cache(key)
    stations = await provider.fetch(session, credential)
    return len(stations)

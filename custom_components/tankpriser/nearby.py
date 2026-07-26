"""Ranking stations around a point.

Pure — no Home Assistant imports — so the maths can be unit-tested on its own
(``tests/test_nearby.py``). The caller supplies the stations and the position.

Distance is a haversine on a spherical earth. Over the tens of kilometres this
is ever asked about it differs from the ellipsoidal answer by a few metres,
which cannot change a figure printed as "3,2 km", and it runs over the whole
national list on every GPS fix without noticeable cost.
"""

from __future__ import annotations

import re
from math import asin, cos, degrees, radians, sin, sqrt
from typing import Any, Final, Iterable

from .const import SPOKEN_STATIONS

# Mean earth radius (IUGG), metres.
EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    h = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(min(1.0, h)))


def bounding_box(
    latitude: float, longitude: float, radius_m: float
) -> tuple[float, float, float, float]:
    """A lat/lon box that contains the circle, as (min_lat, max_lat, min_lon, max_lon).

    A cheap rejection test: comparing two floats is far less work than a
    haversine, and the pool this filters is every station in the country.
    """
    lat_delta = degrees(radius_m / EARTH_RADIUS_M)
    # Meridians converge towards the poles, so a degree of longitude is shorter
    # the further north you are. The floor keeps this finite near the poles —
    # unreachable in Denmark, but a divide-by-zero is a poor way to find out.
    shrink = max(cos(radians(latitude)), 0.01)
    lon_delta = lat_delta / shrink
    return (
        latitude - lat_delta,
        latitude + lat_delta,
        longitude - lon_delta,
        longitude + lon_delta,
    )


def rank_nearby(
    stations: Iterable[Any],
    latitude: float,
    longitude: float,
    radius_m: float,
    fuel_key: str,
) -> list[dict]:
    """Every station selling ``fuel_key`` within the radius, cheapest first.

    Deliberately *not* truncated: a caller reporting how many stations are in
    range needs the real count, and the ones that display a list slice it
    themselves.
    """
    min_lat, max_lat, min_lon, max_lon = bounding_box(latitude, longitude, radius_m)
    out: list[dict] = []
    for station in stations:
        lat, lon = station.latitude, station.longitude
        if lat is None or lon is None:
            continue
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        price = station.prices.get(fuel_key)
        if price is None:
            continue
        metres = haversine_m(latitude, longitude, lat, lon)
        if metres > radius_m:
            continue
        out.append(
            {
                "name": station.name,
                "company": station.company,
                "city": station.city,
                "price": price,
                "list_price": station.list_prices.get(fuel_key),
                "discount_ore": station.discount_ore or None,
                "distance_km": round(metres / 1000.0, 1),
                "latitude": lat,
                "longitude": lon,
                # An estimated position must not be handed to a navigator;
                # a caller can skip these or warn.
                "coord_approx": station.coord_approx,
            }
        )
    # Cheapest first — that is the question being asked. Distance breaks a tie,
    # because two stations at the same price are not equally useful.
    out.sort(key=lambda s: (s["price"], s["distance_km"]))
    return out


# Spelled out because "all 3 cost" is read aloud as "all three cost" by some
# voices and "all digit three" by others; the word is unambiguous.
_COUNT_WORDS: Final = {
    True: {2: "to", 3: "tre"},
    False: {2: "two", 3: "three"},
}

# "OK Nordre Ringvej 110" -> "OK Nordre Ringvej". Trailing house number, with an
# optional letter ("12B"), and nothing else — a name ending in a digit that is
# part of the brand ("Circle K 24/7") has no leading space before the number.
_HOUSE_NUMBER: Final = re.compile(r",?\s+\d+\s*[A-Za-z]?$")


def _spoken_place(station: dict) -> str:
    """How one station is named out loud.

    The station name, minus its house number: "one hundred and ten" is three
    syllables that cannot help you choose, and the map action is what actually
    navigates. Falls back to company and city for a source that gave no name —
    ambiguous when a chain has several forecourts in one town, but better than
    a silent gap.
    """
    short = _HOUSE_NUMBER.sub("", station.get("name") or "").strip()
    if short:
        return short
    return " ".join(p for p in (station.get("company"), station.get("city")) if p)


def _number(value: float, danish: bool, decimals: int = 2) -> str:
    """A figure written the way the language reads it aloud.

    Danish wants a decimal comma: "16,79 kroner" is read as sixteen seventy-nine,
    where "16.79" comes out as "sixteen point seven nine".
    """
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if danish else text


def spoken_cheapest(ranked: list[dict], danish: bool) -> str:
    """The single cheapest station as a sentence.

    For the shortcut that asks nothing and simply drives you there: one station,
    named, priced and placed, with no list to hold in your head at 110 km/h.
    """
    if not ranked:
        return "Ingen stationer i nærheden." if danish else "No stations nearby."
    best = ranked[0]
    price = _number(best["price"], danish)
    distance = _number(best["distance_km"], danish, 1)
    place = _spoken_place(best)
    if danish:
        return f"Billigste er {place}, {price} kroner, {distance} kilometer væk."
    return f"The cheapest is {place}, {price} kroner, {distance} kilometres away."


def spoken_sentence(ranked: list[dict], danish: bool) -> str:
    """The cheapest few stations as a sentence, ready to be read aloud.

    Built here rather than left to the user's template so a Siri Shortcut is one
    line instead of a Jinja loop — and so the phrasing is right: Danish wants a
    decimal comma, and "16,79 kroner" read out beats "16.79".

    Module level and pure so it can be tested without Home Assistant.
    """
    if not ranked:
        return "Ingen stationer i nærheden." if danish else "No stations nearby."

    top = ranked[:SPOKEN_STATIONS]
    # A chain often prices every forecourt identically — OK does, nationally —
    # and then repeating the figure per station spends the listener's attention
    # on the one number that never varies. Say it once up front and leave each
    # station with the only thing that does differ: how far away it is.
    same_price = len(top) > 1 and len({s["price"] for s in top}) == 1
    lines: list[str] = []
    if same_price:
        count = _COUNT_WORDS[danish].get(len(top), str(len(top)))
        price = _number(top[0]["price"], danish)
        lines.append(
            f"Alle {count} koster {price} kroner."
            if danish
            else f"All {count} cost {price} kroner."
        )

    label = "Nummer" if danish else "Number"
    unit = "kilometer" if danish else "kilometres"
    for index, station in enumerate(top, start=1):
        distance = f"{_number(station['distance_km'], danish, 1)} {unit}."
        if same_price:
            tail = distance
        else:
            tail = f"{_number(station['price'], danish)} kroner, {distance}"
        lines.append(f"{label} {index}: {_spoken_place(station)}, {tail}")
    return " ".join(lines)

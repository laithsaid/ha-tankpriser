"""Ranking stations around a point.

Pure — no Home Assistant imports — so the maths can be unit-tested on its own
(``tests/test_nearby.py``). The caller supplies the stations and the position.

Distance is a haversine on a spherical earth. Over the tens of kilometres this
is ever asked about it differs from the ellipsoidal answer by a few metres,
which cannot change a figure printed as "3,2 km", and it runs over the whole
national list on every GPS fix without noticeable cost.
"""

from __future__ import annotations

from math import asin, cos, degrees, radians, sin, sqrt
from typing import Any, Iterable

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

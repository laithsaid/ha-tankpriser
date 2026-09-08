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
from dataclasses import dataclass
from math import asin, atan2, cos, degrees, radians, sin, sqrt
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


# --- which way you are going -----------------------------------------------
# Below this you are treated as parked, and the search is a circle around you.
# It is not "stopped": at 20 km/h you are in a town, where a station behind you
# is a two-minute detour and dropping it would be wrong.
MOVING_MIN_KMH: Final = 30.0
# A derived heading needs both of these, or GPS jitter invents a direction: a
# phone sitting still wanders tens of metres, and two such fixes are a bearing.
MIN_FIX_DISTANCE_M: Final = 1500.0
# Past this the older fix says where you were, not where you are going.
MAX_FIX_AGE_S: Final = 900.0
# One hour of driving is the horizon worth searching, floored so a slow road
# still looks ahead and capped so a fast one does not spend requests on places
# you will reach in an hour and a half.
HORIZON_MIN_KM: Final = 40.0
HORIZON_MAX_KM: Final = 135.0
# Most circles a single search may spend, before the rim heuristic.
MAX_CORRIDOR_CIRCLES: Final = 3
# A station further off your heading than this, and not right next to you, is
# behind you. 100 rather than 90 so a station just off a junction survives.
BEHIND_DEG: Final = 100.0
# ...and this is "right next to you", where direction stops mattering.
ALWAYS_KEEP_M: Final = 3000.0
# The cheapest sitting this far out in what was searched suggests the good
# prices continue past the edge, so it is worth one more circle.
RIM_FRACTION: Final = 0.8


@dataclass(frozen=True)
class Motion:
    """How the caller is moving, as far as we can tell."""

    speed_kmh: float = 0.0
    # Compass bearing in degrees, 0 = north. None when we could not work one out.
    course_deg: float | None = None
    # Where the answer came from, for diagnostics and for the tests:
    # "reported" (the tracker said so), "derived" (two fixes), "unknown".
    source: str = "unknown"

    @property
    def moving(self) -> bool:
        """Whether to search ahead rather than around."""
        return self.course_deg is not None and self.speed_kmh >= MOVING_MIN_KMH


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing from the first point to the second, in degrees."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_lambda = radians(lon2 - lon1)
    y = sin(d_lambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(d_lambda)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def destination(
    latitude: float, longitude: float, bearing_deg: float, distance_km: float
) -> tuple[float, float]:
    """The point ``distance_km`` away on the given bearing."""
    angular = (distance_km * 1000.0) / EARTH_RADIUS_M
    phi1, lambda1 = radians(latitude), radians(longitude)
    theta = radians(bearing_deg)
    phi2 = asin(sin(phi1) * cos(angular) + cos(phi1) * sin(angular) * cos(theta))
    lambda2 = lambda1 + atan2(
        sin(theta) * sin(angular) * cos(phi1),
        cos(angular) - sin(phi1) * sin(phi2),
    )
    return degrees(phi2), (degrees(lambda2) + 540.0) % 360.0 - 180.0


def angle_between(a: float, b: float) -> float:
    """The smaller angle between two bearings, 0-180 degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def infer_motion(
    latitude: float,
    longitude: float,
    fix: dict | None = None,
    elapsed_s: float | None = None,
) -> Motion:
    """Work out speed and heading from a fresh position and the previous fix.

    Two sources, best first. A tracker that reports ``speed`` and ``course``
    (the Home Assistant companion app does, while it has a GPS lock) is
    believed outright. Otherwise the fresh position the caller just sent is
    compared against the tracker's last one — which is why the caller sends a
    position at all instead of us reading the tracker: a phone whose app went
    quiet an hour ago would otherwise answer confidently about the town you
    left, and the pair of fixes is exactly what a single stale one cannot give.

    Nothing here reads the clock, so it stays pure and testable; the caller
    passes ``elapsed_s``.
    """
    fix = fix or {}

    reported_speed = _to_float(fix.get("speed"))
    reported_course = _to_float(fix.get("course"))
    # The companion app reports metres per second, and a negative value is its
    # way of saying "no fix", not a reversing car.
    if (
        reported_speed is not None
        and reported_speed >= 0
        and reported_course is not None
        and 0 <= reported_course <= 360
    ):
        return Motion(reported_speed * 3.6, reported_course % 360.0, "reported")

    last_lat = _to_float(fix.get("latitude"))
    last_lon = _to_float(fix.get("longitude"))
    if last_lat is None or last_lon is None or elapsed_s is None:
        return Motion()
    if elapsed_s <= 0 or elapsed_s > MAX_FIX_AGE_S:
        return Motion()

    metres = haversine_m(last_lat, last_lon, latitude, longitude)
    if metres < MIN_FIX_DISTANCE_M:
        # Either genuinely parked, or the two fixes are the same one plus
        # noise. Both mean: search around, not ahead.
        return Motion(metres / 1000.0 / (elapsed_s / 3600.0), None, "derived")
    speed_kmh = (metres / 1000.0) / (elapsed_s / 3600.0)
    return Motion(speed_kmh, initial_bearing(last_lat, last_lon, latitude, longitude), "derived")


def search_plan(
    latitude: float,
    longitude: float,
    motion: Motion,
    radius_km: float,
) -> list[tuple[float, float]]:
    """The circle centres to search, in order.

    Parked, that is one circle where you stand. Moving, it is a corridor of up
    to three circles laid along your heading: at speed, half of a circle around
    you is road you have already driven, while distance *ahead* is nearly free
    — the same tank of fuel gets you there either way.
    """
    if not motion.moving or motion.course_deg is None:
        return [(latitude, longitude)]

    horizon = min(max(motion.speed_kmh, HORIZON_MIN_KM), HORIZON_MAX_KM)
    # The first circle is pulled back a little so it still covers the road
    # immediately behind you; the rest are spaced to overlap slightly rather
    # than leave a gap between them at the seams.
    centres: list[tuple[float, float]] = []
    for index in range(MAX_CORRIDOR_CIRCLES):
        offset = radius_km * 0.8 + index * radius_km * 1.8
        if index and offset - radius_km > horizon:
            break
        centres.append(destination(latitude, longitude, motion.course_deg, offset))
    return centres


def searched_km(
    latitude: float,
    longitude: float,
    centres: list[tuple[float, float]],
    radius_km: float,
) -> float:
    """How far from the caller the search actually reached.

    Said out loud when nothing was found, so it has to be the truth rather than
    the radius that was asked for.
    """
    if not centres:
        return 0.0
    return round(
        max(
            haversine_m(latitude, longitude, lat, lon) / 1000.0 + radius_km
            for lat, lon in centres
        )
    )


def should_extend(ranked: list[dict], reach_km: float) -> bool:
    """Whether the cheapest station sits far enough out to look further.

    "Nothing found" is the wrong trigger on its own: three expensive stations
    is not a reason to look further, and a cheaper one just past the edge stays
    invisible either way. What does suggest more is the *cheapest* one sitting
    at the rim — prices that keep falling as you go usually keep falling.
    """
    if not ranked or reach_km <= 0:
        return False
    return ranked[0]["distance_km"] >= RIM_FRACTION * reach_km


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rank_nearby(
    stations: Iterable[Any],
    latitude: float,
    longitude: float,
    radius_m: float,
    fuel_key: str,
    course_deg: float | None = None,
) -> list[dict]:
    """Every station selling ``fuel_key`` within the radius, cheapest first.

    With a ``course_deg`` the ones behind you are dropped: at speed a station
    you have just passed costs a U-turn and a second one to get back, which no
    price difference on one tank repays. Anything within a few kilometres is
    kept whatever its direction, because that close a "detour" is a couple of
    streets. Denmark benefits from this as much as Germany does.

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
        if (
            course_deg is not None
            and metres > ALWAYS_KEEP_M
            and angle_between(initial_bearing(latitude, longitude, lat, lon), course_deg)
            > BEHIND_DEG
        ):
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


def _nothing_found(danish: bool, searched_km: float | None) -> str:
    """What to say when the search came back empty.

    It names the range it covered. Silence, or a bare "no stations nearby",
    is indistinguishable from the integration having failed — and a shortcut
    that fails without saying so is the failure mode this whole surface was
    built to avoid.
    """
    if not searched_km:
        return "Ingen stationer i nærheden." if danish else "No stations nearby."
    reach = int(round(searched_km))
    if danish:
        return f"Ingen stationer inden for {reach} kilometer."
    return f"No stations within {reach} kilometres."


def spoken_cheapest(
    ranked: list[dict],
    danish: bool,
    currency: str = "kroner",
    searched_km: float | None = None,
) -> str:
    """The single cheapest station as a sentence.

    For the shortcut that asks nothing and simply drives you there: one station,
    named, priced and placed, with no list to hold in your head at 110 km/h.

    The price is said to two decimals wherever it is bought, even where the
    signs carry three: "two euro twenty-six" is what a person says, and the
    third digit is 9/10 of a cent that no one reads out. Ranking and thresholds
    still use the full figure — see the caller.
    """
    if not ranked:
        return _nothing_found(danish, searched_km)
    best = ranked[0]
    price = _number(best["price"], danish)
    distance = _number(best["distance_km"], danish, 1)
    place = _spoken_place(best)
    if danish:
        return f"Billigste er {place}, {price} {currency}, {distance} kilometer væk."
    return (
        f"The cheapest is {place}, {price} {currency}, {distance} kilometres away."
    )


def spoken_sentence(
    ranked: list[dict],
    danish: bool,
    currency: str = "kroner",
    searched_km: float | None = None,
) -> str:
    """The cheapest few stations as a sentence, ready to be read aloud.

    Built here rather than left to the user's template so a Siri Shortcut is one
    line instead of a Jinja loop — and so the phrasing is right: Danish wants a
    decimal comma, and "16,79 kroner" read out beats "16.79".

    Module level and pure so it can be tested without Home Assistant.
    """
    if not ranked:
        return _nothing_found(danish, searched_km)

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
            f"Alle {count} koster {price} {currency}."
            if danish
            else f"All {count} cost {price} {currency}."
        )

    label = "Nummer" if danish else "Number"
    unit = "kilometer" if danish else "kilometres"
    for index, station in enumerate(top, start=1):
        distance = f"{_number(station['distance_km'], danish, 1)} {unit}."
        if same_price:
            tail = distance
        else:
            tail = f"{_number(station['price'], danish)} {currency}, {distance}"
        lines.append(f"{label} {index}: {_spoken_place(station)}, {tail}")

    # A thin answer needs the range too. Two stations could mean the area is
    # empty or that we barely looked, and only one of those is worth driving
    # on for. A full list implies its own range and does not need telling.
    if searched_km and len(ranked) < SPOKEN_STATIONS:
        reach = int(round(searched_km))
        lines.append(
            f"Det var alt inden for {reach} kilometer."
            if danish
            else f"That is everything within {reach} kilometres."
        )
    return " ".join(lines)


def country_for_position(
    candidates: list[tuple[object, object, tuple[float, float] | None]],
    latitude: float,
    longitude: float,
) -> object | None:
    """Which of several configured countries answers for a position.

    `candidates` are `(handle, Country, anchor)`, in configuration order;
    `handle` is whatever the caller wants back — a config entry, a code — and
    `anchor` is that setup's fixed point, or None if it has none.

    The rule, and why: a position is matched against each country's box. With
    Denmark configured first and Germany second, everything used to be
    answered by Denmark, so asking outside Hamburg searched Danish stations
    and said there was nothing within 15 km. Boxes overlap along a shared
    border on purpose — a box tight enough to split Flensburg from Kruså would
    be wrong the moment a road bends — so where two match, the nearer anchor
    wins. Somewhere no configured country claims, the first setup answers,
    which keeps a single-country install behaving exactly as it always did.
    """
    if not candidates:
        return None
    matches = [item for item in candidates if item[1].contains(latitude, longitude)]
    if not matches:
        return candidates[0][0]
    if len(matches) == 1:
        return matches[0][0]

    def _to_anchor(item) -> float:
        anchor = item[2]
        if anchor is None:
            return float("inf")
        return haversine_m(latitude, longitude, anchor[0], anchor[1])

    return min(matches, key=_to_anchor)[0]

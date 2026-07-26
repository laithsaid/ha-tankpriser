"""Tests for nearby.py — the "cheapest stations around a point" ranking.

Run with: python tests/test_nearby.py

Pure maths, no Home Assistant: the module is imported straight from the file.
The case that matters most is the one that shipped wrong — a driver halfway to
the next town being offered the stations at home.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load_nearby import load_nearby  # noqa: E402

nearby = load_nearby()

rank_nearby = nearby.rank_nearby
haversine_m = nearby.haversine_m
bounding_box = nearby.bounding_box


@dataclass
class FakeStation:
    """Only the attributes rank_nearby reads, shaped like sources.Station."""

    name: str
    latitude: float | None
    longitude: float | None
    prices: dict
    company: str = "OK"
    city: str = ""
    list_prices: dict = field(default_factory=dict)
    discount_ore: int | None = None
    coord_approx: bool = False


# Real Danish positions, so the distances can be sanity-checked on a map.
SILKEBORG = (56.1697, 9.5451)
AARHUS = (56.1629, 10.2039)
# Roughly the midpoint of the two, on the E45/A15 corridor.
MIDWAY = (56.166, 9.874)

checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


# -- distance ---------------------------------------------------------------
# Silkeborg to Aarhus is ~41 km as the crow flies.
km = haversine_m(*SILKEBORG, *AARHUS) / 1000
check(40 < km < 42, f"Silkeborg-Aarhus should be ~41 km, got {km:.1f}")
check(haversine_m(56.0, 10.0, 56.0, 10.0) == 0.0, "the same point is zero away")
# Symmetric, and 0.1 km resolution is what the sensor prints.
check(
    round(haversine_m(*SILKEBORG, *AARHUS)) == round(haversine_m(*AARHUS, *SILKEBORG)),
    "distance must not depend on the order of the arguments",
)

# -- bounding box -----------------------------------------------------------
min_lat, max_lat, min_lon, max_lon = bounding_box(56.17, 9.55, 15_000)
check(min_lat < 56.17 < max_lat and min_lon < 9.55 < max_lon, "the box contains its centre")
check(
    max_lon - 9.55 > max_lat - 56.17,
    "a degree of longitude is shorter this far north, so the box is wider in lon",
)
# Nothing inside the circle may fall outside the box, or it would be dropped
# before the distance is ever computed.
for bearing_lat, bearing_lon in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    lat = 56.17 + bearing_lat * 0.134   # ~14.9 km north/south
    lon = 9.55 + bearing_lon * 0.240    # ~14.9 km east/west
    check(
        min_lat <= lat <= max_lat and min_lon <= lon <= max_lon,
        f"a point inside the circle fell outside the box: {lat},{lon}",
    )

# -- ranking ----------------------------------------------------------------
stations = [
    FakeStation("OK Silkeborg", *SILKEBORG, {"blyfri95": 16.79}),
    FakeStation("Q8 Silkeborg", 56.1750, 9.5500, {"blyfri95": 16.99, "diesel": 15.99}),
    FakeStation("Shell Aarhus", *AARHUS, {"blyfri95": 16.49}),
    FakeStation("F24 Midtvejs", 56.1680, 9.8800, {"blyfri95": 16.59}),
    FakeStation("OIL! Nowhere", None, None, {"blyfri95": 15.00}),  # unplaceable
    FakeStation("Circle K", 56.1700, 9.5460, {"diesel": 15.79}),   # no petrol
]

# 1. Standing in Silkeborg: the two Silkeborg stations, cheapest first.
here = rank_nearby(stations, *SILKEBORG, 15_000, "blyfri95")
check([s["name"] for s in here] == ["OK Silkeborg", "Q8 Silkeborg"], f"got {here}")
check(here[0]["distance_km"] == 0.0, "the station you are standing at is 0.0 km away")

# 2. THE BUG: halfway to Aarhus, the Silkeborg stations are no longer nearby.
#    Before the nationwide pool this returned them anyway, because they were the
#    only stations the area sensor knew about.
away = rank_nearby(stations, *MIDWAY, 15_000, "blyfri95")
check([s["name"] for s in away] == ["F24 Midtvejs"], f"got {away}")
check(away[0]["distance_km"] < 1.0, "and it is the one actually beside you")

# 3. A wider radius reaches both towns, still cheapest-first — not nearest.
wide = rank_nearby(stations, *MIDWAY, 40_000, "blyfri95")
check(
    [s["name"] for s in wide] == ["Shell Aarhus", "F24 Midtvejs", "OK Silkeborg", "Q8 Silkeborg"],
    f"cheapest first, got {[s['name'] for s in wide]}",
)

# 4. A station that does not sell the fuel is not a candidate, whatever it costs.
check(all(s["name"] != "Circle K" for s in here), "petrol query must skip a diesel-only site")
diesel = rank_nearby(stations, *SILKEBORG, 15_000, "diesel")
check([s["name"] for s in diesel] == ["Circle K", "Q8 Silkeborg"], f"got {diesel}")

# 5. A station with no coordinates cannot be ranked by distance at all.
check(all(s["name"] != "OIL! Nowhere" for s in wide), "unplaceable station must be dropped")

# 6. Equal prices are broken by distance, so the closer of two identical
#    forecourts is named first — OK prices every station in the country alike.
same = [
    FakeStation("OK Far", 56.2500, 9.5451, {"blyfri95": 16.79}),
    FakeStation("OK Near", 56.1750, 9.5451, {"blyfri95": 16.79}),
]
check(
    [s["name"] for s in rank_nearby(same, *SILKEBORG, 15_000, "blyfri95")]
    == ["OK Near", "OK Far"],
    "a price tie is broken by distance",
)

# 7. The row carries what the card and the Shortcut need, including the flag
#    that stops an estimated pin being handed to a navigator.
approx = FakeStation(
    "F24 Estimeret", 56.1700, 9.5451, {"blyfri95": 17.29},
    company="F24", city="Silkeborg",
    list_prices={"blyfri95": 17.49}, discount_ore=20, coord_approx=True,
)
row = rank_nearby([approx], *SILKEBORG, 15_000, "blyfri95")[0]
check(row["coord_approx"] is True, "approximate positions stay flagged")
check(row["list_price"] == 17.49 and row["discount_ore"] == 20, "pump price and discount survive")
check(row["city"] == "Silkeborg" and row["company"] == "F24", "place and chain survive")

# 8. Nothing in range is an empty list, not an error.
check(rank_nearby(stations, 55.0, 12.0, 5_000, "blyfri95") == [], "empty when far from everything")

print(f"nearby tests passed ({checks} assertions)")

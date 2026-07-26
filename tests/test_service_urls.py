"""The `tankpriser.nearby` service's navigation links.

Run with: python tests/test_service_urls.py

`services.py` imports Home Assistant, so the URL building is lifted out of it
with `ast` and exercised on its own — the same trick the other service-side
tests use. What is being checked is one property, and it is the one that would
send someone to the wrong forecourt: **`urls[i]` must describe `stations[i]`**.

A station whose position is only estimated gets an empty string rather than
being dropped, because dropping it shifts every station after it up one place —
so "number three" would open the fourth station's map, confidently and wrongly.
"""

from __future__ import annotations

import ast
import os

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)

# Pull `_MAPS_URL` out of services.py without importing it.
tree = ast.parse(open(os.path.join(BASE, "services.py"), encoding="utf-8").read())
MAPS = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and getattr(node.targets[0], "id", "") == "_MAPS_URL"
)

checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def urls_for(stations, maps="google"):
    """The service's own list comprehension, kept in step by hand."""
    template = MAPS[maps]
    return [
        "" if s["coord_approx"] else template.format(lat=s["latitude"], lon=s["longitude"])
        for s in stations
    ]


def station(name, lat=56.1697, lon=9.5451, approx=False):
    return {"name": name, "latitude": lat, "longitude": lon, "coord_approx": approx}


# Every map option produces a usable link for an exact position.
exact = station("OK Nordre Ringvej", 56.1697, 9.5451)
for name in ("google", "apple", "osm"):
    url = urls_for([exact], name)[0]
    check(url.startswith("http"), f"{name}: {url}")
    check("56.1697" in url and "9.5451" in url, f"{name} lost the coordinates: {url}")
check(len(MAPS) == 3, f"unexpected map options: {sorted(MAPS)}")

# THE PROPERTY: an estimated station holds its place with an empty string.
ranked = [
    station("OK Nordre Ringvej", 56.1697, 9.5451),
    station("F24 Motorvejen", 55.65, 12.08, approx=True),
    station("Shell Århusvej", 56.18, 9.56),
]
urls = urls_for(ranked)
check(len(urls) == len(ranked), "one url per station, always")
check(urls[1] == "", "an estimated position must not be offered as a destination")
check("56.18" in urls[2], "the station after it keeps its own url, not a borrowed one")
# Spelled out, because this is the failure the empty string exists to prevent:
check(
    "12.08" not in urls[2],
    "urls must not close up around a skipped station — index 3 would open Roskilde",
)

# Nothing nearby: an empty list, not an error.
check(urls_for([]) == [], "no stations, no urls")

# All estimated: all empty, and the caller gets a visible nothing rather than a
# confident wrong turn.
check(
    urls_for([station("F24 A", approx=True), station("F24 B", approx=True)]) == ["", ""],
    "estimated-only results offer no navigation at all",
)

print(f"service url tests passed ({checks} assertions)")

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

# Pull `MAPS_URLS` out of const.py without importing it. It moved there from
# services.py when the fill-up notification started building the same links:
# two copies of a navigation URL is exactly the kind of duplicate that drifts
# until one of them sends you to a route preview instead of turn-by-turn.
tree = ast.parse(open(os.path.join(BASE, "const.py"), encoding="utf-8").read())
MAPS = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.AnnAssign)
    and getattr(node.target, "id", "") == "MAPS_URLS"
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

# Google's link must start navigation, not open a route preview: a preview asks
# for a starting point, which is a dialog to dismiss while driving.
google = urls_for([exact], "google")[0]
check("dir_action=navigate" in google, google)
check("travelmode=driving" in google, google)
# Apple's scheme has its own way of saying the same thing.
check("dirflg=d" in urls_for([exact], "apple")[0], urls_for([exact], "apple")[0])

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


# --- how many stations come back -------------------------------------------
# `NEARBY_MAX_STATIONS` was 8, sized for a sentence in a car, and the map pin
# reused the same call: a pin on Berlin found 343 forecourts, was handed the
# eight *cheapest* of them, and plotted a scatter reaching 22 km out while a
# station 400 m away went unmentioned. The count is a field now. These read
# services.py rather than re-implementing it, because a copy of the rule here
# would go on passing after the caller stopped using it — which is how 0.19.1
# shipped.
src = ast.parse(open(os.path.join(BASE, "services.py"), encoding="utf-8").read())

schema = next(
    node for node in ast.walk(src)
    if isinstance(node, ast.Assign)
    and any(getattr(t, "id", "") == "_NEARBY_SCHEMA" for t in node.targets)
)
schema_src = ast.dump(schema)
check("ATTR_LIMIT" in schema_src, "the service takes no limit field")
check(
    "NEARBY_MAX_STATIONS" in schema_src,
    "the limit's default drifted away from the listed-count constant",
)
check(
    "NEARBY_LIMIT_MAX" in schema_src,
    "the limit has no ceiling, and the list goes into a recorded attribute",
)

answer = next(
    node for node in ast.walk(src)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "nearby_answer"
)
args = [a.arg for a in answer.args.args + answer.args.kwonlyargs]
check("limit" in args, f"nearby_answer does not accept a limit: {args}")

# Every truncation of the ranked list must use the argument. One left behind
# on the constant is a limit that works for the map and not for the border, or
# the other way round, and nothing would say so.
sliced = [
    ast.dump(node)
    for node in ast.walk(answer)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
]
check(any("'limit'" in d for d in sliced), "the answer is not cut to the limit")
check(
    not any("NEARBY_MAX_STATIONS" in d for d in sliced),
    "a truncation was left on the constant instead of the limit",
)

print(f"service url tests passed ({checks} assertions)")

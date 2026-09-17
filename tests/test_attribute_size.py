"""The area sensor's attributes must fit in what the recorder will store.

Run with: python tests/test_attribute_size.py

Home Assistant's recorder refuses a state whose attributes exceed 16 KB. It
does not truncate — it stores NONE of them and logs a warning. The station list
was uncapped, which was harmless while an area meant 10 km of Denmark and
stopped being harmless the moment France shipped with a 50 km circle. From his
own log on 2026-09-17:

    State attributes for sensor.france_sp95_e10 exceed maximum size of 16384
    bytes. This can cause database performance issues; Attributes will not be
    stored

Measured against the live feed at his Lyon anchor that day: SP95-E10 was 343
stations and 94 KB, Gazole 401 and 110 KB — 5.7x and 6.7x over. The sensor went
on working, because the card reads the live state; what was silently empty was
its history.

`sensor.py` imports Home Assistant, so it cannot be loaded here. This checks
the two things that can regress without anyone noticing: that the cap is small
enough to do its job against a pessimistic row, and that the source really
applies it — and really does not apply it to `station_count`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)

RECORDER_LIMIT = 16384  # homeassistant.components.recorder.db_schema


def _const() -> types.ModuleType:
    package = sys.modules.get("tp")
    if package is None:
        package = types.ModuleType("tp")
        package.__path__ = [BASE]
        sys.modules["tp"] = package
    spec = importlib.util.spec_from_file_location("tp.const", os.path.join(BASE, "const.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["tp.const"] = module
    spec.loader.exec_module(module)
    return module


const = _const()


def _sensor_rule():
    """`_listed_stations` lifted out of sensor.py, which cannot be imported
    here because the module pulls in Home Assistant at the top.

    Compiled from the real source rather than copied, so a change to the rule
    is a change to what this tests — a copy would have gone stale the first
    time anyone touched it.
    """
    source = open(os.path.join(BASE, "sensor.py"), encoding="utf-8").read()
    match = re.search(
        r"^def _listed_stations\(.*?(?=^def |^class )", source, re.S | re.M
    )
    if match is None:                                    # pragma: no cover
        raise AssertionError("_listed_stations is gone from sensor.py")
    namespace = {
        "json": json,
        "STATION_ATTR_LIMIT": const.STATION_ATTR_LIMIT,
        "STATION_ATTR_BUDGET": const.STATION_ATTR_BUDGET,
    }
    exec(compile(match.group(0), "sensor.py", "exec"), namespace)
    return namespace["_listed_stations"]


sensor_rule = _sensor_rule()
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def row(index: int) -> dict:
    """One station, sized pessimistically.

    Longer than anything the real feeds return — a 40-character forecourt name
    and a 44-character address — so a pass here is a pass on real data rather
    than on a lucky fixture.
    """
    return {
        "name": f"TOTALENERGIES ACCESS RELAIS DE LA GARE {index:04d}"[:40],
        "company": "TOTALENERGIES ACCESS",
        "postnummer": "69007",
        "city": "Villefranche-sur-Saone",
        "address": "355 Avenue du Marechal de Lattre de Tassigny"[:44],
        "price": 2.249,
        "list_price": 2.349,
        "discount_ore": 10,
        "updated": "2026-09-17T21:04:11+02:00",
        "latitude": 45.750398,
        "longitude": 4.852286,
        "coord_approx": False,
    }


def size_of(count: int) -> int:
    return len(json.dumps([row(i) for i in range(count)], ensure_ascii=False).encode())


class FakeStation:
    """A station the size of the worst one a real feed has produced."""

    def __init__(self, index: int, fat: bool):
        self.name = (f"TOTALENERGIES ACCESS RELAIS DE LA GARE {index:04d}"[:40]
                     if fat else f"SHELL {index}")
        self.company = "TOTALENERGIES ACCESS" if fat else "SHELL"
        self.postnummer = "69007"
        self.city = "Villefranche-sur-Saone" if fat else "Lyon"
        self.address = ("355 Avenue du Marechal de Lattre de Tassigny"[:44]
                        if fat else "Rue Garibaldi")
        self.prices = {"diesel": 2.249}
        # A Danish discount is what makes a row fat: two more populated fields
        # where France carries nulls.
        self.list_prices = {"diesel": 2.349} if fat else {}
        self.discount_ore = 10 if fat else 0
        self.updated = "2026-09-17T21:04:11+02:00"
        self.latitude = 45.750398
        self.longitude = 4.852286
        self.coord_approx = False


def listed(count: int, fat: bool = True) -> list[dict]:
    """What the sensor would publish, via the real rule."""
    return sensor_rule([FakeStation(i, fat) for i in range(count)], "diesel")


def bytes_of(rows: list[dict]) -> int:
    return len(json.dumps(rows, ensure_ascii=False).encode())


print("the cap keeps the attributes storable")
limit = const.STATION_ATTR_LIMIT
check("there is a cap at all", isinstance(limit, int) and limit > 0, limit)
check("and it is the 50 that was asked for", limit == 50, limit)

# The count is the ceiling: a country with plenty of cheap, short-named
# forecourts publishes exactly 50 and no more.
slim = listed(400, fat=False)
check("a big area is capped at the count", len(slim) == limit, len(slim))
check(
    "and fits with room for the summary attributes",
    bytes_of(slim) <= RECORDER_LIMIT - 1024,
    f"{bytes_of(slim)} bytes of {RECORDER_LIMIT}",
)

# The budget is what actually holds. 50 FAT rows are 18.5 KB -- over the
# recorder's limit -- so the count alone would not have fixed anything. This
# is the check that fails if the byte budget is ever removed.
check(
    "50 pessimistic rows would have overflowed a count-only cap",
    size_of(50) > RECORDER_LIMIT,
    f"{size_of(50)} bytes",
)
fat = listed(400, fat=True)
check(
    "so the budget trims them further",
    len(fat) < limit,
    f"published {len(fat)} of a possible {limit}",
)
check(
    "and what is published always fits",
    bytes_of(fat) <= RECORDER_LIMIT - 1024,
    f"{bytes_of(fat)} bytes of {RECORDER_LIMIT}",
)

# Never publish nothing. An empty list is worse than one huge station: the
# cheapest is the answer the sensor exists to give.
class Monster(FakeStation):
    def __init__(self):
        super().__init__(0, True)
        self.name = "X" * 40_000

check(
    "one impossible station still gets published rather than none",
    len(sensor_rule([Monster()], "diesel")) == 1,
)
check(
    "an empty area publishes an empty list, not a crash",
    sensor_rule([], "diesel") == [],
)

# And it must still be worth capping at all.
check(
    "an uncapped French area would not have fit",
    size_of(343) > RECORDER_LIMIT,
    f"343 stations = {size_of(343)} bytes",
)

print()
print("the counts stay honest")
source = open(os.path.join(BASE, "sensor.py"), encoding="utf-8").read()
check(
    "station_count reports every station, not the published list",
    '"station_count": len(stations),' in source,
)
check(
    "and listed_count reports what actually made it in",
    '"listed_count": len(listed),' in source,
)

print()
if FAILURES:
    print(f"{len(FAILURES)} attribute-size checks FAILED")
    raise SystemExit(1)
print("all attribute-size checks passed")

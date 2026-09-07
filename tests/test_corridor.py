"""Tests for the driving search: motion inference, the corridor, the rim.

Run with: python tests/test_corridor.py

`nearby.py` imports nothing but `const.py`, so this needs no Home Assistant and
no network. What is being checked is the behaviour a driver would notice:

* a search that looks *ahead* when you are moving and *around* when you are not,
* stations behind you dropped at speed but never in town,
* the range actually searched being the range that gets said out loud.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)


def _load() -> types.ModuleType:
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    module = None
    for name in ("const", "nearby"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
    return module


nb = _load()
FAILURES: list[str] = []

# On the A7 north of Hamburg, heading south-south-east for Hannover.
HAMBURG = (53.5511, 9.9937)
COURSE_SSE = 160.0


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


class Station:
    """The few fields `rank_nearby` reads."""

    def __init__(self, name, lat, lon, price):
        self.name = name
        self.company = name.split()[0]
        self.city = ""
        self.latitude = lat
        self.longitude = lon
        self.prices = {"diesel": price}
        self.list_prices = {}
        self.discount_ore = 0
        self.coord_approx = False


# --- motion ----------------------------------------------------------------
def test_a_tracker_that_reports_speed_is_believed() -> None:
    print("infer_motion from a tracker that has a GPS lock")
    motion = nb.infer_motion(*HAMBURG, {"speed": 36.1, "course": 160})
    check("metres per second become km/h", round(motion.speed_kmh) == 130, motion.speed_kmh)
    check("the reported course is kept", motion.course_deg == 160.0, motion.course_deg)
    check("and that counts as moving", motion.moving is True)
    check("the source is recorded", motion.source == "reported", motion.source)

    # The companion app sends -1 for "no lock", which is not a reversing car.
    motion = nb.infer_motion(*HAMBURG, {"speed": -1, "course": -1})
    check("a no-lock reading is ignored", motion.source != "reported", motion.source)


def test_motion_derived_from_two_positions() -> None:
    print("infer_motion from the fresh position and the tracker's last fix")
    # 30 km south-south-east of Hamburg, a quarter of an hour later.
    later = nb.destination(*HAMBURG, COURSE_SSE, 30.0)
    motion = nb.infer_motion(
        *later,
        {"latitude": HAMBURG[0], "longitude": HAMBURG[1]},
        elapsed_s=900,
    )
    check("speed comes out of distance over time", round(motion.speed_kmh) == 120, motion.speed_kmh)
    check(
        "the heading is the one actually travelled",
        abs(motion.course_deg - COURSE_SSE) < 1.0,
        motion.course_deg,
    )
    check("that is moving", motion.moving is True)


def test_a_parked_phone_never_invents_a_heading() -> None:
    print("infer_motion on a phone that has not really gone anywhere")
    # 200 m of GPS wander over ten minutes.
    wandered = nb.destination(*HAMBURG, 42.0, 0.2)
    motion = nb.infer_motion(
        *wandered, {"latitude": HAMBURG[0], "longitude": HAMBURG[1]}, elapsed_s=600
    )
    check("no heading is claimed", motion.course_deg is None, motion.course_deg)
    check("and it is not moving", motion.moving is False)

    stale = nb.infer_motion(
        *nb.destination(*HAMBURG, COURSE_SSE, 50),
        {"latitude": HAMBURG[0], "longitude": HAMBURG[1]},
        elapsed_s=4 * 3600,
    )
    check("a fix from hours ago says nothing", stale.moving is False, stale)

    check("no fix at all is not moving", nb.infer_motion(*HAMBURG).moving is False)


def test_slow_is_not_moving() -> None:
    print("in town, a station behind you is a two-minute detour")
    crawl = nb.Motion(20.0, 160.0, "reported")
    check("20 km/h searches around you", crawl.moving is False)
    check("90 km/h searches ahead", nb.Motion(90.0, 160.0, "reported").moving is True)


# --- the corridor ----------------------------------------------------------
def test_parked_is_one_circle_where_you_stand() -> None:
    print("search_plan when parked")
    plan = nb.search_plan(*HAMBURG, nb.Motion(), 25)
    check("exactly one circle", len(plan) == 1, len(plan))
    check("centred on you", plan[0] == HAMBURG, plan[0])


def test_moving_is_a_corridor_along_the_heading() -> None:
    print("search_plan at motorway speed")
    motion = nb.Motion(130.0, COURSE_SSE, "reported")
    plan = nb.search_plan(*HAMBURG, motion, 25)
    check("three circles", len(plan) == 3, len(plan))

    distances = [nb.haversine_m(*HAMBURG, lat, lon) / 1000 for lat, lon in plan]
    check(
        "each one further down the road than the last",
        distances == sorted(distances),
        [round(d) for d in distances],
    )
    bearings = [nb.initial_bearing(*HAMBURG, lat, lon) for lat, lon in plan]
    check(
        "every one of them ahead of you, not around you",
        all(nb.angle_between(b, COURSE_SSE) < 1.0 for b in bearings),
        [round(b) for b in bearings],
    )
    gaps = [b - a for a, b in zip(distances, distances[1:])]
    check(
        "spaced to overlap rather than leave a gap",
        all(gap < 2 * 25 for gap in gaps),
        [round(g) for g in gaps],
    )
    check(
        "the first circle still covers the road just behind you",
        distances[0] < 25,
        round(distances[0]),
    )


def test_a_slower_road_looks_less_far() -> None:
    print("the corridor is a time horizon, not a fixed distance")
    slow = nb.search_plan(*HAMBURG, nb.Motion(45.0, COURSE_SSE, "reported"), 25)
    fast = nb.search_plan(*HAMBURG, nb.Motion(130.0, COURSE_SSE, "reported"), 25)
    check("fewer circles at 45 km/h than at 130", len(slow) < len(fast), (len(slow), len(fast)))
    check(
        "and a shorter reach",
        nb.searched_km(*HAMBURG, slow, 25) < nb.searched_km(*HAMBURG, fast, 25),
    )
    check(
        "never more than three circles for one answer",
        len(nb.search_plan(*HAMBURG, nb.Motion(250.0, COURSE_SSE, "reported"), 25)) <= 3,
    )


def test_the_reach_is_what_was_really_covered() -> None:
    print("searched_km")
    plan = nb.search_plan(*HAMBURG, nb.Motion(130.0, COURSE_SSE, "reported"), 25)
    reach = nb.searched_km(*HAMBURG, plan, 25)
    furthest = max(nb.haversine_m(*HAMBURG, lat, lon) / 1000 for lat, lon in plan)
    check(
        "the far edge of the furthest circle, not the radius asked for",
        abs(reach - (furthest + 25)) < 1,
        (reach, round(furthest)),
    )
    check("parked, it is just the radius", nb.searched_km(*HAMBURG, [HAMBURG], 25) == 25)


# --- the rim ---------------------------------------------------------------
def test_the_rim_heuristic() -> None:
    print("should_extend")
    check("nothing found is not a reason to look further", nb.should_extend([], 100) is False)
    check(
        "a cheap station close by is not either",
        nb.should_extend([{"distance_km": 12}], 100) is False,
    )
    check(
        "but the cheapest sitting at the edge is",
        nb.should_extend([{"distance_km": 95}], 100) is True,
    )
    check(
        "it is the cheapest that counts, not the nearest",
        nb.should_extend([{"distance_km": 90}, {"distance_km": 3}], 100) is True,
    )


# --- ranking ---------------------------------------------------------------
def test_stations_behind_you_are_dropped_at_speed() -> None:
    print("rank_nearby with a heading")
    ahead = nb.destination(*HAMBURG, COURSE_SSE, 40)
    behind = nb.destination(*HAMBURG, (COURSE_SSE + 180) % 360, 40)
    just_passed = nb.destination(*HAMBURG, (COURSE_SSE + 180) % 360, 1.5)
    stations = [
        Station("Aral ahead", ahead[0], ahead[1], 1.759),
        Station("Shell behind", behind[0], behind[1], 1.599),
        Station("Esso just passed", just_passed[0], just_passed[1], 1.649),
    ]

    everything = nb.rank_nearby(stations, *HAMBURG, 100_000, "diesel")
    check("without a heading, all three are offered", len(everything) == 3, len(everything))
    check("cheapest first", everything[0]["name"] == "Shell behind", everything[0]["name"])

    driving = nb.rank_nearby(stations, *HAMBURG, 100_000, "diesel", course_deg=COURSE_SSE)
    names = [s["name"] for s in driving]
    check("the one 40 km behind is gone", "Shell behind" not in names, names)
    check("the one you just passed is kept", "Esso just passed" in names, names)
    check("and so is the one ahead", "Aral ahead" in names, names)


# --- what gets said --------------------------------------------------------
def test_an_empty_answer_says_how_far_it_looked() -> None:
    print("spoken output")
    check(
        "empty, in Danish, names the range",
        nb.spoken_sentence([], danish=True, searched_km=135)
        == "Ingen stationer inden for 135 kilometer.",
        nb.spoken_sentence([], danish=True, searched_km=135),
    )
    check(
        "empty, in English, names the range",
        "within 135 kilometres" in nb.spoken_sentence([], danish=False, searched_km=135),
    )
    check(
        "with no range known it still says something",
        nb.spoken_sentence([], danish=True) == "Ingen stationer i nærheden.",
    )
    check(
        "the cheapest-only sentence does the same",
        "135" in nb.spoken_cheapest([], danish=True, searched_km=135),
    )


def test_a_thin_answer_says_so() -> None:
    print("one station found in 135 km is not the same as a full list")
    one = [{"name": "Aral Nord", "company": "ARAL", "city": "", "price": 1.759, "distance_km": 12.0}]
    said = nb.spoken_sentence(one, danish=True, currency="euro", searched_km=135)
    check("the range is added", "135 kilometer" in said, said)
    check("the station is still named", "Aral Nord" in said, said)

    three = [
        {"name": f"Aral {i}", "company": "ARAL", "city": "", "price": 1.7 + i / 100, "distance_km": i}
        for i in (1, 2, 3)
    ]
    check(
        "a full list needs no such caveat",
        "Det var alt" not in nb.spoken_sentence(three, danish=True, searched_km=135),
        nb.spoken_sentence(three, danish=True, searched_km=135),
    )
    check(
        "and the currency is the country's",
        "euro" in nb.spoken_sentence(three, danish=True, currency="euro"),
    )


if __name__ == "__main__":
    test_a_tracker_that_reports_speed_is_believed()
    test_motion_derived_from_two_positions()
    test_a_parked_phone_never_invents_a_heading()
    test_slow_is_not_moving()
    test_parked_is_one_circle_where_you_stand()
    test_moving_is_a_corridor_along_the_heading()
    test_a_slower_road_looks_less_far()
    test_the_reach_is_what_was_really_covered()
    test_the_rim_heuristic()
    test_stations_behind_you_are_dropped_at_speed()
    test_an_empty_answer_says_how_far_it_looked()
    test_a_thin_answer_says_so()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all corridor tests passed")

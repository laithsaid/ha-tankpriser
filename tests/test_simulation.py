"""Tests for the drive simulator's route maths.

Run with: python tests/test_simulation.py

Only the pure part is exercised here — turning waypoints into legs, and finding
where you are after driving so far along them. The parts that write states and
call services need Home Assistant, and testing them against a stub would only
prove the stub works.

What matters below is that the simulated car ends up where the route says, at
the right distance and pointing the right way: a simulation that drifts is
worse than none, because the thing it is testing is a *heading*.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)

# The A7 south from the Danish border: Flensburg, Hamburg, Hannover.
ROUTE = [(54.7820, 9.4360), (53.5511, 9.9937), (52.3759, 9.7320)]


def _load() -> tuple[types.ModuleType, types.ModuleType]:
    # simulate.py needs only HomeAssistant as a type; nothing here calls it.
    sys.modules.setdefault(
        "homeassistant", types.ModuleType("homeassistant")
    )
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules.setdefault("homeassistant.core", core)

    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    loaded = {}
    for name in ("const", "nearby", "simulate"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["simulate"], loaded["nearby"]


sim, nb = _load()
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def test_the_route_becomes_legs() -> None:
    print("build_legs")
    legs = sim.build_legs(ROUTE)
    check("one leg per gap between waypoints", len(legs) == 2, len(legs))
    check(
        "each leg starts where the last waypoint was",
        (legs[0].latitude, legs[0].longitude) == ROUTE[0],
        (legs[0].latitude, legs[0].longitude),
    )
    check(
        "Flensburg to Hamburg is about 140 km",
        130 < legs[0].length_km < 150,
        round(legs[0].length_km),
    )
    check(
        "and the first leg heads south",
        150 < legs[0].bearing < 190,
        round(legs[0].bearing),
    )
    check(
        "a repeated waypoint makes no zero-length leg",
        len(sim.build_legs([ROUTE[0], ROUTE[0], ROUTE[1]])) == 1,
    )


def test_where_the_car_is_after_driving() -> None:
    print("position_at")
    legs = sim.build_legs(ROUTE)
    total = sum(leg.length_km for leg in legs)

    start = sim.position_at(legs, 0.0)
    check(
        "at zero you are at the first waypoint",
        nb.haversine_m(start[0], start[1], *ROUTE[0]) < 1,
        start,
    )

    end = sim.position_at(legs, total)
    check(
        "at the end you are at the last one",
        nb.haversine_m(end[0], end[1], *ROUTE[-1]) < 100,
        (end, ROUTE[-1]),
    )

    check(
        "driving past the end parks you there, it does not fly on",
        nb.haversine_m(*sim.position_at(legs, total + 500)[:2], *ROUTE[-1]) < 100,
    )

    # Halfway along the first leg.
    half = sim.position_at(legs, legs[0].length_km / 2)
    check(
        "halfway is halfway",
        abs(nb.haversine_m(half[0], half[1], *ROUTE[0]) / 1000 - legs[0].length_km / 2)
        < 0.5,
        nb.haversine_m(half[0], half[1], *ROUTE[0]) / 1000,
    )
    check(
        "and it faces along the leg it is on",
        abs(half[2] - legs[0].bearing) < 0.001,
        (half[2], legs[0].bearing),
    )

    # One metre past the first waypoint, the heading is the *second* leg's.
    turned = sim.position_at(legs, legs[0].length_km + 0.001)
    check(
        "the heading changes at the waypoint, not before it",
        abs(turned[2] - legs[1].bearing) < 0.001,
        (turned[2], legs[1].bearing),
    )


def test_the_distance_covered_per_step() -> None:
    print("a step is speed x interval, and the route ends")
    legs = sim.build_legs(ROUTE)
    total = sum(leg.length_km for leg in legs)

    # 110 km/h for 60 s is 1.833 km.
    class Fake:
        speed_kmh = 110.0
        interval_s = 60.0
        total_km = total
        step_km = sim.DriveSimulation.step_km
        steps = sim.DriveSimulation.steps

    fake = Fake()
    check("a step is speed x interval", abs(Fake.step_km.fget(fake) - 1.8333) < 0.001,
          Fake.step_km.fget(fake))
    walked = Fake.steps.fget(fake)
    check(
        "and enough steps are taken to reach the end",
        walked * Fake.step_km.fget(fake) >= total,
        (walked, round(total)),
    )


def test_the_shipped_routes_are_sane() -> None:
    print("the named routes")
    const = sys.modules["tp.const"]
    for name, points in const.SIMULATION_ROUTES.items():
        legs = sim.build_legs([tuple(p) for p in points])
        total = sum(leg.length_km for leg in legs)
        check(
            f"{name}: at least two waypoints and a real length",
            len(points) >= 2 and len(legs) == len(points) - 1 and total > 50,
            (len(points), round(total)),
        )
        check(
            f"{name}: no leg so long it drives through the sea unnoticed",
            max(leg.length_km for leg in legs) < 350,
            round(max(leg.length_km for leg in legs)),
        )


if __name__ == "__main__":
    test_the_route_becomes_legs()
    test_where_the_car_is_after_driving()
    test_the_distance_covered_per_step()
    test_the_shipped_routes_are_sane()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all simulation tests passed")

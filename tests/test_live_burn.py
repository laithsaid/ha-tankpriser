"""Tests for the live burn rate — what the car is using while it is running.

Run with: python tests/test_live_burn.py

This is the half of the answer that needs no learning at all. The tank is
visibly emptying, so "at this rate it runs out at 18:40" is arithmetic on the
last couple of hours: no habit, no history, no correction factor. A car on its
first ever drive can answer it.

What has to be got right is knowing when *not* to answer. Readings are only
stored when the level actually moves, so a parked car goes quiet — but a fuel
sender is coarse and a car on a slope wanders, and either could otherwise be
read as a journey and produce a confident, absurd number.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
HOUR = 3600.0
CAPACITY = 60.0
NOW = 1_700_000_000.0


def _load():
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    loaded = {}
    for name in ("const", "prediction"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["prediction"], loaded["const"]


pred, const = _load()
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def driving(readings, capacity=CAPACITY):
    """A model fed `readings` of (hours before now, litres[, odometer])."""
    model = pred.ConsumptionModel(capacity)
    for entry in readings:
        hours, litres = entry[0], entry[1]
        odo = entry[2] if len(entry) > 2 else None
        model.add_reading(NOW - hours * HOUR, litres, odo)
    return model


# --- while the car is running ----------------------------------------------
def test_a_car_being_driven_answers_from_what_it_is_doing() -> None:
    print("burning 8 L/h with 20 L left")
    # Two hours of driving, 16 litres gone, currently 20 in the tank.
    model = driving([(2.0, 36.0), (1.0, 28.0), (0.05, 20.0)])
    burn = pred.live_burn(model, NOW)
    check("it answers", burn is not None)
    check("the rate is litres over hours", abs(burn.litres_per_hour - 8.2) < 0.2, burn.litres_per_hour)
    check(
        "so the tank runs out in about two and a half hours",
        abs(burn.hours_until_empty - 2.4) < 0.2,
        burn.hours_until_empty,
    )
    check("and it says what it measured", burn.litres_consumed == 16.0, burn.litres_consumed)
    check("over how long", abs(burn.over_hours - 1.95) < 0.1, burn.over_hours)


def test_it_needs_no_history_whatsoever() -> None:
    """The point of this half: a brand-new car can still be measured."""
    print("a car with no completed tanks at all")
    model = driving([(1.5, 50.0), (0.1, 38.0)])
    check("no tanks learned", len(model.segments) == 0)
    burn = pred.live_burn(model, NOW)
    check("but the live answer is there anyway", burn is not None)
    check("and carries no correction of any kind", not hasattr(burn, "calibration"))


def test_an_odometer_adds_efficiency_and_speed() -> None:
    print("with an odometer on the same drive")
    model = driving([(2.0, 36.0, 1000.0), (1.0, 28.0, 1090.0), (0.05, 20.0, 1180.0)])
    burn = pred.live_burn(model, NOW)
    check(
        "180 km on 16 litres is about 8.9 L/100 km",
        abs(burn.litres_per_100km - 8.9) < 0.2,
        burn.litres_per_100km,
    )
    check(
        "and that is about 92 km/h",
        abs(burn.km_per_hour - 92) < 3,
        burn.km_per_hour,
    )


# --- when it must refuse to answer -----------------------------------------
def test_a_parked_car_says_nothing() -> None:
    print("silence is the signal")
    # The same drive, but it ended hours ago.
    model = pred.ConsumptionModel(CAPACITY)
    for hours, litres in ((6.0, 36.0), (5.0, 28.0), (4.0, 20.0)):
        model.add_reading(NOW - hours * HOUR, litres)
    check("nothing reported recently means not running", pred.live_burn(model, NOW) is None)
    check("no readings at all is also None", pred.live_burn(pred.ConsumptionModel(CAPACITY), NOW) is None)
    check(
        "and one lone reading cannot make a rate",
        pred.live_burn(driving([(0.1, 40.0)]), NOW) is None,
    )


def test_a_coarse_sender_wobbling_is_not_a_journey() -> None:
    print("a parked car on a slope")
    # Half a litre of wander over an hour — under the threshold.
    model = driving([(1.0, 40.0), (0.05, 39.5)])
    check("too small a drop to be driving", pred.live_burn(model, NOW) is None)
    # The same half-litre over three minutes would divide by almost nothing.
    brief = driving([(0.06, 40.0), (0.01, 39.5)])
    check("and too short a window either way", pred.live_burn(brief, NOW) is None)


def test_a_refuel_inside_the_window_is_excluded() -> None:
    """Otherwise the car appears to un-burn several litres and the rate inverts."""
    print("filling up, then driving off")
    model = pred.ConsumptionModel(CAPACITY)
    model.add_reading(NOW - 1.6 * HOUR, 8.0)      # nearly empty
    model.add_reading(NOW - 1.5 * HOUR, 58.0)     # filled up: closes the tank
    model.add_reading(NOW - 0.8 * HOUR, 52.0)     # driving away
    model.add_reading(NOW - 0.02 * HOUR, 46.0)
    burn = pred.live_burn(model, NOW)
    check("it still answers", burn is not None)
    check("the rate is positive", burn.litres_per_hour > 0, burn.litres_per_hour)
    check(
        "and measured only from the new tank, not across the fill",
        abs(burn.litres_consumed - 12.0) < 0.01,
        burn.litres_consumed,
    )


def test_the_window_does_not_reach_back_forever() -> None:
    print("only the last couple of hours describe this drive")
    # A slow morning followed by a fast motorway hour: the old part is dropped.
    model = driving([(6.0, 58.0), (5.0, 52.0), (1.0, 40.0), (0.02, 28.0)])
    burn = pred.live_burn(model, NOW)
    check(
        "only readings inside the window count",
        abs(burn.litres_consumed - 12.0) < 0.01,
        burn.litres_consumed,
    )
    check(
        "so the rate describes the motorway, not the morning",
        burn.litres_per_hour > 10,
        burn.litres_per_hour,
    )


def test_the_two_answers_are_different_questions() -> None:
    """The whole reason this exists as a separate calculation.

    A car that is normally parked six days a week has a low daily rate and a
    long "days until refuel". The moment it is actually driven it burns litres
    per hour, and the tank in front of it is measured in hours. Both are true;
    they are answers to different questions.
    """
    print("hours while driving, days while parked")
    model = pred.ConsumptionModel(CAPACITY)
    # Three quiet tanks: 45 L over ten days each.
    day = 86400.0
    ts = NOW - 40 * day
    for _ in range(3):
        model.add_reading(ts, 55.0)
        model.add_reading(ts + 10 * day, 10.0)
        ts += 10.5 * day
    model.add_reading(NOW - 2 * HOUR, 55.0)
    model.add_reading(NOW - 1 * HOUR, 47.0)
    model.add_reading(NOW - 0.02 * HOUR, 39.0)

    habit = pred.predict(model, 39.0)
    burn = pred.live_burn(model, NOW)
    check("the habit answer is in days", habit is not None and habit.days_until_empty > 3,
          habit.days_until_empty if habit else None)
    check("the live answer is in hours", burn is not None and burn.hours_until_empty < 12,
          burn.hours_until_empty if burn else None)
    check(
        "and neither contradicts the other — they measure different things",
        habit.days_until_empty * 24 > burn.hours_until_empty,
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print(f"all {len(tests)} live-burn test groups passed")

"""Tests for the "fill up now" decision.

Run with: python tests/test_fillup.py

`fillup.py` imports nothing at all, so this needs neither Home Assistant nor a
network. Most of what is checked here is *silence*: the alert interrupts
someone, and the failure mode that matters is not a missed nudge but a car
parked low on the drive producing the same push every half hour until it is
muted for good.
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
    spec = importlib.util.spec_from_file_location(
        "fillup", os.path.join(BASE, "fillup.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["fillup"] = module
    spec.loader.exec_module(module)
    return module


fu = _load()
FAILURES: list[str] = []
NOW = 1_700_000_000.0
ON = fu.Settings(enabled=True, low_pct=25.0, near_km=5.0)


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def car(level=15.0, placed=True, days=3.0):
    return fu.CarSnapshot(
        car_id="passat",
        name="Passat",
        fuel_key="diesel",
        level_pct=level,
        litres=10.0,
        days_until_empty=days,
        latitude=56.1697 if placed else None,
        longitude=9.5451 if placed else None,
    )


def station(price=16.79, km=0.3, approx=False, name="OK Silkeborg"):
    return {
        "name": name,
        "company": "OK",
        "city": "Silkeborg",
        "price": price,
        "distance_km": km,
        "latitude": 56.17,
        "longitude": 9.55,
        "coord_approx": approx,
    }


# --- when it should speak --------------------------------------------------
def test_low_and_next_to_something_cheap() -> None:
    print("the one case worth interrupting for")
    memory = fu.FillupMemory()
    out = fu.evaluate(car(), [station(16.79), station(17.29, km=2.0)], ON, memory, NOW)
    check("a suggestion is made", out is not None)
    check("it names the car", out.car_name == "Passat", out.car_name)
    check("and the cheapest station", out.station["name"] == "OK Silkeborg")
    check(
        "the saving is the gap to the dearest in range, in øre",
        out.saving_minor == 50,
        out.saving_minor,
    )
    check("the memory is now spent", memory.armed is False)


def test_what_it_says() -> None:
    print("the message")
    out = fu.evaluate(car(level=18.0), [station(16.79)], ON, fu.FillupMemory(), NOW)
    title, body = fu.message(out, "kr./L", "16,79", danish=True)
    check("the title names the car", title == "Tank Passat", title)
    check("the level is there", "18 %" in body, body)
    check("so are the days left", "3 dage" in body, body)
    check("and the station, distance and price", "OK Silkeborg" in body and "0,3 km" in body and "16,79 kr./L" in body, body)
    check("and the saving is not mentioned when there is none", "øre" not in body, body)

    english_title, english = fu.message(out, "kr./L", "16.79", danish=False)
    check("English says the same things", english_title == "Fill up Passat", english_title)
    check(
        "with a decimal point in the distance, not a comma",
        "0.3 km away" in english and "days left" in english,
        english,
    )

    with_saving = fu.evaluate(
        car(level=18.0), [station(16.79), station(17.29, km=2.0)], ON, fu.FillupMemory(), NOW
    )
    _, body = fu.message(with_saving, "kr./L", "16,79", danish=True)
    check("a real saving is mentioned", "50 øre" in body, body)

    german = fu.message(with_saving, "€/L", "1,719", danish=False, minor_unit="cent")
    check("Germany counts in cents", "50 cent" in german[1], german[1])


# --- when it must stay quiet -----------------------------------------------
def test_it_says_nothing_when_it_should_not() -> None:
    print("silence")
    check(
        "switched off",
        fu.evaluate(car(), [station()], fu.Settings(enabled=False), fu.FillupMemory(), NOW)
        is None,
    )
    check(
        "a full tank",
        fu.evaluate(car(level=80.0), [station()], ON, fu.FillupMemory(), NOW) is None,
    )
    check(
        "a level we do not know",
        fu.evaluate(car(level=None), [station()], ON, fu.FillupMemory(), NOW) is None,
    )
    check(
        "a car we cannot place",
        fu.evaluate(car(placed=False), [station()], ON, fu.FillupMemory(), NOW) is None,
    )
    check(
        "nothing in range",
        fu.evaluate(car(), [], ON, fu.FillupMemory(), NOW) is None,
    )
    check(
        "cheap, but 30 km away — that is an errand, not a nudge",
        fu.evaluate(car(), [station(km=30.0)], ON, fu.FillupMemory(), NOW) is None,
    )
    check(
        "a station whose position is only estimated",
        fu.evaluate(car(), [station(approx=True)], ON, fu.FillupMemory(), NOW) is None,
    )


def test_it_says_it_once() -> None:
    print("a car parked low on the drive is mentioned once, not forever")
    memory = fu.FillupMemory()
    first = fu.evaluate(car(), [station()], ON, memory, NOW)
    check("the first refresh speaks", first is not None)

    half_hour = NOW + 1800
    check(
        "the next refresh does not",
        fu.evaluate(car(), [station()], ON, memory, half_hour) is None,
    )
    check(
        "nor does it an hour later",
        fu.evaluate(car(), [station()], ON, memory, NOW + 3600) is None,
    )
    check(
        "after the cooldown, and still low, it may speak again",
        fu.evaluate(car(), [station()], ON, memory, NOW + fu.COOLDOWN_S + 60) is not None,
    )


def test_filling_up_re_arms_it() -> None:
    print("the alert comes back once the tank does")
    memory = fu.FillupMemory()
    fu.evaluate(car(), [station()], ON, memory, NOW)
    check("spent", memory.armed is False)

    # Filled up: well clear of the threshold.
    check(
        "a full tank says nothing itself",
        fu.evaluate(car(level=95.0), [station()], ON, memory, NOW + 60) is None,
    )
    check("but it re-arms the alert", memory.armed is True)
    check(
        "so the next low tank is announced without waiting out the cooldown",
        fu.evaluate(car(level=12.0), [station()], ON, memory, NOW + 120) is not None,
    )


def test_hovering_on_the_line_does_not_re_arm() -> None:
    print("a level wobbling around the threshold must not re-arm on noise")
    memory = fu.FillupMemory()
    fu.evaluate(car(level=24.0), [station()], ON, memory, NOW)
    check("spent", memory.armed is False)
    # 26 % is above the threshold but nowhere near a refuel.
    fu.evaluate(car(level=26.0), [station()], ON, memory, NOW + 60)
    check("still spent", memory.armed is False)
    check(
        "and dropping back under says nothing",
        fu.evaluate(car(level=24.0), [station()], ON, memory, NOW + 120) is None,
    )


def test_a_car_still_learning_is_still_alerted() -> None:
    print("no prediction yet is not a reason to stay quiet")
    out = fu.evaluate(car(days=None), [station()], ON, fu.FillupMemory(), NOW)
    check("it still fires", out is not None)
    _, body = fu.message(out, "kr./L", "16,79", danish=True)
    check("and simply omits the days", "dage" not in body, body)


if __name__ == "__main__":
    test_low_and_next_to_something_cheap()
    test_what_it_says()
    test_it_says_nothing_when_it_should_not()
    test_it_says_it_once()
    test_filling_up_re_arms_it()
    test_hovering_on_the_line_does_not_re_arm()
    test_a_car_still_learning_is_still_alerted()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all fill-up tests passed")

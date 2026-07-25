"""Tests for prediction.py — refuel detection and the two-tier estimator.

Run with: python tests/test_prediction.py   (no pytest, no dependencies)

`prediction.py` imports nothing from Home Assistant, so unlike the other test
files this one imports it for real — as a synthetic package next to `const.py`.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
DAY = 86_400.0


def _load() -> tuple[types.ModuleType, types.ModuleType]:
    """Import const + prediction as a two-module package, without Home Assistant."""
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package

    modules = {}
    for name in ("const", "prediction"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules["const"], modules["prediction"]


const, prediction = _load()
Model = prediction.ConsumptionModel
predict = prediction.predict
CAP = 60.0  # litres, for every model below


def drive(model, start_ts: float, litres_from: float, litres_to: float,
          days: float, *, odo_from: float | None = None,
          km: float | None = None, steps: int = 4) -> float:
    """Feed a gradual drain, as a real level sensor would. Returns the end ts."""
    for i in range(1, steps + 1):
        fraction = i / steps
        ts = start_ts + days * DAY * fraction
        litres = litres_from + (litres_to - litres_from) * fraction
        odo = None if odo_from is None or km is None else odo_from + km * fraction
        model.add_reading(ts, litres, odo)
    return start_ts + days * DAY


def full_tank(model, ts: float, litres: float = CAP, odo: float | None = None) -> None:
    """A refuel: one big jump up, which is what closes a segment."""
    model.add_reading(ts, litres, odo)


# -- refuel detection --------------------------------------------------------
def test_refuel_closes_a_tank() -> None:
    model = Model(CAP)
    model.add_reading(0, 50.0)
    end = drive(model, 0, 50.0, 12.0, 7.0)
    assert model.segments == []
    assert model.add_reading(end + 60, CAP) is True, "a jump up is a refuel"
    assert len(model.segments) == 1
    tank = model.segments[0]
    assert round(tank.consumed_litres, 1) == 38.0
    assert round(tank.duration_days, 1) == 7.0


def test_small_topup_is_not_a_refuel() -> None:
    """Below REFUEL_MIN_JUMP_FRACTION of the tank it is noise, not a fill-up."""
    model = Model(CAP)
    model.add_reading(0, 30.0)
    # +5 L on a 60 L tank is 8 %, under the 15 % threshold.
    assert model.add_reading(DAY, 35.0) is False
    assert model.segments == []


# -- tier 1: the tank in progress -------------------------------------------
def test_a_parked_car_predicts_nothing() -> None:
    """The trap this guards: consumed ~0 over days would mean "empty in years"."""
    model = Model(CAP)
    model.add_reading(0, 40.0)
    model.add_reading(5 * DAY, 39.9)  # a whisker of drift, no real driving
    assert predict(model, 39.9) is None


def test_one_days_driving_is_not_enough_on_its_own() -> None:
    """A single long trip must not be projected as a daily habit."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    drive(model, 0, 60.0, 45.0, 0.4)  # 15 L in under half a day
    assert predict(model, 45.0) is None


def test_early_estimate_from_the_open_tank() -> None:
    model = Model(CAP)
    model.add_reading(0, 60.0)
    drive(model, 0, 60.0, 45.0, 3.0)  # 15 L over 3 days = 5 L/day
    result = predict(model, 45.0)
    assert result is not None, "3 days and a quarter tank is enough to answer"
    assert result.basis == "current tank"
    assert result.is_early is True
    assert result.segments == 0, "no tank has completed yet"
    assert round(result.avg_consumption, 1) == 5.0
    assert round(result.days_until_empty, 0) == 9.0  # 45 L / 5 L per day
    assert 0 < result.confidence <= const.EARLY_CONFIDENCE_CAP


def test_one_completed_tank_is_still_early() -> None:
    """MIN_SEGMENTS_FOR_PREDICTION is 2, so one tank alone stays provisional."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    end = drive(model, 0, 60.0, 10.0, 10.0)
    full_tank(model, end + 60)
    drive(model, end + 60, 60.0, 50.0, 2.0)
    result = predict(model, 50.0)
    assert result is not None
    assert result.segments == 1
    assert result.basis == "current tank"
    assert result.confidence <= const.EARLY_CONFIDENCE_CAP


# -- tier 2: completed tanks -------------------------------------------------
def test_two_tanks_make_it_ready() -> None:
    model = Model(CAP)
    model.add_reading(0, 60.0)
    ts = drive(model, 0, 60.0, 10.0, 10.0)      # 50 L / 10 d = 5 L/day
    full_tank(model, ts + 60)
    ts = drive(model, ts + 60, 60.0, 10.0, 10.0)
    full_tank(model, ts + 60)
    result = predict(model, 60.0)
    assert result is not None
    assert result.basis == "tanks"
    assert result.is_early is False
    assert result.segments == 2
    assert round(result.avg_consumption, 1) == 5.0
    assert round(result.days_until_empty, 0) == 12.0  # 60 L / 5
    assert result.confidence > const.EARLY_CONFIDENCE_CAP


def test_the_open_tank_keeps_calibrating_between_refuels() -> None:
    """Habit change must show up without waiting for the next fill-up."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    ts = drive(model, 0, 60.0, 10.0, 10.0)      # two calm tanks: 5 L/day
    full_tank(model, ts + 60)
    ts = drive(model, ts + 60, 60.0, 10.0, 10.0)
    full_tank(model, ts + 60)
    calm = predict(model, 60.0).days_until_empty

    # Now drive twice as hard on the open tank.
    drive(model, ts + 60, 60.0, 40.0, 2.0)      # 20 L in 2 days = 10 L/day
    busy = predict(model, 40.0)
    assert busy.basis == "tanks", "completed tanks still make it 'ready'"
    assert busy.avg_consumption > 5.0, "the open tank must pull the rate up"
    assert busy.days_until_empty < calm


def test_odometer_gives_litres_per_100km() -> None:
    model = Model(CAP)
    model.add_reading(0, 60.0, 10_000.0)
    ts = drive(model, 0, 60.0, 10.0, 10.0, odo_from=10_000.0, km=625.0)
    full_tank(model, ts + 60, odo=10_625.0)
    ts2 = drive(model, ts + 60, 60.0, 10.0, 10.0, odo_from=10_625.0, km=625.0)
    full_tank(model, ts2 + 60, odo=11_250.0)
    result = predict(model, 60.0)
    assert result.method == "odometer"
    assert result.consumption_unit == "L/100 km"
    assert round(result.avg_consumption, 1) == 8.0  # 50 L / 625 km
    # Days still come from the time-based rate, the only calendar projection.
    assert round(result.days_until_empty, 0) == 12.0


def test_missing_odometer_falls_back_to_litres_per_day() -> None:
    model = Model(CAP)
    model.add_reading(0, 60.0, 10_000.0)
    ts = drive(model, 0, 60.0, 10.0, 10.0, odo_from=10_000.0, km=625.0)
    full_tank(model, ts + 60, odo=10_625.0)
    ts2 = drive(model, ts + 60, 60.0, 10.0, 10.0)  # this tank has no odometer
    full_tank(model, ts2 + 60)
    result = predict(model, 60.0)
    assert result.method == "time"
    assert result.consumption_unit == "L/day"


def test_seed_demo_is_ready_immediately() -> None:
    model = Model(CAP)
    model.seed_demo(now_ts=100 * DAY, tanks=3, litres_per_day=5.0, days_per_tank=7.0)
    result = predict(model, 60.0)
    assert result is not None and result.basis == "tanks" and result.segments == 3


def test_a_cold_model_says_nothing() -> None:
    assert predict(Model(CAP), None) is None
    empty = Model(CAP)
    empty.add_reading(0, 30.0)
    assert predict(empty, 30.0) is None, "one reading implies no rate at all"


def test_serialisation_round_trip_keeps_the_open_tank() -> None:
    """The open tank now drives the early estimate, so it must survive a restart."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    drive(model, 0, 60.0, 45.0, 3.0)
    before = predict(model, 45.0)

    revived = Model.from_dict(model.as_dict(), CAP)
    assert revived.open_tank_start is not None
    after = predict(revived, 45.0)
    assert after.days_until_empty == before.days_until_empty
    assert after.basis == before.basis


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} prediction tests passed")

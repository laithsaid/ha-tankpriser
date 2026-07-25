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


def test_a_short_window_is_not_enough_on_its_own() -> None:
    """A burst of driving must not be projected as a daily habit.

    EARLY_MIN_DAYS is 3 precisely because someone who drives irregularly would
    otherwise have one busy day define their whole rate.
    """
    model = Model(CAP)
    model.add_reading(0, 60.0)
    drive(model, 0, 60.0, 45.0, 0.4)  # 15 L in under half a day
    assert predict(model, 45.0) is None

    later = Model(CAP)
    later.add_reading(0, 60.0)
    drive(later, 0, 60.0, 45.0, 2.0)  # still short of the 3-day window
    assert predict(later, 45.0) is None


def test_early_estimate_from_the_open_tank() -> None:
    model = Model(CAP)
    model.add_reading(0, 60.0)
    drive(model, 0, 60.0, 40.0, 4.0)  # 20 L over 4 days = 5 L/day
    result = predict(model, 40.0)
    assert result is not None, "4 days and a third of a tank is enough to answer"
    assert result.basis == "current tank"
    assert result.is_early is True
    assert result.segments == 0, "no tank has completed yet"
    assert round(result.avg_consumption, 1) == 5.0
    assert round(result.days_until_empty, 0) == 8.0  # 40 L / 5 L per day
    assert 0 < result.confidence <= const.EARLY_CONFIDENCE_CAP


def test_one_completed_tank_is_still_early() -> None:
    """MIN_SEGMENTS_FOR_PREDICTION is 2, so one tank alone stays provisional."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    end = drive(model, 0, 60.0, 10.0, 10.0)
    full_tank(model, end + 60)
    drive(model, end + 60, 60.0, 50.0, 2.0)  # too short to count on its own
    result = predict(model, 50.0)
    assert result is not None, "one completed tank is still worth answering from"
    assert result.segments == 1
    assert result.basis == "one tank"
    assert result.confidence <= const.EARLY_CONFIDENCE_CAP

    # Once the open tank clears the window it is folded in, and says so.
    drive(model, end + 60, 60.0, 40.0, 5.0)
    assert predict(model, 40.0).basis == "current tank"


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


def _two_calm_tanks() -> tuple[object, float]:
    """A car with two completed tanks at a steady 5 L/day. Returns (model, ts)."""
    model = Model(CAP)
    model.add_reading(0, 60.0)
    ts = drive(model, 0, 60.0, 10.0, 10.0)
    full_tank(model, ts + 60)
    ts = drive(model, ts + 60, 60.0, 10.0, 10.0)
    full_tank(model, ts + 60)
    return model, ts + 60


def test_the_open_tank_keeps_calibrating_between_refuels() -> None:
    """Habit change must show up without waiting for the next fill-up."""
    model, ts = _two_calm_tanks()
    calm = predict(model, 60.0)
    assert round(calm.avg_consumption, 1) == 5.0

    # Now drive twice as hard, for long enough that it means something.
    drive(model, ts, 60.0, 20.0, 4.0)           # 40 L in 4 days = 10 L/day
    busy = predict(model, 20.0)
    assert busy.basis == "tanks", "completed tanks still make it 'ready'"
    assert busy.avg_consumption > 5.0, "the open tank must pull the rate up"
    # ...but only in proportion to the 4 days it covers against a 10-day tank,
    # not as though it were a whole tank's worth of evidence.
    assert busy.avg_consumption < 10.0


def test_bursty_driving_does_not_swing_the_prediction() -> None:
    """The case that motivated the weighting: drive hard one day, then park.

    Before weighting by time covered, a single 20 L Saturday took a settled
    12-day prediction down to 3.2 days — the open tank got the same EWMA weight
    as a completed tank despite covering one day of it.
    """
    model, ts = _two_calm_tanks()
    settled = predict(model, 60.0).days_until_empty
    assert round(settled) == 12

    # One big day out, then the car sits on the drive for a week.
    model.add_reading(ts + DAY, 40.0)           # 20 L in a single day
    after_trip = predict(model, 40.0).days_until_empty
    assert after_trip < settled, "a heavy day should shorten the estimate"
    assert after_trip > settled / 2, (
        f"but not halve it on one day's evidence (got {after_trip})"
    )

    for day in range(2, 9):                     # parked, level unchanged
        model.add_reading(ts + day * DAY, 40.0)
    after_week = predict(model, 40.0).days_until_empty
    assert after_week > after_trip, "a quiet week should stretch it back out"


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

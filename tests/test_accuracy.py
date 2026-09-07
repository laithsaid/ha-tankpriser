"""Tests for grading the prediction against what the car actually did.

Run with: python tests/test_accuracy.py

`accuracy.py` and `prediction.py` are both pure, so this needs no Home
Assistant. The point of most of these is that the *grader* is right — a grader
that flatters the model is worse than none, and the specific way it could
flatter or libel it is by comparing "days until empty" against a refuel that
happened at a quarter tank.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
DAY = 86400.0
CAPACITY = 60.0


def _load():
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    loaded = {}
    for name in ("const", "prediction", "accuracy"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["accuracy"], loaded["prediction"]


acc, pred = _load()
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def tank(start_day: float, days: float, litres: float, start_litres: float = 55.0):
    """One completed tank: `litres` burnt over `days`, starting at `start_litres`."""
    return pred.Segment(
        start_ts=start_day * DAY,
        end_ts=(start_day + days) * DAY,
        start_litres=start_litres,
        end_litres=start_litres - litres,
    )


def steady(n: int, days: float = 10.0, litres: float = 45.0):
    """`n` identical tanks — a car driven exactly the same way every time."""
    return [tank(i * (days + 0.5), days, litres) for i in range(n)]


# --- the grader itself -----------------------------------------------------
def test_a_perfectly_predictable_car_scores_near_zero() -> None:
    print("a car that burns the same amount every tank")
    report = acc.backtest(CAPACITY, steady(6), car="Metronome")
    check("every tank after the first is graded", report.sample_count == 5, report.sample_count)
    check(
        "and the model is right about all of them",
        report.mean_abs_error_pct is not None and report.mean_abs_error_pct < 0.5,
        report.mean_abs_error_pct,
    )
    check("with no lean either way", abs(report.bias_pct) < 0.5, report.bias_pct)
    check("verdict says so", "matching reality" in report.verdict, report.verdict)


def test_the_first_tank_is_never_graded() -> None:
    print("nothing to predict the first tank from")
    one = acc.backtest(CAPACITY, steady(1))
    check("one tank scores nothing", one.sample_count == 0, one.sample_count)
    check("and says so rather than inventing a number", one.verdict == "not enough data")
    check("two tanks score one", acc.backtest(CAPACITY, steady(2)).sample_count == 1)


def test_refuelling_early_does_not_look_like_an_error() -> None:
    """The trap this whole module is shaped around.

    Both cars burn 4.5 L/day. One runs its tank down to 10 L, the other tops up
    at 40 L — half a tank — every time. Their driving is identical, so their
    scores must be too: anything else is measuring a habit, not a model.
    """
    print("a driver who tops up early must not be graded as a bad prediction")
    thorough = [tank(i * 11, 10.0, 45.0, start_litres=55.0) for i in range(5)]
    cautious = [tank(i * 4, 3.333, 15.0, start_litres=55.0) for i in range(5)]

    deep = acc.backtest(CAPACITY, thorough)
    shallow = acc.backtest(CAPACITY, cautious)
    check(
        "the cautious driver is not reported as wildly optimistic",
        abs(shallow.bias_pct) < 1.0,
        shallow.bias_pct,
    )
    check(
        "and both drivers score about the same",
        abs(deep.bias_pct - shallow.bias_pct) < 1.0,
        (deep.bias_pct, shallow.bias_pct),
    )


def test_a_car_that_suddenly_drives_more_reads_as_optimistic() -> None:
    print("consumption doubles: the model should be caught out, in the right direction")
    # Four calm tanks, then three where the same tank empties twice as fast.
    calm = [tank(i * 11, 10.0, 45.0) for i in range(4)]
    busy = [tank(44 + i * 6, 5.0, 45.0) for i in range(3)]
    report = acc.backtest(CAPACITY, calm + busy)

    late = [s for s in report.scored if s.index >= 4]
    check("the busy tanks are graded", len(late) == 3, len(late))
    check(
        "and every one of them reads optimistic",
        all(s.optimistic for s in late),
        [s.error_pct for s in late],
    )
    check("the summary agrees", report.bias_pct > 0, report.bias_pct)
    check(
        "and names the direction in words",
        "optimistic" in report.verdict,
        report.verdict,
    )


def test_a_car_that_suddenly_drives_less_reads_as_pessimistic() -> None:
    print("the mirror image, so the sign convention is not accidental")
    busy = [tank(i * 6, 5.0, 45.0) for i in range(4)]
    calm = [tank(24 + i * 11, 10.0, 45.0) for i in range(3)]
    report = acc.backtest(CAPACITY, busy + calm)
    check("it leans the other way", report.bias_pct < 0, report.bias_pct)
    check("and says so", "pessimistic" in report.verdict, report.verdict)


def test_noisy_but_centred_is_told_apart_from_consistently_wrong() -> None:
    print("the distinction the whole report exists to make")
    # Alternating long and short tanks: individually wrong, on average right.
    noisy = []
    day = 0.0
    for index in range(9):
        days = 14.0 if index % 2 else 6.0
        noisy.append(tank(day, days, 45.0))
        day += days + 0.5
    report = acc.backtest(CAPACITY, noisy)
    check(
        "the individual tanks are well off",
        report.mean_abs_error_pct > 15,
        report.mean_abs_error_pct,
    )
    check("but there is no consistent lean", abs(report.bias_pct) < 25, report.bias_pct)
    check(
        "and the verdict blames the driving, not the model",
        "irregular driving" in report.verdict or "matching reality" in report.verdict,
        report.verdict,
    )


# --- the bookkeeping -------------------------------------------------------
def test_what_each_score_carries() -> None:
    print("a single graded tank")
    report = acc.backtest(CAPACITY, steady(4))
    first = report.scored[0]
    check("it knows which tanks it had", first.tanks_known == 1, first.tanks_known)
    check("it records the basis", first.basis in ("tanks", "one tank", "current tank"), first.basis)
    check("actual rate is litres over days", abs(first.actual_rate - 4.5) < 0.01, first.actual_rate)
    check(
        "predicted days is about the actual days",
        abs(first.predicted_days - first.actual_days) < 0.1,
        (first.predicted_days, first.actual_days),
    )
    check("and the tank did not finish low", first.finished_low is False)


def test_a_tank_run_almost_dry_is_flagged() -> None:
    print("how close the driver actually cuts it")
    dry = [tank(i * 11, 10.0, 50.0, start_litres=55.0) for i in range(4)]
    report = acc.backtest(CAPACITY, dry)
    check("ending at 5 L of 60 counts as low", report.low_finishes == 3, report.low_finishes)


def test_rubbish_tanks_are_not_graded() -> None:
    print("segments the model itself ignores must not be scored")
    good = steady(3)
    # A refuel logged twice within an hour, and a tank that consumed nothing.
    blip = pred.Segment(start_ts=100 * DAY, end_ts=100 * DAY + 600, start_litres=55.0, end_litres=54.0)
    idle = pred.Segment(start_ts=110 * DAY, end_ts=120 * DAY, start_litres=55.0, end_litres=55.0)
    report = acc.backtest(CAPACITY, good + [blip, idle])
    check(
        "neither is graded",
        report.sample_count == 2,
        [s.index for s in report.scored],
    )


def test_the_trend_needs_enough_tanks() -> None:
    print("trend")
    check("three tanks give no trend", acc.backtest(CAPACITY, steady(4)).trend_pct is None)
    improving = acc.backtest(CAPACITY, steady(8))
    check("eight tanks give one", improving.trend_pct is not None, improving.trend_pct)


def test_the_report_serialises() -> None:
    print("as_dict, for the service and the card")
    data = acc.as_dict(acc.backtest(CAPACITY, steady(5), car="Passat"))
    check("names the car", data["car"] == "Passat")
    check("carries the headline numbers", data["bias_pct"] is not None and data["verdict"])
    check("and one entry per graded tank", len(data["tanks"]) == 4, len(data["tanks"]))
    import json

    check("and survives JSON, as a service response must", bool(json.dumps(data)))


# --- the correction --------------------------------------------------------
def drifting(days_each: list[float], litres: float = 45.0):
    """Tanks of steadily changing length — a habit that keeps moving."""
    out, day = [], 0.0
    for days in days_each:
        out.append(tank(day, days, litres))
        day += days + 0.5
    return out


def test_the_correction_fixes_a_standing_bias() -> None:
    """The case the correction exists for: a trend the EWMA can never catch.

    Consumption that keeps creeping up leaves the model permanently behind — it
    is always averaging a past that was slower than the present. That residual
    never resolves itself, so it is worth correcting.
    """
    print("consumption creeping up tank after tank")
    segments = drifting([14, 13, 12, 11, 10, 9, 8, 7, 6.5, 6])
    raw = acc.backtest(CAPACITY, segments)
    fixed = acc.backtest(CAPACITY, segments, calibrate=True)

    late_raw = [s.error_pct for s in raw.scored if s.index >= 5]
    late_fixed = [s.error_pct for s in fixed.scored if s.index >= 5]
    raw_mean = sum(late_raw) / len(late_raw)
    fixed_mean = sum(late_fixed) / len(late_fixed)
    check(
        "the raw model has a standing bias once it is warmed up",
        raw_mean > 15,
        raw_mean,
    )
    check(
        "and the correction removes a real part of it",
        fixed_mean < raw_mean - 4,
        (raw_mean, fixed_mean),
    )
    check("the factor shortens the projection", fixed.calibration > 1.0, fixed.calibration)
    check("the report says it was calibrated", fixed.calibrated is True)
    check("and the raw one says it was not", raw.calibrated is False)


def test_it_works_in_the_other_direction_too() -> None:
    print("consumption creeping down")
    segments = drifting([6, 6.5, 7, 8, 9, 10, 11, 12, 13, 14])
    raw = acc.backtest(CAPACITY, segments)
    fixed = acc.backtest(CAPACITY, segments, calibrate=True)
    check("the raw model leans pessimistic", raw.bias_pct < -5, raw.bias_pct)
    check("the correction pulls it back", abs(fixed.bias_pct) < abs(raw.bias_pct))
    check("and lengthens the projection", fixed.calibration < 1.0, fixed.calibration)


def test_a_holiday_does_not_move_the_correction() -> None:
    """A fortnight in France must leave the correction alone.

    Two tanks burnt at three times the usual rate are not evidence that the
    model is wrong about this car — they are evidence of a holiday. A mean lets
    them push the factor to 1.2 and leaves the prediction pessimistic for weeks
    after coming home, which is worse than not correcting at all.
    """
    print("an excursion is not a habit")
    segments = (
        [tank(i * 10.5, 10.0, 45.0) for i in range(10)]
        + [tank(105 + i * 3.5, 3.0, 45.0) for i in range(2)]
        + [tank(112 + i * 10.5, 10.0, 45.0) for i in range(10)]
    )
    raw = acc.backtest(CAPACITY, segments)
    fixed = acc.backtest(CAPACITY, segments, calibrate=True)
    check(
        "the trip barely moves the factor",
        abs(fixed.calibration - 1.0) < 0.02,
        fixed.calibration,
    )
    after_raw = [s.error_pct for s in raw.scored if s.index >= 12]
    after_fixed = [s.error_pct for s in fixed.scored if s.index >= 12]
    raw_mean = sum(after_raw) / len(after_raw)
    fixed_mean = sum(after_fixed) / len(after_fixed)
    check(
        "so the tanks after the trip are no worse than with no correction at all",
        abs(fixed_mean) <= abs(raw_mean) + 0.5,
        (raw_mean, fixed_mean),
    )


def test_transition_lag_is_left_to_the_base_model() -> None:
    """A permanent change of habit is the EWMA's job, not the correction's.

    Move house, take a new job: consumption steps up and stays up. The model
    lags for two or three tanks and then catches up entirely, so there is
    nothing standing left to correct — and a correction fitted to that
    transition would still be leaning long after the model had recovered.
    """
    print("a permanent step change settles by itself")
    segments = [tank(i * 12.5, 12.0, 45.0) for i in range(4)]
    day = 50.0
    for _ in range(10):
        segments.append(tank(day, 7.0, 45.0))
        day += 7.5

    raw = acc.backtest(CAPACITY, segments)
    settled = [s.error_pct for s in raw.scored if s.index >= 7]
    settled_mean = sum(settled) / len(settled)
    check(
        "the raw model has caught up on its own",
        abs(settled_mean) < 5,
        settled_mean,
    )
    fixed = acc.backtest(CAPACITY, segments, calibrate=True)
    check(
        "so the correction stays out of it",
        abs(fixed.calibration - 1.0) < 0.06,
        fixed.calibration,
    )


def test_it_cannot_damage_a_model_that_is_already_right() -> None:
    """The one failure that would be unforgivable: making good predictions worse."""
    print("an already-accurate car")
    raw = acc.backtest(CAPACITY, steady(8))
    fixed = acc.backtest(CAPACITY, steady(8), calibrate=True)
    check("the factor stays at 1", abs(fixed.calibration - 1.0) < 0.01, fixed.calibration)
    check(
        "and the scores are unchanged",
        abs(fixed.mean_abs_error_pct - raw.mean_abs_error_pct) < 0.5,
        (raw.mean_abs_error_pct, fixed.mean_abs_error_pct),
    )


def test_it_waits_for_enough_evidence() -> None:
    print("a few tanks that happen to agree are not a lean")
    scored = acc.backtest(CAPACITY, steady(8)).scored
    for count in range(acc.CALIBRATION_MIN_TANKS):
        check(
            f"{count} graded tanks means no correction",
            acc.calibration_from(scored[:count]) == 1.0,
            count,
        )
    check(
        "the minimum is high enough that one freak tank cannot be the median",
        acc.CALIBRATION_MIN_TANKS >= 5,
        acc.CALIBRATION_MIN_TANKS,
    )


def test_the_correction_is_damped_and_capped() -> None:
    print("a wild measurement must not produce a wild correction")

    class Fake:
        def __init__(self, error_pct, calibration=1.0):
            self.error_pct = error_pct
            self.calibration = calibration

    huge = [Fake(400.0) for _ in range(6)]
    check(
        "a 400 % lean is capped, not obeyed",
        acc.calibration_from(huge) == round(1 + acc.CALIBRATION_MAX, 3),
        acc.calibration_from(huge),
    )
    check(
        "and so is the other direction",
        acc.calibration_from([Fake(-90.0) for _ in range(6)])
        == round(1 - acc.CALIBRATION_MAX, 3),
    )
    modest = [Fake(20.0) for _ in range(6)]
    check(
        "an ordinary lean is only half-corrected, so it converges",
        abs(acc.calibration_from(modest) - 1.10) < 0.001,
        acc.calibration_from(modest),
    )


def test_the_factor_compounds_with_the_one_already_applied() -> None:
    """The arithmetic that decides whether it converges or stalls.

    A tank predicted with a 1.1 factor that still came in 10 % optimistic was
    really driven at 1.1 x 1.1 of the raw rate. Reading the residual alone and
    applying it to the raw model would under-correct for ever.
    """
    print("residual errors are compounded with the factor that produced them")

    class Fake:
        def __init__(self, error_pct, calibration):
            self.error_pct = error_pct
            self.calibration = calibration

    residual = [Fake(10.0, 1.1) for _ in range(6)]
    # wanted = 1.1 * 1.1 = 1.21; damped by half -> 1 + 0.21/2 = 1.105
    check(
        "the new factor exceeds the old one",
        abs(acc.calibration_from(residual) - 1.105) < 0.002,
        acc.calibration_from(residual),
    )


def test_the_correction_reaches_the_prediction() -> None:
    print("the factor is actually applied, and reported")
    model = pred.ConsumptionModel(CAPACITY, segments=steady(4))
    plain = pred.predict(model, 30.0)
    faster = pred.predict(model, 30.0, 1.25)
    check("a higher factor shortens the projection",
          faster.days_until_empty < plain.days_until_empty,
          (plain.days_until_empty, faster.days_until_empty))
    check("by the factor, near enough",
          abs(faster.days_until_empty - plain.days_until_empty / 1.25) < 0.15,
          (plain.days_until_empty, faster.days_until_empty))
    check("and it is reported", faster.calibration == 1.25, faster.calibration)
    check("no factor is reported as 1.0", plain.calibration == 1.0)
    check(
        "a nonsense factor is ignored rather than obeyed",
        pred.predict(model, 30.0, 0).days_until_empty == plain.days_until_empty,
    )
    check(
        "with no odometer the reported L/day is the corrected rate, so it moves "
        "with the projection rather than contradicting it",
        abs(faster.avg_consumption - plain.avg_consumption * 1.25) < 0.05,
        (plain.avg_consumption, faster.avg_consumption),
    )

    # With an odometer the headline figure is L/100 km — a property of the car
    # and the road. A persistent error in *days* usually means the driving
    # changed, not that the engine did, so that number must not be bent.
    with_odo = []
    for index in range(4):
        segment = tank(index * 10.5, 10.0, 45.0)
        segment.odo_start = index * 600.0
        segment.odo_end = index * 600.0 + 500.0
        with_odo.append(segment)
    odo_model = pred.ConsumptionModel(CAPACITY, segments=with_odo)
    odo_plain = pred.predict(odo_model, 30.0)
    odo_fixed = pred.predict(odo_model, 30.0, 1.25)
    check("the odometer path reports L/100 km", odo_plain.consumption_unit == "L/100 km")
    check(
        "and the correction leaves it untouched",
        odo_fixed.avg_consumption == odo_plain.avg_consumption,
        (odo_plain.avg_consumption, odo_fixed.avg_consumption),
    )
    check(
        "while still shortening the projection",
        odo_fixed.days_until_empty < odo_plain.days_until_empty,
        (odo_plain.days_until_empty, odo_fixed.days_until_empty),
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print(f"all {len(tests)} accuracy test groups passed")

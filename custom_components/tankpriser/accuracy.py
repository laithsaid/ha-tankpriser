"""Was the prediction right? Scored against what the car actually did.

A prediction nobody checks is a number, not a measurement. This grades the
consumption model against reality, tank by tank, and says whether it is
*consistently* off — which is the only kind of wrong you can do anything about.

**What is compared, and why it is not the obvious thing.** The sensor answers
"days until empty", but nobody drives to empty: a tank ends when its owner
decides to fill up, at a quarter full or wherever. Comparing "we said 11 days"
against "you refuelled after 8" would therefore measure a refuelling habit and
report a perfect model as wildly optimistic. So what is scored is the *rate*:

    at the start of tank k, using only tanks 0..k-1, the model believed some
    litres-per-day. Tank k then really burned C litres over D days. Ask how
    long the model would have said those C litres would last, and compare with
    D.

That is walk-forward: each tank is graded by a model that had not yet seen it,
which is the only honest way to grade a model on its own training data.

**It works on history you already have.** Completed tanks are persisted, so the
whole grading runs retroactively over the stored segments — an answer on the
first run rather than after months of collecting.

**Sign convention: positive error means optimistic** — the model said the fuel
would last longer than it did. That is the direction that strands you, so it is
the direction worth naming.

The measurement is only half of it: `calibration_from` turns a repeated lean
into a correction the prediction actually applies — damped, capped, taken from
the *typical* tank rather than the average one, and graded walk-forward so it
can be judged rather than trusted.

The division of labour matters. The estimate in `prediction.py` already folds in
the tank in progress, so it absorbs a change of behaviour within days; this
correction is the slow half, and exists for a bias that *never* resolves. Let
either do the other's job and both get worse — a correction fitted to a holiday
leaves the prediction pessimistic for weeks after the driver comes home.

Pure: no Home Assistant, no I/O. Tests in ``tests/test_accuracy.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from .const import MIN_SEGMENT_DAYS
from .prediction import ConsumptionModel, Segment, predict

# A tank that ends below this fraction is one the driver cut fine. Not an error
# in itself — it is context for whether optimism actually matters to them.
LOW_FINISH_FRACTION: Final = 0.10
# Inside this, a prediction is "good enough to plan a week around".
GOOD_ERROR_PCT: Final = 10.0

# --- correction ------------------------------------------------------------
# Grading is only half of it: a measured, repeated lean is something to fix, not
# just to report. The correction is a single multiplier on the predicted daily
# rate, and the three constants below are all about not trusting it too much.
#
# Fewest graded tanks before correcting at all. A handful of tanks can agree by
# accident, and the median below needs enough around it to have a middle worth
# taking: at five, one freak tank cannot be it.
CALIBRATION_MIN_TANKS: Final = 5
# How much of the measured lean to actually apply. Half, because some of any
# measured bias is noise, and a half-correction that is wrong costs half as
# much while a half-correction that is right still removes most of the error
# over successive tanks — it converges rather than overshooting.
CALIBRATION_DAMPING: Final = 0.5
# Never move the projection by more than this, whatever the measurement says.
# A bigger apparent lean than this is not a miscalibrated model, it is a car
# whose use has changed completely, and the honest response to that is to let
# the ordinary learning catch up rather than to bend the answer.
CALIBRATION_MAX: Final = 0.25


@dataclass(frozen=True)
class TankScore:
    """One completed tank, graded by a model that had not yet seen it."""

    index: int
    started: float
    ended: float
    consumed_litres: float
    actual_days: float
    # What the model believed then, and what it would have claimed.
    predicted_rate: float          # L/day
    actual_rate: float             # L/day
    predicted_days: float          # how long it would have said `consumed` lasts
    error_pct: float               # + = optimistic (predicted longer than reality)
    # What it was working from at the time, so a bad score can be read in
    # context — the first grade of a model with one tank behind it means little.
    tanks_known: int
    confidence: float
    basis: str
    method: str
    # The correction in force when this tank was predicted (1.0 = none).
    calibration: float = 1.0
    # Did this tank end near empty? Not a fault; it says how much the driver
    # relies on the number being right.
    finished_low: bool = False

    @property
    def optimistic(self) -> bool:
        return self.error_pct > 0


@dataclass(frozen=True)
class AccuracyReport:
    """How a car's prediction has held up."""

    car: str
    scored: list[TankScore]
    # Typical size of the error, ignoring direction.
    mean_abs_error_pct: float | None = None
    # Mean *signed* error: the answer to "is it always off, or just noisy?".
    bias_pct: float | None = None
    worst_error_pct: float | None = None
    good_count: int = 0
    low_finishes: int = 0
    # Mean absolute error of the recent half minus the earlier half. Negative
    # means it is getting better as it learns, which is what should happen.
    trend_pct: float | None = None
    # Whether these scores were produced with the correction applied.
    calibrated: bool = False
    # The correction that applies to this car's *next* prediction. 1.0 = none.
    calibration: float = 1.0
    # A plain-language reading of the above; see `verdict_of`.
    verdict: str = "not enough data"

    @property
    def sample_count(self) -> int:
        return len(self.scored)


def calibration_from(scored: list[TankScore]) -> float:
    """The multiplier to apply to the daily rate, learned from past tanks.

    The arithmetic falls out of how the error is defined. ``error_pct`` compares
    predicted days with actual days, and for the same litres that is exactly the
    inverse ratio of the rates — so a tank that came in 20 % optimistic was
    driven at 1.2x the rate the model used for it.

    That "rate the model used" already includes whatever correction was in force
    at the time, which is why each score is multiplied by its own
    ``calibration`` before averaging. Skipping that step measures the residual
    error of a corrected model and then applies it to the *raw* one, which
    silently under-corrects for ever — the factor converges to half the lean
    instead of removing it.

    The result is then damped and capped, because a measured bias is part signal
    and part noise with no way to tell them apart from one number. Half of the
    remaining lean goes each time the factor is recomputed, so it approaches the
    answer over successive tanks rather than overshooting it.
    """
    if len(scored) < CALIBRATION_MIN_TANKS:
        return 1.0
    ratios = sorted(
        (1.0 + s.error_pct / 100.0) * (s.calibration or 1.0) for s in scored
    )
    # The MEDIAN, not the mean. A fortnight driving to France is two tanks burnt
    # at three times the usual rate, and a mean lets those two rewrite the
    # correction for a car whose habits have not changed at all — leaving the
    # prediction pessimistic for weeks afterwards, worse than no correction.
    # The median ignores a minority of freak tanks entirely, and only shifts
    # once the unusual driving stops being unusual, which is exactly when a
    # correction becomes the right response.
    middle = len(ratios) // 2
    wanted = (
        ratios[middle]
        if len(ratios) % 2
        else (ratios[middle - 1] + ratios[middle]) / 2.0
    )
    adjustment = max(
        -CALIBRATION_MAX,
        min(CALIBRATION_MAX, (wanted - 1.0) * CALIBRATION_DAMPING),
    )
    return round(1.0 + adjustment, 3)


def backtest(
    capacity_l: float,
    segments: list[Segment],
    car: str = "",
    calibrate: bool = False,
) -> AccuracyReport:
    """Grade every completed tank against the model as it stood before it.

    The first tank can never be graded — there is nothing behind it to predict
    from — so a car with one refuel produces an empty report rather than a
    fabricated score.

    With ``calibrate``, each tank is graded by a model carrying the correction
    that the tanks *before* it had earned. That keeps the comparison honest:
    deriving one factor from every tank and then scoring those same tanks with
    it would be marking your own homework, and would flatter the correction
    exactly when it deserves it least.
    """
    scored: list[TankScore] = []
    for index, segment in enumerate(segments):
        factor = calibration_from(scored) if calibrate else 1.0
        score = _score(capacity_l, segments[:index], segment, index, factor)
        if score is not None:
            scored.append(score)
    return _summarise(car, scored, calibrated=calibrate)


def _score(
    capacity_l: float,
    history: list[Segment],
    segment: Segment,
    index: int,
    calibration: float = 1.0,
) -> TankScore | None:
    """Grade one tank using only the tanks before it, or None if ungradeable."""
    if segment.duration_days < MIN_SEGMENT_DAYS or segment.consumed_litres <= 0:
        # Too short or non-consuming: the model ignores these when learning, so
        # grading against them would be scoring noise.
        return None

    model = ConsumptionModel(capacity_l, segments=list(history))
    # Asked as of this tank's *start*: what the model then believed, applied to
    # the fuel that was in the tank at that moment.
    prediction = predict(model, segment.start_litres, calibration)
    if prediction is None or not prediction.days_until_empty:
        return None

    # `predict` reports efficiency in L/100 km when it can, so the daily rate is
    # recovered from the projection instead — the one number that is always in
    # litres per day.
    predicted_rate = segment.start_litres / prediction.days_until_empty
    if predicted_rate <= 0:
        return None

    actual_rate = segment.consumed_litres / segment.duration_days
    predicted_days = segment.consumed_litres / predicted_rate
    error_pct = (predicted_days - segment.duration_days) / segment.duration_days * 100

    return TankScore(
        index=index,
        started=segment.start_ts,
        ended=segment.end_ts,
        consumed_litres=round(segment.consumed_litres, 2),
        actual_days=round(segment.duration_days, 2),
        predicted_rate=round(predicted_rate, 3),
        actual_rate=round(actual_rate, 3),
        predicted_days=round(predicted_days, 2),
        error_pct=round(error_pct, 1),
        tanks_known=len(history),
        confidence=prediction.confidence,
        basis=prediction.basis,
        method=prediction.method,
        calibration=prediction.calibration,
        finished_low=segment.end_litres <= LOW_FINISH_FRACTION * capacity_l,
    )


def _summarise(
    car: str, scored: list[TankScore], calibrated: bool = False
) -> AccuracyReport:
    """Turn the graded tanks into the few numbers worth reading."""
    if not scored:
        return AccuracyReport(car=car, scored=[], calibrated=calibrated)

    errors = [s.error_pct for s in scored]
    absolute = [abs(e) for e in errors]
    report = AccuracyReport(
        car=car,
        scored=scored,
        mean_abs_error_pct=round(sum(absolute) / len(absolute), 1),
        bias_pct=round(sum(errors) / len(errors), 1),
        worst_error_pct=max(errors, key=abs),
        good_count=sum(1 for e in absolute if e <= GOOD_ERROR_PCT),
        low_finishes=sum(1 for s in scored if s.finished_low),
        trend_pct=_trend(absolute),
        calibrated=calibrated,
        calibration=calibration_from(scored),
    )
    return replace(report, verdict=verdict_of(report))


def _trend(absolute: list[float]) -> float | None:
    """Recent typical error minus earlier typical error, or None if too few.

    Four tanks is the fewest that gives two halves of two, and two of anything
    is already a shaky average — so this is a hint, labelled as one, not a
    finding.
    """
    if len(absolute) < 4:
        return None
    half = len(absolute) // 2
    earlier = sum(absolute[:half]) / half
    recent = sum(absolute[half:]) / (len(absolute) - half)
    return round(recent - earlier, 1)


def verdict_of(report: AccuracyReport) -> str:
    """One line saying what the numbers mean, for someone not reading a table.

    Bias is the headline rather than the average error, because a model that is
    off by 20 % in both directions is merely noisy — irregular driving does that
    — while one that is off by 20 % the *same way* every time is wrong in a way
    that could be corrected.
    """
    if not report.scored or report.bias_pct is None:
        return "not enough data"
    if report.sample_count < 3:
        return "too few tanks to say — check back after a couple more refuels"

    bias = report.bias_pct
    spread = report.mean_abs_error_pct or 0.0
    direction = "optimistic" if bias > 0 else "pessimistic"

    if abs(bias) <= GOOD_ERROR_PCT and spread <= 20:
        return "matching reality — no consistent lean, and the spread is small"
    if abs(bias) <= GOOD_ERROR_PCT:
        return (
            "no consistent lean, but the individual tanks vary a lot — that is "
            "irregular driving rather than a broken model"
        )
    if abs(bias) > 25:
        return (
            f"consistently {direction} by about {abs(bias):.0f} % — the model is "
            "leaning the same way nearly every tank"
        )
    return f"slightly {direction}, by about {abs(bias):.0f} % on average"


def as_dict(report: AccuracyReport) -> dict:
    """The report as plain data, for a service response or a card."""
    return {
        "car": report.car,
        "verdict": report.verdict,
        "tanks_scored": report.sample_count,
        "calibrated": report.calibrated,
        "calibration": report.calibration,
        "mean_abs_error_pct": report.mean_abs_error_pct,
        "bias_pct": report.bias_pct,
        "worst_error_pct": report.worst_error_pct,
        "within_10_pct": report.good_count,
        "low_finishes": report.low_finishes,
        "trend_pct": report.trend_pct,
        "tanks": [
            {
                "index": s.index,
                "started": s.started,
                "ended": s.ended,
                "consumed_litres": s.consumed_litres,
                "actual_days": s.actual_days,
                "predicted_days": s.predicted_days,
                "predicted_rate": s.predicted_rate,
                "actual_rate": s.actual_rate,
                "error_pct": s.error_pct,
                "tanks_known": s.tanks_known,
                "confidence": s.confidence,
                "basis": s.basis,
                "method": s.method,
                "calibration": s.calibration,
                "finished_low": s.finished_low,
            }
            for s in report.scored
        ],
    }

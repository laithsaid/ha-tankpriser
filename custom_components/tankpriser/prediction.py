"""Fuel-consumption learning and prediction — pure, Home-Assistant-free.

This module deliberately imports nothing from ``homeassistant`` so the whole
learning/estimation core can be unit-tested offline and reasoned about on its
own. The HA glue (state tracking, persistence, entities) lives in
``consumption.py`` and drives the model defined here.

The model is simple on purpose:

* Feed it normalised fuel-*litre* readings over time (:meth:`ConsumptionModel.add_reading`).
* An upward jump of at least ``REFUEL_MIN_JUMP_FRACTION`` of the tank is a
  refuel; it closes the current tank into a completed :class:`Segment`.
* :func:`predict` turns those tanks into a days-until-empty estimate using an
  exponentially-weighted average (recent weighted higher), reporting L/100 km
  when an odometer is available and L/day otherwise.

The tank **in progress** counts as an observation too, once it has run long
enough and burnt enough fuel to mean something. Without that, nothing could be
predicted until two refuels had happened — weeks of `unknown` while the data
needed for a rough answer was already in hand. Estimates resting on a partial
tank say so (``Prediction.basis``) and have their confidence capped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .const import (
    CONFIDENCE_TARGET_SEGMENTS,
    EARLY_CONFIDENCE_CAP,
    EARLY_MIN_CONSUMED_FRACTION,
    EARLY_MIN_DAYS,
    EWMA_ALPHA,
    LEVEL_UNIT_PERCENT,
    MAX_SEGMENTS,
    MIN_SEGMENT_DAYS,
    MIN_SEGMENTS_FOR_PREDICTION,
    REFUEL_MIN_JUMP_FRACTION,
)

_SECONDS_PER_DAY = 86_400.0


# --- value extraction / normalisation --------------------------------------
def dig(obj: Any, path: str | None) -> Any:
    """Follow a dotted attribute ``path`` into nested dicts.

    Empty/None path returns ``obj`` unchanged. A missing key anywhere returns
    ``None`` rather than raising, so a temporarily-absent attribute is treated
    as "no reading" instead of crashing the tracker.
    """
    if not path:
        return obj
    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def to_litres(value: Any, unit: str, capacity_l: float) -> float | None:
    """Normalise a raw level reading to litres, or ``None`` if not numeric."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    if unit == LEVEL_UNIT_PERCENT:
        pct = max(0.0, min(num, 100.0))
        return pct / 100.0 * capacity_l
    return max(0.0, num)


def to_float(value: Any) -> float | None:
    """Best-effort float, tolerating odometers reported as '12345.6 km' etc."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    # Pull a leading number out of strings like "12345,6 km": optional sign,
    # digits, at most one decimal point.
    text = str(value).strip().replace(",", ".")
    num = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch == "-" and not num:
            num += ch
        elif ch == "." and "." not in num:
            num += ch
        else:
            break
    try:
        return float(num)
    except ValueError:
        return None


# --- data structures --------------------------------------------------------
@dataclass
class Sample:
    """One normalised reading."""

    ts: float
    litres: float
    odo: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "litres": self.litres, "odo": self.odo}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Sample":
        return cls(
            ts=float(raw["ts"]),
            litres=float(raw["litres"]),
            odo=None if raw.get("odo") is None else float(raw["odo"]),
        )


@dataclass
class Segment:
    """One completed tank: from just after a refuel to just before the next."""

    start_ts: float
    end_ts: float
    start_litres: float
    end_litres: float
    odo_start: float | None = None
    odo_end: float | None = None

    @property
    def consumed_litres(self) -> float:
        return max(0.0, self.start_litres - self.end_litres)

    @property
    def duration_days(self) -> float:
        return max(0.0, (self.end_ts - self.start_ts) / _SECONDS_PER_DAY)

    @property
    def distance_km(self) -> float | None:
        if self.odo_start is None or self.odo_end is None:
            return None
        dist = self.odo_end - self.odo_start
        return dist if dist > 0 else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "start_litres": self.start_litres,
            "end_litres": self.end_litres,
            "odo_start": self.odo_start,
            "odo_end": self.odo_end,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Segment":
        return cls(
            start_ts=float(raw["start_ts"]),
            end_ts=float(raw["end_ts"]),
            start_litres=float(raw["start_litres"]),
            end_litres=float(raw["end_litres"]),
            odo_start=None if raw.get("odo_start") is None else float(raw["odo_start"]),
            odo_end=None if raw.get("odo_end") is None else float(raw["odo_end"]),
        )


@dataclass
class Prediction:
    """Result of :func:`predict`."""

    days_until_empty: float | None
    avg_consumption: float | None
    consumption_unit: str
    segments: int  # completed tanks behind the number
    confidence: float
    method: str  # "odometer" | "time"
    # "tanks" once MIN_SEGMENTS_FOR_PREDICTION tanks are complete; until then
    # "current tank", meaning the number leans on the tank in progress and will
    # move as it is refined.
    basis: str = "tanks"

    @property
    def is_early(self) -> bool:
        return self.basis != "tanks"

    def as_dict(self) -> dict[str, Any]:
        return {
            "days_until_empty": self.days_until_empty,
            "avg_consumption": self.avg_consumption,
            "consumption_unit": self.consumption_unit,
            "segments": self.segments,
            "confidence": self.confidence,
            "method": self.method,
            "basis": self.basis,
        }


@dataclass
class _Observation:
    """One stretch of driving the estimator can learn a rate from.

    A completed tank is one; so is the tank currently in progress. Unifying them
    is the point — the open tank then falls out of the same weighting as the
    newest (and therefore heaviest) data point, instead of being ignored until a
    refuel happens to close it.
    """

    consumed_litres: float
    days: float
    distance_km: float | None

    @property
    def litres_per_day(self) -> float:
        return self.consumed_litres / self.days


# --- the model --------------------------------------------------------------
class ConsumptionModel:
    """Turns a stream of litre readings into completed consumption segments."""

    def __init__(
        self,
        capacity_l: float,
        *,
        samples: list[Sample] | None = None,
        segments: list[Segment] | None = None,
        segment_start: Sample | None = None,
    ) -> None:
        self.capacity_l = float(capacity_l)
        self.samples: list[Sample] = samples if samples is not None else []
        self.segments: list[Segment] = segments if segments is not None else []
        self._segment_start: Sample | None = segment_start

    # -- ingestion ----------------------------------------------------------
    def add_reading(
        self, ts: float, litres: float, odo: float | None = None
    ) -> bool:
        """Feed one normalised reading; return True if it was a refuel.

        Readings must arrive in time order. A drop is ordinary consumption; a
        jump up of at least ``REFUEL_MIN_JUMP_FRACTION`` of the tank closes the
        current tank into a :class:`Segment` and starts a fresh one.
        """
        litres = max(0.0, min(float(litres), self.capacity_l))
        sample = Sample(ts, litres, odo)
        refuel = False

        if self.samples:
            prev = self.samples[-1]
            threshold = REFUEL_MIN_JUMP_FRACTION * self.capacity_l
            if litres - prev.litres >= threshold:
                self._close_segment(prev)
                self._segment_start = sample
                refuel = True
        else:
            self._segment_start = sample

        self.samples.append(sample)
        self._trim()
        return refuel

    def _close_segment(self, end: Sample) -> None:
        """Record the tank that ran from ``_segment_start`` to ``end``."""
        start = self._segment_start
        if start is None or end.ts <= start.ts:
            return
        if end.litres >= start.litres:
            # No net consumption (or a partial top-up we could not tell apart);
            # nothing useful to learn from this tank.
            return
        self.segments.append(
            Segment(
                start_ts=start.ts,
                end_ts=end.ts,
                start_litres=start.litres,
                end_litres=end.litres,
                odo_start=start.odo,
                odo_end=end.odo,
            )
        )
        if len(self.segments) > MAX_SEGMENTS:
            del self.segments[: len(self.segments) - MAX_SEGMENTS]

    def _trim(self) -> None:
        from .const import MAX_RAW_SAMPLES

        if len(self.samples) > MAX_RAW_SAMPLES:
            del self.samples[: len(self.samples) - MAX_RAW_SAMPLES]

    @property
    def current_litres(self) -> float | None:
        return self.samples[-1].litres if self.samples else None

    @property
    def open_tank_start(self) -> Sample | None:
        """The reading the tank in progress started from.

        Exposed because the estimator learns from the open tank as well as from
        completed ones — that is what lets it answer before two refuels have
        happened.
        """
        return self._segment_start

    def seed_demo(
        self,
        now_ts: float,
        tanks: int = 3,
        litres_per_day: float = 5.0,
        days_per_tank: float = 7.0,
    ) -> None:
        """Replace history with synthetic tanks so a prediction shows at once.

        For testing/demo only: real learning overwrites these as tanks complete.
        Each synthetic tank runs full → (full − consumed) over ``days_per_tank``.
        """
        tanks = max(1, int(tanks))
        seg_secs = max(0.0, days_per_tank) * _SECONDS_PER_DAY
        consumed = min(self.capacity_l, max(0.1, litres_per_day * days_per_tank))
        end_litres = max(0.0, self.capacity_l - consumed)
        segments = [
            Segment(
                start_ts=now_ts - i * seg_secs - seg_secs,
                end_ts=now_ts - i * seg_secs,
                start_litres=self.capacity_l,
                end_litres=end_litres,
            )
            for i in range(tanks)
        ]
        segments.reverse()  # oldest first
        self.segments = segments

    # -- (de)serialisation --------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {
            "capacity_l": self.capacity_l,
            "samples": [s.as_dict() for s in self.samples],
            "segments": [s.as_dict() for s in self.segments],
            "segment_start": self._segment_start.as_dict()
            if self._segment_start is not None
            else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], capacity_l: float) -> "ConsumptionModel":
        """Rebuild a model, taking capacity from the live config."""
        start_raw = raw.get("segment_start")
        return cls(
            capacity_l=capacity_l,
            samples=[Sample.from_dict(s) for s in raw.get("samples", [])],
            segments=[Segment.from_dict(s) for s in raw.get("segments", [])],
            segment_start=Sample.from_dict(start_raw) if start_raw else None,
        )


# --- estimation -------------------------------------------------------------
def _ewma(values: list[float], alpha: float = EWMA_ALPHA) -> float:
    """Exponentially-weighted mean, oldest→newest (recent weighted higher)."""
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _confidence(rates: list[float]) -> float:
    """0..1 from how many tanks we have and how consistent they are."""
    n = len(rates)
    if n == 0:
        return 0.0
    count_factor = min(1.0, n / CONFIDENCE_TARGET_SEGMENTS)
    mean = sum(rates) / n
    if mean <= 0:
        return 0.0
    variance = sum((r - mean) ** 2 for r in rates) / n
    cv = math.sqrt(variance) / mean  # coefficient of variation
    consistency = 1.0 / (1.0 + cv)
    return round(max(0.0, min(1.0, count_factor * consistency)), 2)


def _open_observation(model: ConsumptionModel) -> _Observation | None:
    """The tank in progress, if it has enough signal to learn a rate from.

    Both gates matter. Without the time one, a single long trip an hour after a
    fill-up would be projected as a daily habit; without the consumption one, a
    car parked for days would report a rate near zero — "empty in nine years".
    """
    start = model.open_tank_start
    latest = model.samples[-1] if model.samples else None
    if start is None or latest is None or latest.ts <= start.ts:
        return None

    days = (latest.ts - start.ts) / _SECONDS_PER_DAY
    consumed = start.litres - latest.litres
    if days < EARLY_MIN_DAYS:
        return None
    if consumed < EARLY_MIN_CONSUMED_FRACTION * model.capacity_l:
        return None

    distance = None
    if start.odo is not None and latest.odo is not None and latest.odo > start.odo:
        distance = latest.odo - start.odo
    return _Observation(consumed, days, distance)


def predict(
    model: ConsumptionModel, current_litres: float | None
) -> Prediction | None:
    """Estimate days until empty, or ``None`` when there is nothing to go on.

    Two tiers, because waiting for two refuels means weeks of `unknown` when the
    tank in progress can already answer the question roughly:

    * ``MIN_SEGMENTS_FOR_PREDICTION`` completed tanks or more → ``basis="tanks"``.
    * Fewer than that, but the open tank has real consumption in it →
      ``basis="current tank"``: the same arithmetic, confidence capped, and the
      caller is expected to present it as provisional.

    Either way the tank in progress is included as the *newest* observation, so
    the estimate keeps calibrating between refuels rather than only at them.

    Days-until-empty always uses a time-based (L/day) rate — it is the only
    thing that can project a calendar date. When every observation also carries
    an odometer distance we additionally report efficiency as L/100 km.
    """
    completed = [
        _Observation(s.consumed_litres, s.duration_days, s.distance_km)
        for s in model.segments
        if s.duration_days >= MIN_SEGMENT_DAYS
    ]
    open_tank = _open_observation(model)
    ready = len(completed) >= MIN_SEGMENTS_FOR_PREDICTION
    if not ready and open_tank is None:
        return None  # genuinely nothing to go on yet

    # Oldest → newest, so the EWMA leans on the most recent driving.
    observations = completed + ([open_tank] if open_tank is not None else [])
    daily_rate = _ewma([o.litres_per_day for o in observations])

    # Prefer odometer efficiency when every observation carries a distance.
    if observations and all(o.distance_km for o in observations):
        per_100 = [
            100.0 * o.consumed_litres / o.distance_km  # type: ignore[operator]
            for o in observations
        ]
        avg_consumption: float | None = round(_ewma(per_100), 2)
        consumption_unit = "L/100 km"
        method = "odometer"
    else:
        avg_consumption = round(daily_rate, 2)
        consumption_unit = "L/day"
        method = "time"

    if current_litres is None:
        current_litres = model.current_litres
    days: float | None = None
    if current_litres is not None and daily_rate > 0:
        days = round(current_litres / daily_rate, 1)

    # Confidence is earned by *completed* tanks: an estimate resting on one
    # partial tank must not look as trustworthy as one resting on six.
    confidence = _confidence([o.litres_per_day for o in completed])
    if not ready:
        confidence = min(
            EARLY_CONFIDENCE_CAP, max(confidence, round(EARLY_CONFIDENCE_CAP / 2, 2))
        )

    return Prediction(
        days_until_empty=days,
        avg_consumption=avg_consumption,
        consumption_unit=consumption_unit,
        segments=len(completed),
        confidence=confidence,
        method=method,
        basis="tanks" if ready else "current tank",
    )

"""When to say "fill up now" — the decision, with nothing of Home Assistant in it.

The integration already knows three things separately: how much fuel a car has
left, where that car is standing, and what every station near it charges. This
is the one place that puts them together, because the moment worth interrupting
someone is the intersection of all three — low tank, cheap forecourt, right
there — and none of the three alone is worth a notification.

The hard part is not the arithmetic, it is not becoming noise. A car parked low
on the drive would otherwise produce the same alert every half hour for a week,
and the second one of those is already worse than none: the reader learns to
swipe them away, and then misses the one that mattered. So the rules here are
mostly about *silence* — one alert per low tank, a cooldown, and a reset that
only comes when the car has actually been filled.

Pure, so all of that is unit-testable (``tests/test_fillup.py``); the Home
Assistant side is in ``notifications.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Below this fraction of a tank, a fill-up is worth mentioning. A default
# rather than a fixed rule — a 40 L city car and a 90 L estate disagree about
# what "quarter tank" is worth interrupting for.
DEFAULT_LOW_PCT: Final = 25.0
# How near "right there" is. Beyond a few kilometres this stops being a nudge
# about the station you are passing and becomes an errand.
DEFAULT_NEAR_KM: Final = 5.0
# Once told, stay quiet this long even if everything still lines up.
COOLDOWN_S: Final = 6 * 3600.0
# The tank has to climb this far above the threshold before a new low tank
# counts as new. Without it a level hovering on the line re-arms on noise.
REARM_MARGIN_PCT: Final = 10.0


@dataclass(frozen=True)
class CarSnapshot:
    """What the decision needs to know about one car, right now."""

    car_id: str
    name: str
    fuel_key: str | None
    level_pct: float | None
    litres: float | None
    days_until_empty: float | None
    latitude: float | None
    longitude: float | None

    @property
    def is_placed(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class FillupMemory:
    """What was already said about one car, so it is not said again.

    Deliberately small and held in memory only. Losing it to a restart costs at
    most one repeated alert, and only if the car is still low and still beside
    the same cheap station — while persisting it would mean a second store to
    keep, version and migrate for a notification.
    """

    fired_at: float = 0.0
    armed: bool = True

    def record(self, now: float) -> None:
        self.fired_at = now
        self.armed = False

    def rearm(self) -> None:
        self.fired_at = 0.0
        self.armed = True


@dataclass(frozen=True)
class Settings:
    """The user's answers, or the defaults."""

    enabled: bool = False
    low_pct: float = DEFAULT_LOW_PCT
    near_km: float = DEFAULT_NEAR_KM


@dataclass(frozen=True)
class Suggestion:
    """A fill-up worth interrupting someone for."""

    car_id: str
    car_name: str
    level_pct: float
    days_until_empty: float | None
    station: dict
    # How much cheaper this station is than the dearest one in range, in the
    # country's minor unit (øre, cents). Zero when there is nothing to compare.
    saving_minor: int = 0


def evaluate(
    car: CarSnapshot,
    ranked: list[dict],
    settings: Settings,
    memory: FillupMemory,
    now: float,
) -> Suggestion | None:
    """Decide whether to suggest filling up, and re-arm when the car is filled.

    ``ranked`` is what `nearby.rank_nearby` returned for this car's position and
    fuel, cheapest first — so the station to send someone to is simply the first
    one, and an empty list means there is nothing to suggest.

    Mutates ``memory``: the re-arming has to happen on the refreshes where
    nothing is suggested, which is most of them.
    """
    if not settings.enabled or car.level_pct is None:
        return None

    # Filled up (or topped up well clear of the line) — the next low tank is a
    # new one, and may be announced. Checked before every early return below,
    # because the refresh that re-arms is normally a quiet one.
    if car.level_pct > settings.low_pct + REARM_MARGIN_PCT:
        memory.rearm()
        return None

    if car.level_pct > settings.low_pct or not car.is_placed:
        return None
    if not memory.armed and (now - memory.fired_at) < COOLDOWN_S:
        return None
    if not ranked:
        return None

    best = ranked[0]
    if best.get("distance_km") is None or best["distance_km"] > settings.near_km:
        # Cheap, but not here. Saying so would be an errand, not a nudge.
        return None
    # An estimated position is not somewhere to send a car — the same rule the
    # navigation links follow.
    if best.get("coord_approx"):
        return None

    dearest = max((s["price"] for s in ranked), default=best["price"])
    memory.record(now)
    return Suggestion(
        car_id=car.car_id,
        car_name=car.name,
        level_pct=car.level_pct,
        days_until_empty=car.days_until_empty,
        station=best,
        saving_minor=int(round((dearest - best["price"]) * 100)),
    )


def message(
    suggestion: Suggestion,
    unit: str,
    price_text: str,
    danish: bool,
    minor_unit: str = "øre",
) -> tuple[str, str]:
    """The (title, body) to send.

    Says the three things that justify the interruption, in the order they
    answer "why are you telling me this": how little is left, where to go, and
    what it costs. The saving is only mentioned when there is one — "0 øre
    cheaper than the dearest nearby" is an argument against stopping.
    """
    station = suggestion.station
    name = station.get("name") or station.get("company") or "?"
    distance = f"{station['distance_km']:.1f}".replace(".", "," if danish else ".")

    if danish:
        title = f"Tank {suggestion.car_name}"
        left = f"{suggestion.level_pct:.0f} %".replace(".", ",")
        if suggestion.days_until_empty is not None:
            left += f", ca. {suggestion.days_until_empty:.0f} dage tilbage"
        body = f"{left}. {name}, {distance} km væk, {price_text} {unit}."
        if suggestion.saving_minor > 0:
            body += f" {suggestion.saving_minor} {minor_unit} under den dyreste i nærheden."
        return title, body

    title = f"Fill up {suggestion.car_name}"
    left = f"{suggestion.level_pct:.0f} %"
    if suggestion.days_until_empty is not None:
        left += f", about {suggestion.days_until_empty:.0f} days left"
    body = f"{left}. {name}, {distance} km away, {price_text} {unit}."
    if suggestion.saving_minor > 0:
        body += f" {suggestion.saving_minor} {minor_unit} below the dearest nearby."
    return title, body

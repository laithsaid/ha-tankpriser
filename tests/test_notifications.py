"""Tests for the price-change notification rules (notifications.py).

Run with: python tests/test_notifications.py

Same `ast` lifting as tests/test_card_registration.py — the module imports Home
Assistant, which is not worth installing to test a pure comparison of two price
snapshots.

These exist because of a real report: the cheapest Blyfri 95 fell from 16,19 to
16,09 and no notification arrived. The "below threshold" rule used to fire only
on the refresh that crossed the line, so a price already under the threshold
could keep falling in silence — see `test_threshold_fires_on_every_drop_below`.
"""

from __future__ import annotations

import ast
import io
import os
import sys
from typing import Any

SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "tankpriser",
    "notifications.py",
)

RULE_ANY = "any_change"
RULE_CHEAPEST = "cheapest_change"
RULE_THRESHOLD = "below_threshold"
RULE_DECREASE = "decrease_only"

FUEL = "blyfri95"
DISPLAY = "Blyfri 95 (E10)"


def _load() -> dict[str, Any]:
    tree = ast.parse(io.open(SOURCE, encoding="utf-8").read())
    wanted = {"_evaluate_fuel", "_any_price_changed", "_cheapest_price"}
    nodes = [n for n in tree.body if getattr(n, "name", "") in wanted]
    missing = wanted - {n.name for n in nodes}
    if missing:
        raise SystemExit(f"notifications.py no longer defines {sorted(missing)}")
    namespace: dict[str, Any] = {
        "RULE_ANY": RULE_ANY,
        "RULE_CHEAPEST": RULE_CHEAPEST,
        "RULE_THRESHOLD": RULE_THRESHOLD,
        "RULE_DECREASE": RULE_DECREASE,
    }
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(future + nodes, []), "<notify>", "exec"), namespace)
    return namespace


_NS = _load()
evaluate = _NS["_evaluate_fuel"]


class Station:
    def __init__(self, name: str, price: float | None) -> None:
        self.name = name
        self.prices = {} if price is None else {FUEL: price}


class Data:
    """Stand-in for coordinator.TankpriserData (only what the rules touch)."""

    def __init__(self, *pairs: tuple[str, float | None]) -> None:
        self.stations = [Station(name, price) for name, price in pairs]

    def stations_for(self, fuel: str) -> list[Station]:
        matching = [s for s in self.stations if fuel in s.prices]
        return sorted(matching, key=lambda s: s.prices[fuel])

    def cheapest(self, fuel: str) -> Station | None:
        ordered = self.stations_for(fuel)
        return ordered[0] if ordered else None


def fire(prev: Data, curr: Data, rule: str, threshold: float | None = None):
    return evaluate(prev, curr, FUEL, DISPLAY, rule, threshold)


# The reported case: cheapest 16,19 -> 16,09, both already under a 17,00 line.
BEFORE = Data(("OK Silkeborg", 16.19), ("Shell Silkeborg", 16.49))
AFTER = Data(("OK Silkeborg", 16.09), ("Shell Silkeborg", 16.49))


def test_threshold_fires_on_every_drop_below() -> None:
    """The regression: a fall *while already below* the line must be reported."""
    message = fire(BEFORE, AFTER, RULE_THRESHOLD, 17.0)
    assert message == "Blyfri 95 (E10): 16,09 is below 17,00", message


def test_threshold_still_fires_on_the_crossing() -> None:
    prev = Data(("OK Silkeborg", 17.20))
    curr = Data(("OK Silkeborg", 16.90))
    assert fire(prev, curr, RULE_THRESHOLD, 17.0) == (
        "Blyfri 95 (E10): 16,90 is below 17,00"
    )


def test_threshold_ignores_prices_above_the_line() -> None:
    prev = Data(("OK Silkeborg", 17.50))
    curr = Data(("OK Silkeborg", 17.20))
    assert fire(prev, curr, RULE_THRESHOLD, 17.0) is None


def test_threshold_is_silent_on_a_rise_below_the_line() -> None:
    """"Drops below" means drops. A rise from 16,09 to 16,49 is not news."""
    assert fire(AFTER, BEFORE, RULE_THRESHOLD, 17.0) is None


def test_threshold_does_not_repeat_an_unchanged_price() -> None:
    assert fire(BEFORE, BEFORE, RULE_THRESHOLD, 17.0) is None


def test_threshold_without_a_threshold_never_fires() -> None:
    """Guarded again in evaluate_and_notify, which warns rather than staying mute."""
    assert fire(BEFORE, AFTER, RULE_THRESHOLD, None) is None


def test_threshold_reports_a_first_reading_under_the_line() -> None:
    empty = Data(("OK Silkeborg", None))
    assert fire(empty, AFTER, RULE_THRESHOLD, 17.0) == (
        "Blyfri 95 (E10): 16,09 is below 17,00"
    )


def test_cheapest_change_reports_the_direction() -> None:
    assert fire(BEFORE, AFTER, RULE_CHEAPEST) == (
        "Blyfri 95 (E10): cheapest 16,19 → 16,09 ↓"
    )
    assert fire(AFTER, BEFORE, RULE_CHEAPEST) == (
        "Blyfri 95 (E10): cheapest 16,09 → 16,19 ↑"
    )
    assert fire(BEFORE, BEFORE, RULE_CHEAPEST) is None


def test_cheapest_change_ignores_a_threshold_left_in_the_form() -> None:
    """The threshold box stays visible for every rule; it must not gate this one."""
    assert fire(BEFORE, AFTER, RULE_CHEAPEST, 17.0) == (
        "Blyfri 95 (E10): cheapest 16,19 → 16,09 ↓"
    )
    assert fire(BEFORE, AFTER, RULE_CHEAPEST, 10.0) == (
        "Blyfri 95 (E10): cheapest 16,19 → 16,09 ↓"
    )


def test_decrease_only_is_one_directional() -> None:
    assert fire(BEFORE, AFTER, RULE_DECREASE) == (
        "Blyfri 95 (E10): dropped 16,19 → 16,09"
    )
    assert fire(AFTER, BEFORE, RULE_DECREASE) is None


def test_any_change_notices_a_station_that_is_not_cheapest() -> None:
    prev = Data(("OK Silkeborg", 16.19), ("Shell Silkeborg", 16.49))
    curr = Data(("OK Silkeborg", 16.19), ("Shell Silkeborg", 16.29))
    assert fire(prev, curr, RULE_ANY) == (
        "Blyfri 95 (E10): prices updated (cheapest 16,19)"
    )
    assert fire(prev, prev, RULE_ANY) is None
    # ... and the cheapest-only rules stay quiet for the same pair.
    assert fire(prev, curr, RULE_CHEAPEST) is None
    assert fire(prev, curr, RULE_DECREASE) is None


def test_a_fuel_nobody_sells_here_is_not_a_notification() -> None:
    none_sold = Data(("OK Silkeborg", None))
    assert fire(BEFORE, none_sold, RULE_CHEAPEST) is None
    assert fire(BEFORE, none_sold, RULE_THRESHOLD, 17.0) is None


def test_first_snapshot_after_a_restart_is_only_a_baseline() -> None:
    """No previous price means no claim about a change (threshold rule aside)."""
    empty = Data(("OK Silkeborg", None))
    assert fire(empty, AFTER, RULE_CHEAPEST) is None
    assert fire(empty, AFTER, RULE_DECREASE) is None


def test_a_ten_ore_rehearsal_fires_every_rule() -> None:
    """What `tankpriser.test_notification` does: pretend it was 10 øre dearer.

    The service is only worth having if that rehearsal exercises whichever rule
    the user actually picked, so assert it for all four rather than the one that
    happened to be selected while writing it.
    """
    current = Data(("OK Silkeborg", 16.09), ("Shell Silkeborg", 16.49))
    before = Data(("OK Silkeborg", 16.19), ("Shell Silkeborg", 16.59))
    for rule, threshold in (
        (RULE_ANY, None),
        (RULE_CHEAPEST, None),
        (RULE_DECREASE, None),
        (RULE_THRESHOLD, 17.0),
    ):
        assert fire(before, current, rule, threshold), rule


def test_a_rehearsal_cannot_fire_above_the_threshold() -> None:
    """Prices over the line stay silent, and the service must say why."""
    current = Data(("OK Silkeborg", 17.40))
    before = Data(("OK Silkeborg", 17.50))
    assert fire(before, current, RULE_THRESHOLD, 17.0) is None
    assert fire(before, current, RULE_CHEAPEST) is not None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} notification tests passed")
    sys.exit(0)

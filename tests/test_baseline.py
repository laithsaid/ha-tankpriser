"""Tests for the persisted change-detection baseline (coordinator.py).

Run with: python tests/test_baseline.py

Notifications compare each refresh with the one before it. That baseline used
to live only in memory, so every restart made the next refresh the new baseline
and whatever the price did across the gap was never announced — and since the
chains move prices about once a day, a daily restart meant a notification could
never arrive at all.

`sources.py` is imported for real (aiohttp stubbed, as in test_discounts.py);
the two baseline helpers are lifted out of coordinator.py with `ast`, which
imports Home Assistant.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import os
import sys
import types
from typing import Any

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
MAX_BASELINE_AGE = 7 * 24 * 3600.0
NOW = 1_800_000_000.0


def _load_sources() -> types.ModuleType:
    sys.modules.setdefault(
        "aiohttp",
        types.SimpleNamespace(
            ClientSession=object,
            ClientError=Exception,
            ClientTimeout=lambda **kwargs: None,
            ClientResponseError=Exception,
        ),
    )
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    modules = {}
    for name in ("const", "sources"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules["sources"]


sources = _load_sources()
Station = sources.Station


def _load_helpers() -> dict[str, Any]:
    tree = ast.parse(
        io.open(os.path.join(BASE, "coordinator.py"), encoding="utf-8").read()
    )
    wanted = {"baseline_payload", "stations_from_baseline"}
    nodes = [n for n in tree.body if getattr(n, "name", "") in wanted]
    missing = wanted - {n.name for n in nodes}
    if missing:
        raise SystemExit(f"coordinator.py no longer defines {sorted(missing)}")
    namespace: dict[str, Any] = {
        "Station": Station,
        "MAX_BASELINE_AGE": MAX_BASELINE_AGE,
    }
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(future + nodes, []), "<coord>", "exec"), namespace)
    return namespace


_NS = _load_helpers()
payload = _NS["baseline_payload"]
restore = _NS["stations_from_baseline"]


def station(name: str, **prices: float) -> Station:
    return Station(
        name=name, company="OK", postnummer="8600", updated="2026-07-27", prices=prices
    )


AREA = [
    station("OK Silkeborg", blyfri95=16.09, diesel=15.29),
    station("Shell Silkeborg", blyfri95=16.49),
]


def test_round_trip_preserves_names_and_prices() -> None:
    stored = payload(AREA, NOW)
    back = restore(stored, NOW + 60)
    assert back is not None
    assert [s.name for s in back] == ["OK Silkeborg", "Shell Silkeborg"]
    assert back[0].prices == {"blyfri95": 16.09, "diesel": 15.29}
    assert back[1].prices == {"blyfri95": 16.49}


def test_only_names_and_prices_are_stored() -> None:
    """Nothing worth redacting, and nothing that is re-fetched anyway."""
    stored = payload(AREA, NOW)
    assert set(stored) == {"saved", "stations"}
    for record in stored["stations"]:
        assert set(record) == {"name", "prices"}


def test_stations_without_prices_are_dropped() -> None:
    stored = payload([*AREA, station("Q8 Uden Priser")], NOW)
    assert len(stored["stations"]) == 2


def test_the_stored_prices_are_a_copy() -> None:
    """The live snapshot must not be able to mutate what gets written."""
    live = station("OK Silkeborg", blyfri95=16.09)
    stored = payload([live], NOW)
    live.prices["blyfri95"] = 99.99
    assert stored["stations"][0]["prices"] == {"blyfri95": 16.09}


def test_a_stale_baseline_is_not_news() -> None:
    stored = payload(AREA, NOW)
    assert restore(stored, NOW + MAX_BASELINE_AGE - 60) is not None
    assert restore(stored, NOW + MAX_BASELINE_AGE + 60) is None


def test_a_clock_that_went_backwards_is_refused() -> None:
    """A Pi with no RTC catching up over NTP must not compare with the future."""
    stored = payload(AREA, NOW)
    assert restore(stored, NOW - 3600) is None


def test_nothing_stored_yet() -> None:
    assert restore(None, NOW) is None
    assert restore({}, NOW) is None
    assert restore({"saved": NOW, "stations": []}, NOW) is None


def test_a_corrupt_file_costs_one_comparison_not_a_failed_setup() -> None:
    assert restore("not a dict", NOW) is None
    assert restore({"saved": "yesterday", "stations": []}, NOW) is None
    assert restore({"saved": NOW, "stations": "nope"}, NOW) is None
    assert restore({"saved": NOW, "stations": [None, 7]}, NOW) is None
    assert restore({"saved": NOW, "stations": [{"name": "OK"}]}, NOW) is None


def test_unparseable_prices_are_skipped_but_the_station_survives() -> None:
    stored = {
        "saved": NOW,
        "stations": [{"name": "OK Silkeborg", "prices": {"blyfri95": "16.09",
                                                         "diesel": "n/a"}}],
    }
    back = restore(stored, NOW)
    assert back is not None
    assert back[0].prices == {"blyfri95": 16.09}


def test_a_restored_baseline_answers_what_the_rules_ask_it() -> None:
    """The rules only ever call `cheapest` / `stations_for`, so those must work
    on a rebuilt snapshot exactly as on a freshly fetched one."""
    back = restore(payload(AREA, NOW), NOW)
    ordered = sorted(
        (s for s in back if "blyfri95" in s.prices),
        key=lambda s: s.prices["blyfri95"],
    )
    assert ordered[0].name == "OK Silkeborg"
    assert ordered[0].prices["blyfri95"] == 16.09


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} baseline tests passed")
    sys.exit(0)

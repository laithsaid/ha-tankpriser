"""Tests for the `spoken` attribute (sensor.spoken_sentence / _spoken_place).

Run with: python tests/test_spoken.py

`sensor.py` does import Home Assistant, so the handful of names it pulls in are
stubbed below — the sentence builder itself is pure and needs none of them.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)


def _stub_homeassistant() -> None:
    """Enough of Home Assistant for `import sensor` to succeed."""

    def module(name: str, **attrs) -> types.ModuleType:
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    module("homeassistant")
    module("homeassistant.components")
    module(
        "homeassistant.components.sensor",
        SensorDeviceClass=type("SensorDeviceClass", (), {"DURATION": "duration"}),
        SensorEntity=type("SensorEntity", (), {}),
        SensorStateClass=type("SensorStateClass", (), {"MEASUREMENT": "measurement"}),
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module("homeassistant.const", UnitOfTime=type("UnitOfTime", (), {"DAYS": "d"}))
    module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
    module("homeassistant.helpers")
    module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module(
        "homeassistant.helpers.event", async_track_state_change_event=lambda *a: None
    )
    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=dict)
    module("homeassistant.util", dt=types.SimpleNamespace(utcnow=lambda: None))
    module("homeassistant.util.location", distance=lambda *a: None)


def _load() -> types.ModuleType:
    """Import `sensor.py` with everything it does not need stubbed out.

    Only `const` is loaded for real; the coordinator and consumption modules are
    stubbed rather than imported, which keeps this test from dragging in aiohttp
    and the whole Home Assistant config-entry stack for a pure string builder.
    """
    _stub_homeassistant()
    package = types.ModuleType("tp")
    package.__path__ = [BASE]
    sys.modules["tp"] = package
    sys.modules["tp.consumption"] = types.ModuleType("tp.consumption")
    sys.modules["tp.consumption"].ConsumptionTracker = object
    sys.modules["tp.consumption"].zone_coords = lambda *_a: (None, None)
    sys.modules["tp.coordinator"] = types.ModuleType("tp.coordinator")
    sys.modules["tp.coordinator"].TankpriserCoordinator = object

    # `nearby` is pure, so it loads for real like `const`.
    for name in ("const", "nearby", "sensor"):
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["tp.sensor"]


def station(name: str, price: float, km: float, company: str = "OK") -> dict:
    return {
        "name": name,
        "company": company,
        "city": "Silkeborg",
        "price": price,
        "distance_km": km,
    }


def test_identical_prices_are_stated_once(sensor) -> None:
    """The case that made the sentence useless: one chain, one national price.

    Three OK forecourts at 16.19 read as "OK Silkeborg" three times, so the
    listener learned nothing they could choose on.
    """
    said = sensor.spoken_sentence(
        [
            station("OK Nordre Ringvej 110", 16.19, 1.9),
            station("OK Vestre Ringvej 24", 16.19, 2.1),
            station("OK Julsøvej 93", 16.19, 7.5),
        ],
        danish=True,
    )
    assert said == (
        "Alle tre koster 16,19 kroner. "
        "Nummer 1: OK Nordre Ringvej, 1,9 kilometer. "
        "Nummer 2: OK Vestre Ringvej, 2,1 kilometer. "
        "Nummer 3: OK Julsøvej, 7,5 kilometer."
    ), said


def test_differing_prices_keep_the_price_per_station(sensor) -> None:
    said = sensor.spoken_sentence(
        [
            station("OK Nordre Ringvej 110", 16.19, 1.9),
            station("Q8 Vestergade 5", 16.49, 1.2, company="Q8"),
            station("Shell Århusvej 12", 16.79, 0.8, company="Shell"),
        ],
        danish=True,
    )
    assert said == (
        "Nummer 1: OK Nordre Ringvej, 16,19 kroner, 1,9 kilometer. "
        "Nummer 2: Q8 Vestergade, 16,49 kroner, 1,2 kilometer. "
        "Nummer 3: Shell Århusvej, 16,79 kroner, 0,8 kilometer."
    ), said


def test_english_uses_a_decimal_point(sensor) -> None:
    said = sensor.spoken_sentence(
        [station("OK Nordre Ringvej 110", 16.19, 1.9)], danish=False
    )
    assert said == "Number 1: OK Nordre Ringvej, 16.19 kroner, 1.9 kilometres.", said


def test_at_most_three_are_named(sensor) -> None:
    ranked = [station(f"OK Vej {i}", 16.19 + i, float(i)) for i in range(1, 9)]
    said = sensor.spoken_sentence(ranked, danish=True)
    assert said.count("Nummer") == 3, said
    assert "Nummer 4" not in said, said


def test_no_stations(sensor) -> None:
    assert sensor.spoken_sentence([], danish=True) == "Ingen stationer i nærheden."
    assert sensor.spoken_sentence([], danish=False) == "No stations nearby."


def test_house_number_trimming(sensor) -> None:
    cases = {
        "OK Nordre Ringvej 110": "OK Nordre Ringvej",
        "Circle K Vejlevej 12B": "Circle K Vejlevej",
        "Shell Århusvej, 12": "Shell Århusvej",
        # No trailing number to strip: left exactly as the source gave it.
        "OIL! Silkeborg": "OIL! Silkeborg",
        # Known wart, pinned so a future change to the regex is a deliberate
        # one: a road whose *name* ends in a number loses it too. Rare enough in
        # the Danish station lists to be worth the house numbers this removes.
        "Q8 Rute 9": "Q8 Rute",
    }
    for raw, expected in cases.items():
        got = sensor._spoken_place({"name": raw, "company": "X", "city": "Y"})
        assert got == expected, f"{raw!r} -> {got!r}, wanted {expected!r}"


def test_falls_back_to_company_and_city_without_a_name(sensor) -> None:
    got = sensor._spoken_place({"name": "", "company": "Q8", "city": "Silkeborg"})
    assert got == "Q8 Silkeborg", got


def main() -> int:
    sensor = _load()
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn(sensor)
        except AssertionError as err:
            failures += 1
            print(f"FAIL {name}: {err}")
        else:
            print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

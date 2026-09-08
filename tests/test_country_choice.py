"""Tests for choosing which configured country answers a position.

Run with: python tests/test_country_choice.py

The bug this guards against was found live: with Denmark configured first and
Germany second, asking `tankpriser.nearby` from Hamburg searched the *Danish*
pool and answered "no stations within 15 kilometres". Every entry-picking
caller went through the first entry and never looked at the position it had
been handed.

The rule itself is `nearby.country_for_position`, which needs no Home
Assistant and is called here exactly as `coordinator.entry_for_position` calls
it — only the config entry and `hass` around it are faked.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)


def _load(name: str) -> types.ModuleType:
    package = sys.modules.get("tp")
    if package is None:
        package = types.ModuleType("tp")
        package.__path__ = [BASE]
        sys.modules["tp"] = package
    spec = importlib.util.spec_from_file_location(
        f"tp.{name}", os.path.join(BASE, f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"tp.{name}"] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
nearby = _load("nearby")

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


# Positions worth arguing about.
SILKEBORG = (56.1806, 9.5107)
FLENSBURG = (54.7833, 9.4333)
HAMBURG = (53.5511, 9.9937)
MUNICH = (48.1351, 11.5820)
COPENHAGEN = (55.6761, 12.5683)
KRUSAA = (54.8420, 9.4020)  # Danish side of the border, 6 km from Flensburg
MID_ATLANTIC = (30.0, -40.0)


class FakeEntry:
    def __init__(self, entry_id, country, anchor=None):
        self.entry_id = entry_id
        self.data = {const.CONF_COUNTRY: country}
        self.options = {}
        if anchor is not None:
            self.options[const.CONF_ANCHOR] = {
                "latitude": anchor[0],
                "longitude": anchor[1],
            }


class FakeConfigEntries:
    def __init__(self, entries):
        self._entries = entries

    def async_entries(self, domain):
        return list(self._entries)


class FakeConfig:
    latitude = SILKEBORG[0]
    longitude = SILKEBORG[1]


class FakeHass:
    def __init__(self, entries):
        self.config_entries = FakeConfigEntries(entries)
        self.config = FakeConfig()
        self.data = {const.DOMAIN: {}}


# The real rule, imported rather than reproduced: `nearby.country_for_position`
# is what `coordinator.entry_for_position` calls, and it needs no Home
# Assistant. Entries are shaped for it the same way the coordinator does.
def entry_for_position(hass, latitude=None, longitude=None):
    entries = list(hass.config_entries.async_entries(const.DOMAIN))
    if not entries:
        return None
    if latitude is None or longitude is None:
        return entries[0]
    return nearby.country_for_position(
        [
            (
                entry,
                const.country_of(
                    str(entry.data.get(const.CONF_COUNTRY, const.DEFAULT_COUNTRY))
                ),
                _anchor_of(hass, entry),
            )
            for entry in entries
        ],
        latitude,
        longitude,
    )


def _anchor_of(hass, entry):
    stored = entry.options.get(const.CONF_ANCHOR) or {}
    latitude = stored.get("latitude", hass.config.latitude)
    longitude = stored.get("longitude", hass.config.longitude)
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


print("the country boxes cover their own country")
dk = const.country_of("dk")
de = const.country_of("de")
check("Silkeborg is in Denmark", dk.contains(*SILKEBORG))
check("Copenhagen is in Denmark", dk.contains(*COPENHAGEN))
check("Munich is in Germany", de.contains(*MUNICH))
check("Hamburg is in Germany", de.contains(*HAMBURG))
check("Hamburg is NOT in Denmark", not dk.contains(*HAMBURG))
check("Munich is NOT in Denmark", not dk.contains(*MUNICH))
check("Copenhagen is NOT in Germany", not de.contains(*COPENHAGEN))

print("the entry that answers is the country you are standing in")
danish = FakeEntry("dk1", "dk", SILKEBORG)
german = FakeEntry("de1", "de", FLENSBURG)
hass = FakeHass([danish, german])  # Denmark first, as his instance has it

check(
    "Hamburg is answered by Germany, not by the first entry",
    entry_for_position(hass, *HAMBURG) is german,
)
check("Munich is answered by Germany", entry_for_position(hass, *MUNICH) is german)
check("Silkeborg is answered by Denmark", entry_for_position(hass, *SILKEBORG) is danish)
check(
    "Copenhagen is answered by Denmark",
    entry_for_position(hass, *COPENHAGEN) is danish,
)

print("the border strip goes to the nearer anchor")
check(
    "Flensburg goes to the German entry",
    entry_for_position(hass, *FLENSBURG) is german,
)
check(
    "Kruså, 6 km inside Denmark, goes to the German entry because that anchor "
    "is nearer — and a Dane who set up Flensburg wants German prices there",
    entry_for_position(hass, *KRUSAA) is german,
)

print("nothing configured for a place falls back rather than failing")
check(
    "mid-Atlantic still answers with the first entry",
    entry_for_position(hass, *MID_ATLANTIC) is danish,
)
check("no position at all answers with the first entry", entry_for_position(hass) is danish)
check(
    "a single-country setup is unchanged wherever it is asked",
    entry_for_position(FakeHass([danish]), *MUNICH) is danish,
)
check("no entries at all is None, not a crash", entry_for_position(FakeHass([])) is None)

print("order does not decide it")
reversed_hass = FakeHass([german, danish])  # Germany first
check(
    "Silkeborg is still Denmark when Germany is listed first",
    entry_for_position(reversed_hass, *SILKEBORG) is danish,
)
check(
    "Hamburg is still Germany when Germany is listed first",
    entry_for_position(reversed_hass, *HAMBURG) is german,
)

print()
if FAILURES:
    print(f"{len(FAILURES)} country-choice checks FAILED")
    raise SystemExit(1)
print("all country-choice checks passed")

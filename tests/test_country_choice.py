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


# --- both sides of the border at once --------------------------------------
# Picking ONE country is right for a sentence and wrong for a map. Standing at
# Kruså, a 25 km circle holds Danish and German forecourts alike, and showing
# half of them is showing the wrong map.
def entries_for_position(hass, latitude=None, longitude=None):
    entries = list(hass.config_entries.async_entries(const.DOMAIN))
    if not entries:
        return []
    if latitude is None or longitude is None:
        return entries[:1]
    return nearby.countries_for_position(
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


print("\nboth countries in reach")
both = entries_for_position(hass, *KRUSAA)
check("Kruså reaches both countries", len(both) == 2, [e.entry_id for e in both])
check(
    "and the nearer anchor leads, so the spoken answer is unchanged",
    both[0] is entry_for_position(hass, *KRUSAA),
    both[0].entry_id,
)
deep_dk = entries_for_position(hass, *COPENHAGEN)
check(
    "Copenhagen reaches Denmark only",
    len(deep_dk) == 1 and deep_dk[0] is danish,
    [e.entry_id for e in deep_dk],
)
deep_de = entries_for_position(hass, *MUNICH)
check(
    "Munich reaches Germany only",
    len(deep_de) == 1 and deep_de[0] is german,
    [e.entry_id for e in deep_de],
)
nowhere = entries_for_position(hass, *MID_ATLANTIC)
check(
    "somewhere no country claims still falls back to one entry",
    len(nowhere) == 1,
    [e.entry_id for e in nowhere],
)
check(
    "the single answer is always the head of the list",
    all(
        entries_for_position(hass, *where)[0] is entry_for_position(hass, *where)
        for where in (SILKEBORG, FLENSBURG, HAMBURG, KRUSAA, COPENHAGEN, MUNICH)
    ),
)

print("\nsaying both without comparing them")
dk_ranked = [{"name": "OK Kruså", "price": 17.59, "distance_km": 4.2}]
de_ranked = [{"name": "team Flensburg", "price": 2.229, "distance_km": 9.8}]
sentence = nearby.spoken_by_country(
    [
        {"name": "Denmark", "ranked": dk_ranked, "currency": "kroner"},
        {"name": "Germany", "ranked": de_ranked, "currency": "euro"},
    ],
    danish=False,
)
check("it names both countries", "Denmark" in sentence and "Germany" in sentence, sentence)
check("with each price in its own currency",
      "kroner" in sentence and "euro" in sentence, sentence)
check(
    "and never claims one is cheaper than the other",
    "cheaper" not in sentence and "billigere" not in sentence,
    sentence,
)
quiet_side = nearby.spoken_by_country(
    [
        {"name": "Denmark", "ranked": dk_ranked, "currency": "kroner"},
        {"name": "Germany", "ranked": [], "currency": "euro"},
    ],
    danish=False,
)
check(
    "a country with nothing to show is left out rather than announced",
    "Germany" not in quiet_side and "Denmark" in quiet_side,
    quiet_side,
)
empty_everywhere = nearby.spoken_by_country(
    [
        {"name": "Denmark", "ranked": [], "currency": "kroner"},
        {"name": "Germany", "ranked": [], "currency": "euro"},
    ],
    danish=False,
    searched_km=25,
)
check(
    "nothing anywhere says so, and names the range it covered",
    "no stations" in empty_everywhere.lower() and "25" in empty_everywhere,
    empty_everywhere,
)
danish_sentence = nearby.spoken_by_country(
    [
        {"name": const.country_of("dk").spoken_name(True), "ranked": dk_ranked,
         "currency": "kroner"},
        {"name": const.country_of("de").spoken_name(True), "ranked": de_ranked,
         "currency": "euro"},
    ],
    danish=True,
)
check(
    "Danish says Danmark and Tyskland, not Denmark and Germany",
    "Danmark" in danish_sentence and "Tyskland" in danish_sentence,
    danish_sentence,
)

# --- an empty answer that says where it looked ------------------------------
# Found live on 2026-09-17, by pinning the map near southern Belgium. The boxes
# overspill their borders on purpose, so a pin can land inside a configured
# country's box while standing in a country that is not configured at all.
# Luxembourg City is inside BOTH the Belgian and the German box; Germany's
# anchor was nearer, so Germany answered, and no German forecourt is within
# 25 km of it. The answer was "No stations within 25 kilometres" with 233
# Luxembourgish stations under the pin: true about the pool, a lie about the
# place. Northern France did the same thing through Belgium's box.
LUXEMBOURG_CITY = (49.6117, 6.1319)
CHARLEVILLE = (49.7717, 4.7197)  # France, well inside the Belgian box
ARDENNES = (50.0270, 5.3760)     # Saint-Hubert, genuinely in Belgium

print()
print("the trap: a box is not a border")
check(
    "Luxembourg City is inside Belgium's box",
    const.country_of("be").contains(*LUXEMBOURG_CITY),
)
check(
    "and inside Germany's, which is what used to answer",
    const.country_of("de").contains(*LUXEMBOURG_CITY),
)
check(
    "Charleville is inside Belgium's box although it is in France",
    const.country_of("be").contains(*CHARLEVILLE),
)

print()
print("naming what is not set up")
FOUR = ("dk", "de", "nl", "be")
missing_lux = nearby.unconfigured_here(FOUR, *LUXEMBOURG_CITY)
check(
    "a Luxembourg pin names Luxembourg first",
    bool(missing_lux) and missing_lux[0].code == "lu",
    [c.code for c in missing_lux],
)
check(
    "smallest box first, so Luxembourg beats France for the same point",
    [c.code for c in missing_lux] == ["lu", "fr"],
    [c.code for c in missing_lux],
)
missing_fr = nearby.unconfigured_here(FOUR, *CHARLEVILLE)
check(
    "a northern-France pin names France",
    bool(missing_fr) and missing_fr[0].code == "fr",
    [c.code for c in missing_fr],
)
check(
    "with every country set up there is nothing to report",
    nearby.unconfigured_here(("dk", "de", "nl", "be", "lu", "fr"), *LUXEMBOURG_CITY)
    == [],
)
check(
    "a place genuinely in a configured country never names that country",
    "be" not in [c.code for c in nearby.unconfigured_here(FOUR, *ARDENNES)],
    [c.code for c in nearby.unconfigured_here(FOUR, *ARDENNES)],
)
check(
    "somewhere no box claims at all is nobody's gap",
    nearby.unconfigured_here(FOUR, *MID_ATLANTIC) == [],
)

print()
print("the sentence stops implying the forecourts do not exist")
searched_elsewhere = nearby.spoken_cheapest(
    [], danish=False, currency="euro", searched_km=25, country_name="Germany"
)
check(
    "an empty answer names the country it searched",
    "Germany" in searched_elsewhere and "25" in searched_elsewhere,
    searched_elsewhere,
)
check(
    "and still says the range, which the Shortcut reads out",
    "no stations within 25" in searched_elsewhere.lower(),
    searched_elsewhere,
)
no_source = nearby.spoken_no_source("Luxembourg", danish=False)
check(
    "a country with no source says so instead of quoting a range",
    "Luxembourg" in no_source and "not set up" in no_source,
    no_source,
)
check(
    "and says nothing about a range it never searched",
    "kilometre" not in no_source and "within" not in no_source,
    no_source,
)
check(
    "Danish gets its own wording",
    "ikke sat op" in nearby.spoken_no_source("Frankrig", danish=True),
    nearby.spoken_no_source("Frankrig", danish=True),
)
# A box is a rectangle and a border is not, so the sentence must not claim to
# know which country the pin is standing in: Charleville and Brussels are both
# inside the Belgian box and only one of them is in Belgium.
check(
    "it never claims where you are, only what is missing",
    "position" not in no_source.lower() and "you are" not in no_source.lower(),
    no_source,
)

print()
if FAILURES:
    print(f"{len(FAILURES)} country-choice checks FAILED")
    raise SystemExit(1)
print("all country-choice checks passed")

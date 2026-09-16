"""Tests for the Assist intent that answers in the car (intents.py, nearby.py).

Run with: python tests/test_intent.py

Two halves, because the code is in two halves on purpose:

* **What fuel did they just say** — `fuel_from_words` is pure and lives in
  `nearby.py`, so it is imported and called directly.
* **What the handler decides** — `intents.py` imports Home Assistant, so the
  same `ast` lifting the other service-side tests use pulls the handler out and
  runs it against stand-ins. What is being checked is the one decision that can
  give a confident wrong answer at 110 km/h: **a question about a fuel we do
  not follow must not be answered with the price of a different fuel.**
"""

from __future__ import annotations

import ast
import asyncio
import io
import os
import sys
import types
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load_nearby import load_nearby  # noqa: E402

SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "tankpriser",
    "intents.py",
)

nearby = load_nearby()

checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


# --- half one: the words -----------------------------------------------------


def test_the_plain_word_means_the_plain_fuel() -> None:
    """"diesel" is B7. The premium pump has to be asked for by name.

    The synonyms are matched longest-first for exactly this: with a shortest-
    first pass, "diesel extra" contains "diesel" and every question about the
    expensive pump is answered about the cheap one.
    """
    check(nearby.fuel_from_words("diesel") == "diesel", "diesel -> diesel")
    check(
        nearby.fuel_from_words("diesel extra") == "dieselplus",
        "diesel extra -> dieselplus",
    )
    check(
        nearby.fuel_from_words("premium diesel") == "dieselplus",
        "premium diesel -> dieselplus",
    )


def test_the_words_a_dane_actually_says() -> None:
    for said, expected in {
        "benzin": "blyfri95",
        "blyfri": "blyfri95",
        "blyfri 95": "blyfri95",
        "blyfri-95": "blyfri95",
        "oktan 95": "blyfri95",
        "blyfri 98": "blyfri98",
        "oktan 100": "oktan100",
        "hvo": "hvo100",
        "hvo 100": "hvo100",
        "HVO100": "hvo100",
    }.items():
        got = nearby.fuel_from_words(said)
        check(got == expected, f"{said!r} -> {got!r}, wanted {expected!r}")


def test_a_fuel_named_inside_a_sentence() -> None:
    """Assist hands over the slot, but a sentence file can hand over more."""
    check(
        nearby.fuel_from_words("billigste diesel i nærheden") == "diesel",
        "a fuel inside a Danish sentence",
    )
    check(
        nearby.fuel_from_words("where is the cheapest petrol") == "blyfri95",
        "a fuel inside an English sentence",
    )


def test_a_country_renames_a_fuel() -> None:
    """Germany sells Super E10, and near the border either word is fair."""
    german = {"Super E10": "blyfri95", "Super E5": "blyfri95plus"}
    check(
        nearby.fuel_from_words("super e10", german) == "blyfri95",
        "Germany's name for the same pump",
    )
    check(
        nearby.fuel_from_words("super e5", german) == "blyfri95plus",
        "and for the other one",
    )


def test_nonsense_names_no_fuel() -> None:
    """None means 'they named no fuel', which is a different case from a fuel
    we recognise but do not follow — the handler answers them differently."""
    for said in ("", "   ", "the weather", "adblue", "electricity"):
        got = nearby.fuel_from_words(said)
        check(got is None, f"{said!r} should name no fuel, got {got!r}")


def test_both_sentences_exist_in_both_languages() -> None:
    danish = nearby.spoken_not_followed("HVO100", danish=True)
    english = nearby.spoken_not_followed("HVO100", danish=False)
    check("HVO100" in danish and "HVO100" in english, "the fuel is named")
    check(danish != english, "and the two languages differ")
    check(
        nearby.spoken_no_position(True) != nearby.spoken_no_position(False),
        "so does 'I do not know where you are'",
    )


# --- half two: the handler ---------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.speech: str | None = None
        self.speech_slots: dict | None = None
        self.response_type: str | None = None

    def async_set_speech(self, text: str) -> None:
        self.speech = text

    def async_set_speech_slots(self, slots: dict) -> None:
        self.speech_slots = slots


class FakeHandlerBase:
    """Only what `async_handle` calls on itself."""

    def async_validate_slots(self, slots: dict) -> dict:
        return slots


class FakeIntentObject:
    def __init__(self, hass: Any, fuel: str | None) -> None:
        self.hass = hass
        self.slots = {"fuel": {"value": fuel}} if fuel is not None else {}
        self.response = FakeResponse()

    def create_response(self) -> FakeResponse:
        return self.response


class FakeEntry:
    def __init__(self, country: str, fuels: list[str]) -> None:
        self.entry_id = f"entry_{country}"
        self.data = {"country": country, "fuel_types": fuels}
        self.options: dict = {}


class FakeConfig:
    def __init__(self, latitude, longitude, language="da") -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.language = language


class FakeCoordinator:
    def __init__(self, tracker: str) -> None:
        self.nearby_tracker = tracker


class FakeHass:
    def __init__(self, entries, home=(56.17, 9.55), tracker="", language="da") -> None:
        self._entries = entries
        self.config = FakeConfig(home[0] if home else None, home[1] if home else None,
                                 language)
        self.data = {
            "tankpriser": {e.entry_id: FakeCoordinator(tracker) for e in entries}
        }
        self.config_entries = types.SimpleNamespace(async_entries=lambda _d: entries)


def _load_handler(positions: dict[str, tuple[float, float, str] | None], asked: list):
    """Lift the handler out of intents.py and give it stand-ins.

    `positions` maps a tracker entity id to what `tracker_origin` finds for it;
    every call to `nearby_answer` is appended to `asked`.
    """
    tree = ast.parse(io.open(SOURCE, encoding="utf-8").read())
    wanted = {
        "_danish",
        "_configured_fuels",
        "_country_here",
        "_spoken_labels",
        "_origin",
        "CheapestFuelIntent",
    }
    nodes = [n for n in tree.body if getattr(n, "name", "") in wanted]
    missing = wanted - {n.name for n in nodes}
    if missing:
        raise SystemExit(f"intents.py no longer defines {sorted(missing)}")

    async def nearby_answer(hass, latitude, longitude, fuel=None, **kwargs):
        asked.append({"latitude": latitude, "longitude": longitude, "fuel": fuel})
        return {
            "fuel": fuel or "blyfri95",
            "country": "dk",
            "unit": "kr./L",
            "spoken_cheapest": "Billigste er OK Nordre Ringvej, 16,19 kroner.",
            "stations": [{"name": "OK Nordre Ringvej"}],
            "urls": ["https://example.invalid/1"],
        }

    namespace: dict[str, Any] = {
        "CONF_COUNTRY": "country",
        "CONF_FUEL_TYPES": "fuel_types",
        "DEFAULT_COUNTRY": "dk",
        "DOMAIN": "tankpriser",
        "FUEL_TYPES": {
            "blyfri95": "Blyfri 95 (E10)",
            "blyfri98": "Blyfri 98",
            "blyfri95plus": "Blyfri 95 Extra (E5)",
            "oktan100": "Oktan 100",
            "diesel": "Diesel (B7)",
            "dieselplus": "Diesel Extra",
            "hvo100": "HVO100",
        },
        "fuel_label": lambda key, country="dk": (
            {"blyfri95": "Super E10", "diesel": "Diesel"}.get(key, key)
            if country == "de"
            else {
                "blyfri95": "Blyfri 95 (E10)",
                "diesel": "Diesel (B7)",
                "hvo100": "HVO100",
                "dieselplus": "Diesel Extra",
                "blyfri98": "Blyfri 98",
                "blyfri95plus": "Blyfri 95 Extra (E5)",
                "oktan100": "Oktan 100",
            }.get(key, key)
        ),
        "entries_for_position": lambda hass, lat, lon: hass._entries,
        "tracker_origin": lambda hass, entity_id: positions.get(entity_id),
        "fuel_from_words": nearby.fuel_from_words,
        "spoken_no_position": nearby.spoken_no_position,
        "spoken_not_followed": nearby.spoken_not_followed,
        "nearby_answer": nearby_answer,
        "intent": types.SimpleNamespace(
            IntentHandler=FakeHandlerBase,
            IntentResponseType=types.SimpleNamespace(QUERY_ANSWER="query_answer"),
            non_empty_string=str,
        ),
        "_SLOT_SCHEMA": {},
        "INTENT_CHEAPEST": "TankpriserCheapest",
        "callback": lambda fn: fn,
    }
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(future + nodes, []), "<intents>", "exec"), namespace)
    return namespace["CheapestFuelIntent"]()


def _ask(hass, fuel=None, positions=None):
    asked: list = []
    handler = _load_handler(positions or {}, asked)
    intent_obj = FakeIntentObject(hass, fuel)
    asyncio.run(handler.async_handle(intent_obj))
    return intent_obj.response, asked


DK = FakeEntry("dk", ["blyfri95", "diesel"])


def test_a_fuel_we_do_not_follow_is_not_answered_with_another_one() -> None:
    """The failure this whole branch exists to prevent.

    Ask for HVO100 on a setup that follows petrol and diesel: the price of
    petrol is a perfectly plausible answer, said confidently, about the wrong
    pump — and you are driving, so nothing about it sounds wrong.
    """
    response, asked = _ask(FakeHass([DK]), fuel="hvo")
    check(asked == [], "nothing was asked of the price source")
    check("HVO100" in (response.speech or ""), f"names the fuel: {response.speech!r}")


def test_a_fuel_we_do_follow_is_the_one_searched_for() -> None:
    response, asked = _ask(FakeHass([DK]), fuel="diesel")
    check(len(asked) == 1 and asked[0]["fuel"] == "diesel", f"searched: {asked}")
    check(response.speech.startswith("Billigste"), "and the sentence came back")


def test_no_fuel_named_leaves_the_choice_to_the_entry() -> None:
    """"Cheapest fuel" is a fair question; the entry's first fuel answers it."""
    _, asked = _ask(FakeHass([DK]))
    check(asked[0]["fuel"] is None, f"no fuel forced: {asked}")


def test_a_word_we_do_not_recognise_still_gets_an_answer() -> None:
    """Not understood is not the same as not followed: answer the default."""
    response, asked = _ask(FakeHass([DK]), fuel="the good stuff")
    check(len(asked) == 1 and asked[0]["fuel"] is None, f"asked anyway: {asked}")
    check(response.speech.startswith("Billigste"), "and said something useful")


def test_the_german_name_for_a_danish_fuel_at_the_border() -> None:
    """Both entries answer near Flensburg, so both vocabularies should."""
    both = FakeHass([DK, FakeEntry("de", ["blyfri95", "diesel"])])
    _, asked = _ask(both, fuel="super e10")
    check(len(asked) == 1 and asked[0]["fuel"] == "blyfri95", f"matched: {asked}")


def test_the_nominated_device_is_where_the_question_is_asked_from() -> None:
    """Home is where the price sensors look; the car is somewhere else.

    Answering from Home in a car is the exact failure the `nearby` service was
    built to avoid, so the intent must not reintroduce it.
    """
    hass = FakeHass([DK], home=(56.17, 9.55), tracker="device_tracker.iphone")
    _, asked = _ask(hass, positions={"device_tracker.iphone": (54.90, 9.47, "tracker")})
    check(asked[0]["latitude"] == 54.90, f"asked from the phone: {asked}")


def test_a_silent_tracker_falls_back_to_home() -> None:
    """A phone that has said nothing is not a reason to refuse to answer."""
    hass = FakeHass([DK], home=(56.17, 9.55), tracker="device_tracker.iphone")
    _, asked = _ask(hass, positions={"device_tracker.iphone": None})
    check(asked[0]["latitude"] == 56.17, f"asked from Home: {asked}")


def test_no_device_and_no_home_says_so() -> None:
    hass = FakeHass([DK], home=(None, None))
    response, asked = _ask(hass)
    check(asked == [], "nothing was searched")
    check(response.speech == nearby.spoken_no_position(True), response.speech)


def test_the_answer_is_marked_as_an_answer() -> None:
    """A query answer, not an action report — it is what Assist shows."""
    response, _ = _ask(FakeHass([DK]))
    check(response.response_type == "query_answer", str(response.response_type))
    check(
        (response.speech_slots or {}).get("stations") is not None,
        "and the stations ride along for anything that can show them",
    )


def test_english_when_home_assistant_is_english() -> None:
    hass = FakeHass([DK], language="en")
    response, _ = _ask(hass, fuel="hvo")
    check(response.speech == nearby.spoken_not_followed("HVO100", False), response.speech)


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except AssertionError as err:
            failures += 1
            print(f"FAIL {name}: {err}")
        else:
            print(f"ok   {name}")
    print(f"{checks} checks" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

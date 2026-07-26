"""Tests for the spoken sentence a Siri Shortcut reads out (nearby.py).

Run with: python tests/test_spoken.py

`spoken_sentence` and `_spoken_place` live in `nearby.py`, which imports nothing
from Home Assistant — so this needs no stubbing at all. The argument named
`spoken` in each test is that module.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load_nearby import load_nearby  # noqa: E402


def station(name: str, price: float, km: float, company: str = "OK") -> dict:
    return {
        "name": name,
        "company": company,
        "city": "Silkeborg",
        "price": price,
        "distance_km": km,
    }


def test_identical_prices_are_stated_once(spoken) -> None:
    """The case that made the sentence useless: one chain, one national price.

    Three OK forecourts at 16.19 read as "OK Silkeborg" three times, so the
    listener learned nothing they could choose on.
    """
    said = spoken.spoken_sentence(
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


def test_differing_prices_keep_the_price_per_station(spoken) -> None:
    said = spoken.spoken_sentence(
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


def test_english_uses_a_decimal_point(spoken) -> None:
    said = spoken.spoken_sentence(
        [station("OK Nordre Ringvej 110", 16.19, 1.9)], danish=False
    )
    assert said == "Number 1: OK Nordre Ringvej, 16.19 kroner, 1.9 kilometres.", said


def test_at_most_three_are_named(spoken) -> None:
    ranked = [station(f"OK Vej {i}", 16.19 + i, float(i)) for i in range(1, 9)]
    said = spoken.spoken_sentence(ranked, danish=True)
    assert said.count("Nummer") == 3, said
    assert "Nummer 4" not in said, said


def test_no_stations(spoken) -> None:
    assert spoken.spoken_sentence([], danish=True) == "Ingen stationer i nærheden."
    assert spoken.spoken_sentence([], danish=False) == "No stations nearby."


def test_cheapest_names_one_station(spoken) -> None:
    """The sentence the documented shortcut speaks: one station, no list.

    Same trimming and the same Danish decimal comma as the three-station
    version, because it is read aloud in a car just the same.
    """
    ranked = [
        station("OK Nordre Ringvej 110", 16.19, 1.9),
        station("Q8 Vestergade 5", 16.49, 1.2, company="Q8"),
    ]
    assert spoken.spoken_cheapest(ranked, danish=True) == (
        "Billigste er OK Nordre Ringvej, 16,19 kroner, 1,9 kilometer væk."
    ), spoken.spoken_cheapest(ranked, danish=True)
    assert spoken.spoken_cheapest(ranked, danish=False) == (
        "The cheapest is OK Nordre Ringvej, 16.19 kroner, 1.9 kilometres away."
    ), spoken.spoken_cheapest(ranked, danish=False)


def test_cheapest_says_the_second_station_nothing(spoken) -> None:
    """It names the first and only the first — the list is deliberately gone."""
    ranked = [
        station("OK Nordre Ringvej 110", 16.19, 1.9),
        station("Q8 Vestergade 5", 16.49, 1.2, company="Q8"),
    ]
    said = spoken.spoken_cheapest(ranked, danish=True)
    assert "Q8" not in said and "Nummer" not in said, said


def test_cheapest_with_no_stations(spoken) -> None:
    assert spoken.spoken_cheapest([], danish=True) == "Ingen stationer i nærheden."
    assert spoken.spoken_cheapest([], danish=False) == "No stations nearby."


def test_house_number_trimming(spoken) -> None:
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
        got = spoken._spoken_place({"name": raw, "company": "X", "city": "Y"})
        assert got == expected, f"{raw!r} -> {got!r}, wanted {expected!r}"


def test_falls_back_to_company_and_city_without_a_name(spoken) -> None:
    got = spoken._spoken_place({"name": "", "company": "Q8", "city": "Silkeborg"})
    assert got == "Q8 Silkeborg", got


def main() -> int:
    spoken = load_nearby()
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn(spoken)
        except AssertionError as err:
            failures += 1
            print(f"FAIL {name}: {err}")
        else:
            print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

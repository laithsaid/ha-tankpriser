"""Tests for per-chain loyalty discounts (sources.chain_key / apply_discounts).

Run with: python tests/test_discounts.py

`sources.py` has no Home Assistant imports, only aiohttp, which is stubbed here
so the test needs nothing installed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)


def _load() -> tuple[types.ModuleType, types.ModuleType]:
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
    return modules["const"], modules["sources"]


const, sources = _load()
Station = sources.Station
chain_key = sources.chain_key
apply_discounts = sources.apply_discounts


def station(company: str, price: float = 16.99, **prices) -> Station:
    return Station(
        name=f"{company} Testby",
        company=company,
        postnummer="8600",
        updated="2026-07-25",
        prices={"blyfri95": price, **prices},
    )


# -- identifying the chain ---------------------------------------------------
def test_chain_key_matches_how_providers_spell_themselves() -> None:
    cases = {
        "Q8 Service": "q8", "Q8": "q8", "F24": "f24",
        "OK": "ok", "OK Plus": "ok",
        "Shell": "shell", "Shell Express": "shell", "Shell/7-Eleven": "shell",
        "OIL!": "oil", "Circle K": "circlek", "INGO": "circlek",
        "Go'on": "goon", "Uno-X": "unox",
    }
    for company, expected in cases.items():
        assert chain_key(company) == expected, company


def test_chain_key_order_matters() -> None:
    """"ok" is two letters and hides inside other names, so it is tested last."""
    assert chain_key("OKQ8") == "q8", "Q8 must win over the substring 'OK'"
    assert chain_key("") is None
    assert chain_key("Tankstationen Bent") is None


# -- applying the discount ---------------------------------------------------
def test_discount_is_subtracted_and_the_pump_price_kept() -> None:
    out = apply_discounts([station("OK", 16.99, diesel=15.49)], {"ok": 20})
    assert out[0].prices == {"blyfri95": 16.79, "diesel": 15.29}
    assert out[0].list_prices == {"blyfri95": 16.99, "diesel": 15.49}
    assert out[0].discount_ore == 20


def test_only_the_chains_you_have_a_card_for() -> None:
    stations = [station("OK"), station("Shell"), station("Q8 Service")]
    out = apply_discounts(stations, {"ok": 20})
    by_company = {s.company: s for s in out}
    assert by_company["OK"].prices["blyfri95"] == 16.79
    assert by_company["Shell"].prices["blyfri95"] == 16.99
    assert by_company["Shell"].list_prices == {}, "no discount, no pump-price echo"
    assert by_company["Q8 Service"].discount_ore == 0


def test_the_shared_provider_cache_is_never_mutated() -> None:
    """The parsed stations are cached and shared across areas.

    Subtracting in place would compound the discount on every refresh and leak
    one area's loyalty card into another's prices, so new objects are built.
    """
    original = station("OK", 16.99)
    out = apply_discounts([original], {"ok": 20})
    assert out[0] is not original
    assert original.prices == {"blyfri95": 16.99}
    assert original.list_prices == {} and original.discount_ore == 0


def test_applying_twice_cannot_compound() -> None:
    once = apply_discounts([station("OK", 16.99)], {"ok": 20})
    twice = apply_discounts(once, {"ok": 20})
    assert twice[0].prices["blyfri95"] == 16.79, "a discount must apply once only"
    assert twice[0].list_prices["blyfri95"] == 16.99


def test_an_absurd_discount_is_clamped_not_negative() -> None:
    """A user typing kroner into an øre field must not invent free fuel."""
    out = apply_discounts([station("OK", 16.99)], {"ok": 99_999})
    expected = round(16.99 - const.MAX_DISCOUNT_ORE / 100, 2)
    assert out[0].prices["blyfri95"] == expected
    assert out[0].prices["blyfri95"] > 0

    cheap = apply_discounts([station("OK", 0.50)], {"ok": const.MAX_DISCOUNT_ORE})
    assert cheap[0].prices["blyfri95"] > 0, "never zero or below"


def test_no_discounts_configured_is_a_pass_through() -> None:
    stations = [station("OK"), station("Shell")]
    assert apply_discounts(stations, {}) is stations
    out = apply_discounts(stations, {"ok": 0})
    assert out[0].discount_ore == 0 and out[0].list_prices == {}


def test_a_chain_with_no_prices_is_left_alone() -> None:
    empty = Station(name="X", company="OK", postnummer="8600", updated="", prices={})
    out = apply_discounts([empty], {"ok": 20})
    assert out[0].prices == {} and out[0].discount_ore == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} discount tests passed")

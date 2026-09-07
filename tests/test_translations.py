"""Guards on strings.json and the translations beside it.

Run with: python tests/test_translations.py

Two failures that only ever show up in CI, and cost a red build on main each
time:

* **Key shape.** Home Assistant requires every translation key to match
  ``[a-z0-9-_]+``. A selector's options are keyed by the *value* they carry, so
  the moment an internal code is uppercase — country codes were, for one
  afternoon — hassfest rejects the whole integration. Nothing local caught it.
* **Drift.** strings.json, translations/en.json and translations/da.json are
  three files edited by hand, and a key added to one and forgotten in another
  shows up as an untranslated blank in the dialog rather than as an error.

Neither needs Home Assistant, so both can be caught before pushing.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)
SOURCE = os.path.join(BASE, "strings.json")
TRANSLATIONS = os.path.join(BASE, "translations")

# hassfest's rule, copied exactly.
KEY = re.compile(r"^[a-z0-9-_]+$")
# Where the keys are ours to choose, so the rule applies. Elsewhere the keys are
# fixed words from Home Assistant's own schema ("step", "data", "error"...) and
# a mismatch there is a different mistake with a different message.
CHECKED_PARENTS = ("data", "data_description", "options", "menu_options", "fields")

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def load(path: str) -> dict:
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


def walk(node, path=""):
    """Yield (dotted path, key, value) for every mapping entry."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            yield path, key, value
            yield from walk(value, here)


def keyset(node, path=""):
    """Every dotted key path in a document, for comparing two of them."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            found.add(here)
            found |= keyset(value, here)
    return found


def test_every_key_matches_the_rule() -> None:
    print("translation keys are lowercase, as hassfest demands")
    files = [SOURCE] + [
        os.path.join(TRANSLATIONS, name)
        for name in sorted(os.listdir(TRANSLATIONS))
        if name.endswith(".json")
    ]
    bad: list[str] = []
    for path in files:
        for parent, key, _value in walk(load(path)):
            if parent.rsplit(".", 1)[-1] in CHECKED_PARENTS and not KEY.match(key):
                bad.append(f"{os.path.basename(path)}: {parent}.{key}")
    check("no key breaks [a-z0-9-_]+", not bad, bad[:4])
    check("and there were files to check", len(files) >= 3, files)


def test_the_three_files_agree() -> None:
    print("strings.json, en.json and da.json carry the same keys")
    source = keyset(load(SOURCE))
    for name in ("en.json", "da.json"):
        other = keyset(load(os.path.join(TRANSLATIONS, name)))
        missing = sorted(source - other)
        extra = sorted(other - source)
        check(f"{name} is missing nothing", not missing, missing[:4])
        check(f"{name} has nothing strings.json lacks", not extra, extra[:4])


def test_nothing_is_left_blank() -> None:
    print("every leaf actually says something")
    blank: list[str] = []
    for name in ("../strings.json", "en.json", "da.json"):
        path = os.path.join(TRANSLATIONS, name)
        for parent, key, value in walk(load(path)):
            if isinstance(value, str) and not value.strip():
                blank.append(f"{name}: {parent}.{key}")
    check("no empty string slipped in", not blank, blank[:4])


def test_the_country_options_match_the_code() -> None:
    """The specific pairing that broke the build: a selector's option keys are
    the values it stores, so they and the constants have to be the same text."""
    print("the country selector matches const.COUNTRIES")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tp_const", os.path.join(BASE, "const.py")
    )
    const = importlib.util.module_from_spec(spec)
    sys.modules["tp_const"] = const
    spec.loader.exec_module(const)

    options = set(load(SOURCE)["selector"]["country"]["options"])
    check(
        "every country has a label and every label a country",
        options == set(const.COUNTRIES),
        (sorted(options), sorted(const.COUNTRIES)),
    )
    check(
        "and the codes are lowercase, because they double as translation keys",
        all(KEY.match(code) for code in const.COUNTRIES),
        sorted(const.COUNTRIES),
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print(f"all {len(tests)} translation checks passed")

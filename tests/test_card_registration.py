"""Tests for how __init__.py publishes the card as a Lovelace resource.

Run with: python tests/test_card_registration.py

This is the mechanism behind v0.9.1: `add_extra_js_url` alone missed any client
holding an older index.html, which showed the card as "Configuration error" for
days. A stored Lovelace resource reaches those clients, so getting the storage /
YAML / already-registered cases right matters.

Same `ast` lifting as tests/test_geocode.py — the module imports Home Assistant,
which is not worth installing to test this logic.
"""

from __future__ import annotations

import ast
import asyncio
import io
import logging
import os
import sys
import types
from typing import Any

SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "tankpriser",
    "__init__.py",
)
CARD_URL = "/tankpriser/tankpriser-card.js"
URL = f"{CARD_URL}?v=0.10.0"


def _load():
    tree = ast.parse(io.open(SOURCE, encoding="utf-8").read())
    name = "_async_register_lovelace_resource"
    node = next((n for n in tree.body if getattr(n, "name", "") == name), None)
    if node is None:
        raise SystemExit(f"__init__.py no longer defines {name}")
    namespace: dict[str, Any] = {
        "_LOGGER": logging.getLogger("test"),
        "CARD_URL": CARD_URL,
        "HomeAssistant": object,
    }
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(future + [node], []), "<init>", "exec"), namespace)
    return namespace[name]


register = _load()
run = asyncio.run


class FakeHass:
    def __init__(self, data: dict) -> None:
        self.data = data


class StorageCollection:
    """Stand-in for lovelace's ResourceStorageCollection."""

    def __init__(self, items: list, loaded: bool = False) -> None:
        self._items = items
        self.loaded = loaded
        self.log: list = []

    async def async_load(self) -> None:
        self.log.append("load")
        self.loaded = True

    def async_items(self) -> list:
        # Empty until loaded — the real collection lazy-loads, and forgetting
        # that would create a duplicate resource on every restart.
        return list(self._items) if self.loaded else []

    async def async_create_item(self, data: dict) -> None:
        assert set(data) == {"res_type", "url"}, data
        self.log.append(("create", data["url"]))
        self._items.append({"id": "new", **data})

    async def async_update_item(self, item_id: str, updates: dict) -> None:
        assert set(updates) <= {"res_type", "url"}, updates
        self.log.append(("update", item_id, updates["url"]))


class YamlCollection:
    """ResourceYAMLCollection: read-only, owned by configuration.yaml."""

    loaded = True

    def async_items(self) -> list:
        return []


def hass_with(resources) -> FakeHass:
    return FakeHass({"lovelace": types.SimpleNamespace(resources=resources)})


def resource(url: str, item_id: str = "r1") -> dict:
    return {"id": item_id, "type": "module", "url": url}


def test_no_lovelace_yet() -> None:
    """Setup can run before lovelace; that must be reported, not crash."""
    assert run(register(FakeHass({}), URL)) is False


def test_yaml_mode_is_left_alone() -> None:
    assert run(register(hass_with(YamlCollection()), URL)) is False


def test_creates_the_resource_once() -> None:
    collection = StorageCollection([])
    assert run(register(hass_with(collection), URL)) is True
    assert collection.log == ["load", ("create", URL)]
    # Called again on the next setup: must not add a second copy.
    assert run(register(hass_with(collection), URL)) is True
    assert collection.log == ["load", ("create", URL)]


def test_updates_a_stale_version() -> None:
    """The ?v= must follow the installed version or a cache masks the update."""
    collection = StorageCollection([resource(f"{CARD_URL}?v=0.8.1")], loaded=True)
    assert run(register(hass_with(collection), URL)) is True
    assert collection.log == [("update", "r1", URL)]


def test_adopts_a_hand_added_resource() -> None:
    """Someone who added the card by hand keeps one entry, now versioned."""
    collection = StorageCollection([resource(CARD_URL, "r2")], loaded=True)
    assert run(register(hass_with(collection), URL)) is True
    assert collection.log == [("update", "r2", URL)]


def test_leaves_other_resources_untouched() -> None:
    collection = StorageCollection([resource("/hacsfiles/foo/foo.js", "r3")],
                                  loaded=True)
    assert run(register(hass_with(collection), URL)) is True
    assert collection.log == [("create", URL)]
    assert any(i["url"] == "/hacsfiles/foo/foo.js" for i in collection._items)


def test_legacy_dict_shaped_lovelace_is_ignored() -> None:
    """hass.data["lovelace"] was a dict before HA 2024.5.

    The minimum supported version is 2025.2, so that shape is not handled any
    more — it must be ignored rather than written to.
    """
    collection = StorageCollection([], loaded=True)
    assert run(register(FakeHass({"lovelace": {"resources": collection}}), URL)) is False
    assert collection.log == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} card-registration tests passed")
    sys.exit(0)

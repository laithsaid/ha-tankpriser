"""Load `nearby.py` (and the `const.py` it needs) without Home Assistant.

Both modules are pure Python, so this is a plain import inside a throwaway
package — no stubbing, no `homeassistant` installed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)


def load_nearby() -> types.ModuleType:
    """Return the `nearby` module, imported as `tp.nearby`."""
    if "tp" not in sys.modules:
        package = types.ModuleType("tp")
        package.__path__ = [BASE]
        sys.modules["tp"] = package
    for name in ("const", "nearby"):
        if f"tp.{name}" in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(
            f"tp.{name}", os.path.join(BASE, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tp.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["tp.nearby"]

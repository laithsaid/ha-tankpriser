"""Every name imported from a sibling module must actually exist there.

Run with: python tests/test_imports.py

This exists because of a real failure: `country_of` was added to the
`from .sources import (...)` block in `config_flow.py` when it lives in
`const.py`. Python only notices at import time, Home Assistant reported it as
"Setup failed for custom integration 'tankpriser'", and the whole integration —
both countries, every sensor, every service — failed to load. Nothing caught it
first: the other test files deliberately avoid importing anything that pulls in
Home Assistant, so no test ever imported `config_flow.py` at all.

The check is static, so it needs neither Home Assistant nor network: parse
every module in the package, collect the names each one defines at the top
level, and confirm that every relative import asks for a name that is there.
"""

from __future__ import annotations

import ast
import io
import os

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "tankpriser"
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def _tree(path: str) -> ast.Module:
    return ast.parse(io.open(path, encoding="utf-8").read(), filename=path)


def _exported(tree: ast.Module) -> set[str]:
    """Top-level names a module offers: definitions, assignments, re-exports."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


modules = sorted(
    name[:-3]
    for name in os.listdir(BASE)
    if name.endswith(".py") and not name.startswith("__")
)
trees = {name: _tree(os.path.join(BASE, f"{name}.py")) for name in modules}
exported = {name: _exported(tree) for name, tree in trees.items()}

print(f"every module parses ({len(modules)} of them)")
check("all modules parsed", len(trees) == len(modules))

print("every relative import resolves")
for module, tree in trees.items():
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        target = node.module
        if target is None or target not in exported:
            # `from . import geo, geocode` — the names are modules themselves.
            for alias in node.names:
                check(
                    f"{module}: from . import {alias.name}",
                    alias.name in modules,
                    "no such module in the package",
                )
            continue
        for alias in node.names:
            check(
                f"{module}: from .{target} import {alias.name}",
                alias.name in exported[target],
                f"{alias.name} is not defined in {target}.py",
            )

# --- entity classes keep their entity methods ------------------------------
# Added after breaking exactly this, on 2026-09-17. A helper was inserted into
# sensor.py at column 0 while sitting inside a class body. That ends the class,
# and the methods that followed became nested functions inside the helper --
# valid Python, so `ast.parse` was happy, the import check was happy, and both
# suites stayed green. `TankpriserSensor` silently lost
# `extra_state_attributes` and `_handle_coordinator_update`, and every area
# sensor went out with nothing but a state: no station list, no station_count,
# no fuel_type. It looked like a data problem for a good ten minutes.
#
# Nothing here can catch an indentation mistake by reading the code, so this
# asserts the shape instead: these classes must carry these methods, by name.
REQUIRED_METHODS = {
    ("sensor", "TankpriserSensor"): (
        "native_value",
        "extra_state_attributes",
        "_handle_coordinator_update",
    ),
    ("sensor", "CarPredictionSensor"): ("native_value", "extra_state_attributes"),
    ("sensor", "NearbyStationsSensor"): (
        "native_value",
        "extra_state_attributes",
        "_handle_coordinator_update",
    ),
}

print()
print("entity classes still carry their entity methods")
for (module, class_name), required in sorted(REQUIRED_METHODS.items()):
    tree = ast.parse(
        io.open(os.path.join(BASE, f"{module}.py"), encoding="utf-8").read()
    )
    found = {
        node.name
        for top in tree.body
        if isinstance(top, ast.ClassDef) and top.name == class_name
        for node in top.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in required:
        check(
            f"{module}.{class_name}.{name}",
            name in found,
            "not a method of that class -- check the indentation of anything "
            "defined above it",
        )

print()
if FAILURES:
    print(f"{len(FAILURES)} import checks FAILED")
    raise SystemExit(1)
print("all import checks passed")

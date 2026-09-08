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

print()
if FAILURES:
    print(f"{len(FAILURES)} import checks FAILED")
    raise SystemExit(1)
print("all import checks passed")

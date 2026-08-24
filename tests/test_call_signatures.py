"""Every keyword passed between our own functions must be one they accept.

Three bugs of the same shape reached production in one session:

  * the page-marker pass was wired into a function the pipeline never calls;
  * a layout block referenced a name that lived one frame out (NameError);
  * `date_convention_resolved` was attached to build_opinion instead of
    build_summary (TypeError on every job).

All three looked right, all three were unreachable by the test suite, and all
three only surfaced against live API keys - because the orchestration code that
wires stages together is exactly the code a unit test does not execute.

This walks the source of every module under `app/` and checks each call to one
of our own functions against that function's real signature. It needs no keys,
no network, and no fixtures, and it fails on the mistake rather than on its
consequences three stages later.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _modules() -> list[str]:
    names: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            relative = path.relative_to(APP_ROOT.parent).parent
        else:
            relative = path.relative_to(APP_ROOT.parent).with_suffix("")
        module = ".".join(relative.parts)
        if module:
            names.append(module)
    return sorted(set(names))


def _imported_targets(tree: ast.Module, module_name: str) -> dict[str, str]:
    """Local name -> "module:attribute" for everything imported from app.*"""
    targets: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            for alias in node.names:
                local = alias.asname or alias.name
                targets[local] = f"{node.module}:{alias.name}"
    # Functions defined in this module are callable by bare name too.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets.setdefault(node.name, f"{module_name}:{node.name}")
    return targets


def _resolve(target: str):  # noqa: ANN202 - any callable
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attribute, None)


def _check_call(node: ast.Call, targets: dict[str, str], module_name: str) -> list[str]:
    if not isinstance(node.func, ast.Name):
        return []
    target = targets.get(node.func.id)
    if not target:
        return []
    func = _resolve(target)
    if func is None or not callable(func) or inspect.isclass(func):
        return []
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return []

    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return []

    problems: list[str] = []
    for keyword in node.keywords:
        if keyword.arg is None:  # **spread, cannot be checked statically
            continue
        if keyword.arg not in signature.parameters:
            problems.append(
                f"{module_name}:{node.lineno} calls {node.func.id}() with "
                f"{keyword.arg!r}, which it does not accept. Its parameters are: "
                f"{', '.join(signature.parameters) or '(none)'}"
            )
    return problems


@pytest.mark.parametrize("module_name", _modules())
def test_calls_between_our_own_functions_match_their_signatures(module_name):
    path = APP_ROOT.parent / Path(*module_name.split("."))
    source_path = path.with_suffix(".py")
    if not source_path.exists():
        source_path = path / "__init__.py"
    if not source_path.exists():
        pytest.skip(f"no source for {module_name}")

    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    targets = _imported_targets(tree, module_name)

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            problems.extend(_check_call(node, targets, module_name))

    assert not problems, "\n".join(problems)


def test_the_check_would_have_caught_the_bug_that_motivated_it():
    """A guard on the guard: if this stops detecting a bad keyword, the whole
    file becomes decoration."""
    source = "from app.services.extraction.opinion import build_opinion\nbuild_opinion(b, h, nope=1)\n"
    tree = ast.parse(source)
    targets = _imported_targets(tree, "fake")

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            problems.extend(_check_call(node, targets, "fake"))

    assert problems and "nope" in problems[0]


def test_a_correct_call_raises_nothing():
    source = (
        "from app.services.extraction.opinion import build_opinion\n"
        "build_opinion(b, h, rule_config=None)\n"
    )
    tree = ast.parse(source)
    targets = _imported_targets(tree, "fake")

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            problems.extend(_check_call(node, targets, "fake"))

    assert problems == []

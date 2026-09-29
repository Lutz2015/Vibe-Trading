"""Load sandboxed python_module strategies for Qbit live target picking."""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

MODULES_ROOT = Path.home() / ".person-trading" / "strategies" / "modules"

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "pathlib",
        "shutil",
        "pickle",
        "builtins",
        "__builtin__",
        "importlib",
        "ctypes",
        "multiprocessing",
        "threading",
        "requests",
        "urllib",
        "http",
        "ftplib",
        "telnetlib",
    }
)


class PythonModuleStrategyError(ValueError):
    """Raised when a python_module strategy cannot be loaded or executed."""


def modules_root() -> Path:
    root = MODULES_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def module_path_for(strategy_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in strategy_id.strip())
    if not safe:
        raise PythonModuleStrategyError("invalid strategy_id for module path")
    return modules_root() / f"{safe}.py"


def write_module(strategy_id: str, source: str) -> Path:
    path = module_path_for(strategy_id)
    _validate_module_source(source, path)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def resolve_module_path(strategy_id: str, configured_path: str | None) -> Path:
    if configured_path:
        candidate = Path(configured_path).expanduser()
        if not candidate.is_absolute():
            candidate = modules_root() / candidate.name
        candidate = candidate.resolve()
        root = modules_root().resolve()
        if root not in candidate.parents and candidate != root:
            raise PythonModuleStrategyError(f"module path must stay under {root}")
        if candidate.suffix != ".py":
            candidate = candidate.with_suffix(".py")
        if candidate.exists():
            return candidate
    fallback = module_path_for(strategy_id)
    if fallback.exists():
        return fallback
    raise PythonModuleStrategyError(
        f"python module not found for {strategy_id!r}; import with module_code or place file at {fallback}"
    )


def _validate_module_source(source: str, path: Path) -> None:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PythonModuleStrategyError(f"module syntax error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    raise PythonModuleStrategyError(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    raise PythonModuleStrategyError(f"forbidden import: {node.module}")


def _load_module(path: Path) -> ModuleType:
    _validate_module_source(path.read_text(encoding="utf-8"), path)
    spec = importlib.util.spec_from_file_location(f"qbit_strategy_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise PythonModuleStrategyError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_targets_from_module(
    strategy_id: str,
    quotes: dict[str, Any],
    universe: list[str],
    params: dict[str, Any],
    *,
    module_path: str | None = None,
) -> list[dict[str, Any]]:
    """Execute python_module strategy and return [{symbol, weight}, ...]."""
    path = resolve_module_path(strategy_id, module_path)
    module = _load_module(path)

    fn = getattr(module, "build_targets", None)
    if callable(fn):
        result = fn(quotes, universe, params)
    else:
        cls = getattr(module, "LiveStrategy", None) or getattr(module, "Strategy", None)
        if cls is None:
            raise PythonModuleStrategyError(
                "module must define build_targets(quotes, universe, params) "
                "or LiveStrategy/Strategy.build_targets(...)"
            )
        instance = cls()
        build = getattr(instance, "build_targets", None)
        if not callable(build):
            raise PythonModuleStrategyError("Strategy class missing build_targets method")
        result = build(quotes, universe, params)

    if not isinstance(result, list) or not result:
        raise PythonModuleStrategyError("build_targets must return a non-empty list")

    normalized: list[dict[str, Any]] = []
    for row in result:
        if not isinstance(row, dict):
            raise PythonModuleStrategyError("each target must be a dict with symbol/weight")
        symbol = str(row.get("symbol", "")).strip().upper()
        weight = float(row.get("weight", 0.0))
        if not symbol or weight <= 0:
            continue
        normalized.append({"symbol": symbol, "weight": weight})
    if not normalized:
        raise PythonModuleStrategyError("build_targets returned no valid targets")
    total = sum(item["weight"] for item in normalized)
    if total <= 0:
        raise PythonModuleStrategyError("build_targets weights must sum > 0")
    return [{"symbol": item["symbol"], "weight": round(item["weight"] / total, 6)} for item in normalized]

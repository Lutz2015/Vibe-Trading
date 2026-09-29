"""Import person-trading-strategy.yaml into Qbit auto-trading config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.strategy_yaml.models import BUILTIN_MODES, StrategyDocument
from src.strategy_yaml.python_module import (
    PythonModuleStrategyError,
    module_path_for,
    write_module,
)

STRATEGY_MODES = BUILTIN_MODES


class StrategyImportError(ValueError):
    """Raised when YAML cannot be parsed or applied."""


def parse_strategy_yaml(content: str) -> StrategyDocument:
    """Parse and validate strategy YAML DSL."""
    if not (content or "").strip():
        raise StrategyImportError("empty yaml content")
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise StrategyImportError(f"invalid yaml: {exc}") from exc
    if not isinstance(raw, dict):
        raise StrategyImportError("strategy yaml root must be a mapping")
    try:
        return StrategyDocument.model_validate(raw)
    except ValidationError as exc:
        raise StrategyImportError(str(exc)) from exc


def strategy_entry_from_document(
    doc: StrategyDocument,
    *,
    module_code: str | None = None,
) -> dict[str, Any]:
    """Convert DSL document to one auto-trading.yaml strategies[] entry."""
    params = dict(doc.signals.params or {})
    entry: dict[str, Any] = {
        "strategy_id": doc.meta.id,
        "label": doc.meta.name,
        "enabled": False,
        "expected_return_pct": float(params.get("expected_return_pct", 12.0)),
        "risk_volatility_pct": float(params.get("risk_volatility_pct", 10.0)),
        "max_weight": float(params.get("max_weight", doc.portfolio.max_single_weight)),
    }
    if doc.meta.description:
        entry["description"] = doc.meta.description

    if doc.signals.type == "python_module":
        if not (module_code or "").strip() and not (doc.signals.path or "").strip():
            raise StrategyImportError(
                "python_module requires signals.path or import request module_code"
            )
        if module_code and module_code.strip():
            try:
                saved = write_module(doc.meta.id, module_code.strip())
            except PythonModuleStrategyError as exc:
                raise StrategyImportError(str(exc)) from exc
            module_path = str(saved)
        elif doc.signals.path:
            agent_root = Path(__file__).resolve().parents[2]
            source = (agent_root / doc.signals.path).resolve()
            if not source.exists():
                source = Path(doc.signals.path).expanduser()
            if source.exists():
                try:
                    saved = write_module(doc.meta.id, source.read_text(encoding="utf-8"))
                except PythonModuleStrategyError as exc:
                    raise StrategyImportError(str(exc)) from exc
                module_path = str(saved)
            else:
                raise StrategyImportError(f"python module source not found: {doc.signals.path}")
        else:
            module_path = str(module_path_for(doc.meta.id))
        entry["signal_type"] = "python_module"
        entry["module_path"] = module_path
        entry["params"] = {
            **{k: v for k, v in params.items() if k not in {"expected_return_pct", "risk_volatility_pct", "max_weight"}},
            "universe_symbols": list(doc.universe.symbols),
            "module_path": module_path,
        }
        return entry

    if doc.signals.type != "builtin":
        raise StrategyImportError(
            f"signals.type={doc.signals.type!r} is not supported; use builtin or python_module"
        )
    mode = doc.signals.mode
    if mode not in STRATEGY_MODES:
        raise StrategyImportError(
            f"unsupported signals.mode: {mode!r}; allowed: {', '.join(STRATEGY_MODES)}"
        )
    top_n = int(params.get("top_n", 5))
    entry["params"] = {
        "mode": mode,
        "top_n": top_n,
        "universe_symbols": list(doc.universe.symbols),
    }
    return entry


def _merge_global_settings(config: dict[str, Any], doc: StrategyDocument) -> None:
    exec_cfg = doc.execution
    config["mode"] = str(exec_cfg.mode or config.get("mode", "paper"))
    config["execution_mode"] = str(exec_cfg.mode or config.get("execution_mode", "paper"))
    config["poll_interval_sec"] = int(exec_cfg.poll_interval_sec)
    config["trading_hours"] = dict(exec_cfg.trading_hours)
    config["rebalance"] = {
        "time_local": exec_cfg.time_local,
        "window_minutes": int((config.get("rebalance") or {}).get("window_minutes", 30)),
        "rebalance_every_trading_days": int(exec_cfg.rebalance_every_trading_days),
    }
    portfolio = doc.portfolio
    config["portfolio"] = {
        "initial_cash": float(portfolio.initial_cash),
        "total_exposure": float(portfolio.total_exposure),
        "max_single_weight": float(portfolio.max_single_weight),
        "risk_aversion": float((config.get("portfolio") or {}).get("risk_aversion", 1.0)),
        "min_trade_lot": int(portfolio.min_trade_lot),
        "fee_bps": float(portfolio.fee_bps),
    }
    if doc.risk:
        config["risk"] = dict(doc.risk)
    if doc.agent:
        config["agent"] = dict(doc.agent)


def apply_strategy_yaml_to_config(
    config: dict[str, Any],
    content: str,
    *,
    activate: bool = False,
    merge_globals: bool = True,
    module_code: str | None = None,
) -> tuple[dict[str, Any], StrategyDocument, bool]:
    """Upsert a strategy entry into auto-trading config.

    Returns:
        (updated_config, parsed_document, created_new_entry)
    """
    doc = parse_strategy_yaml(content)
    entry = strategy_entry_from_document(doc, module_code=module_code)
    strategy_id = entry["strategy_id"]

    if merge_globals:
        _merge_global_settings(config, doc)

    items: list[dict[str, Any]] = list(config.get("strategies") or [])
    created = True
    for idx, item in enumerate(items):
        if str(item.get("strategy_id", "")).strip() == strategy_id:
            # Preserve enabled flag unless activate requested.
            entry["enabled"] = bool(item.get("enabled", False))
            items[idx] = entry
            created = False
            break
    else:
        items.append(entry)

    if activate:
        for item in items:
            sid = str(item.get("strategy_id", "")).strip()
            item["enabled"] = sid == strategy_id

    config["strategies"] = items
    config.setdefault("enabled", True)
    return config, doc, created


def load_auto_trading_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise StrategyImportError(f"auto trading config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise StrategyImportError("auto trading config root must be a mapping")
    return data


def save_auto_trading_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def import_strategy_yaml_file(
    config_path: Path,
    content: str,
    *,
    activate: bool = False,
    merge_globals: bool = True,
    module_code: str | None = None,
) -> dict[str, Any]:
    """Load, apply YAML, persist auto-trading config. Returns summary dict."""
    config = load_auto_trading_config(config_path)
    config, doc, created = apply_strategy_yaml_to_config(
        config,
        content,
        activate=activate,
        merge_globals=merge_globals,
        module_code=module_code,
    )
    save_auto_trading_config(config_path, config)
    imported_dir = Path.home() / ".person-trading" / "strategies" / "imported"
    imported_dir.mkdir(parents=True, exist_ok=True)
    (imported_dir / f"{doc.meta.id}.yaml").write_text(content.strip() + "\n", encoding="utf-8")
    return {
        "strategy_id": doc.meta.id,
        "name": doc.meta.name,
        "created": created,
        "activated": activate,
        "mode": doc.signals.mode if doc.signals.type == "builtin" else doc.signals.type,
        "signal_type": doc.signals.type,
        "universe_size": len(doc.universe.symbols),
    }

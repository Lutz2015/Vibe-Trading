"""Export Qbit auto-trading config entries to person-trading-strategy.yaml."""

from __future__ import annotations

from typing import Any

import yaml

from src.strategy_yaml.models import (
    StrategyDocument,
    StrategyExecution,
    StrategyMeta,
    StrategyPortfolio,
    StrategySignals,
    StrategyUniverse,
)


def _strategy_item(config: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    for item in config.get("strategies") or []:
        if str(item.get("strategy_id", "")).strip() == strategy_id:
            return item
    raise KeyError(f"strategy not found: {strategy_id}")


def document_from_qbit_config(config: dict[str, Any], strategy_id: str) -> StrategyDocument:
    """Build DSL document from auto-trading.yaml + one strategy entry."""
    item = _strategy_item(config, strategy_id)
    params = dict(item.get("params") or {})
    signal_type = str(item.get("signal_type") or params.pop("signal_type", "builtin") or "builtin")
    portfolio_cfg = dict(config.get("portfolio") or {})
    rebalance_cfg = dict(config.get("rebalance") or {})
    risk_cfg = dict(config.get("risk") or {})
    agent_cfg = dict(config.get("agent") or {})

    universe = [str(s) for s in params.get("universe_symbols") or []]
    if signal_type == "python_module":
        module_path = str(
            item.get("module_path")
            or params.get("module_path")
            or f"strategies/modules/{strategy_id}.py"
        )
        signals = StrategySignals(
            type="python_module",
            path=module_path,
            params={k: v for k, v in params.items() if k not in {"universe_symbols", "module_path", "mode"}},
        )
    else:
        mode = str(params.get("mode") or "momentum")
        signal_params = {
            k: v
            for k, v in params.items()
            if k not in {"universe_symbols", "mode", "module_path", "signal_type"}
        }
        if "top_n" not in signal_params and "top_n" in params:
            signal_params["top_n"] = params["top_n"]
        signals = StrategySignals(type="builtin", mode=mode, params=signal_params)

    return StrategyDocument(
        meta=StrategyMeta(
            id=strategy_id,
            name=str(item.get("label") or strategy_id),
            description=str(item.get("description") or ""),
        ),
        universe=StrategyUniverse(symbols=universe or ["000001.SZ"]),
        signals=signals,
        execution=StrategyExecution(
            mode=str(config.get("execution_mode") or config.get("mode") or "paper"),
            rebalance_every_trading_days=int(rebalance_cfg.get("rebalance_every_trading_days", 15)),
            time_local=str(rebalance_cfg.get("time_local", "14:50")),
            poll_interval_sec=int(config.get("poll_interval_sec", 60)),
            trading_hours=dict(config.get("trading_hours") or {"start": "09:30", "end": "15:00"}),
        ),
        portfolio=StrategyPortfolio(
            initial_cash=float(portfolio_cfg.get("initial_cash", 1_000_000.0)),
            total_exposure=float(portfolio_cfg.get("total_exposure", 1.0)),
            max_single_weight=float(portfolio_cfg.get("max_single_weight", 0.5)),
            min_trade_lot=int(portfolio_cfg.get("min_trade_lot", 100)),
            fee_bps=float(portfolio_cfg.get("fee_bps", 3.0)),
        ),
        risk=risk_cfg,
        agent=agent_cfg,
    )


def export_strategy_yaml(config: dict[str, Any], strategy_id: str) -> str:
    """Serialize one strategy as person-trading-strategy.yaml text."""
    doc = document_from_qbit_config(config, strategy_id)
    header = "# person-trading-strategy.yaml v0.1 — exported from Qbit\n"
    body = yaml.safe_dump(
        doc.model_dump(exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    return header + body

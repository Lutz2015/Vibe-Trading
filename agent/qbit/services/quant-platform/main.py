from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_module(module_name: str, relative_path: str) -> ModuleType:
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


strategy_module = _load_module("strategy_engine_embedded", "backend/services/strategy-engine/main.py")
risk_module = _load_module("risk_engine_embedded", "backend/services/risk-engine/main.py")
execution_module = _load_module("execution_engine_embedded", "backend/services/execution-engine/main.py")
market_module = _load_module("market_data_embedded", "backend/services/market-data/main.py")
backtest_module = _load_module("backtest_engine_embedded", "backend/services/backtest_engine/main.py")
recon_module = _load_module(
    "reconciliation_engine_embedded",
    "backend/services/reconciliation-engine/main.py",
)
monitoring_module = _load_module(
    "monitoring_engine_embedded",
    "backend/services/monitoring-engine/main.py",
)
registry_module = _load_module(
    "strategy_registry_engine_embedded",
    "backend/services/strategy-registry-engine/main.py",
)
ledger_module = _load_module("portfolio_ledger_embedded", "backend/services/portfolio-ledger/main.py")
orchestrator_module = _load_module(
    "trading_orchestrator_embedded",
    "backend/services/trading-orchestrator/main.py",
)


def _inprocess_strategy_resolver(strategy_id: str) -> tuple[str | None, str | None]:
    try:
        active = registry_module.get_active_version(strategy_id)
        detail = registry_module.get_strategy_version(strategy_id, active.active_version)
        return active.active_version, detail.data_version
    except Exception:
        return None, None


def _inprocess_rollout_resolver(strategy_id: str) -> tuple[str | None, float | None]:
    try:
        rollout = registry_module.get_rollout_config(strategy_id)
        return rollout.mode, rollout.canary_ratio
    except Exception:
        return None, None


def _inprocess_risk_check(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    portfolio_value: float,
) -> strategy_module.RiskCheckResult:
    result = risk_module.check_order(
        risk_module.OrderRiskCheckRequest(
            symbol=symbol,
            side=side,
            quantity=quantity,
            notional=quantity * limit_price,
            portfolio_value=portfolio_value,
        )
    )
    return strategy_module.RiskCheckResult(accepted=result.accepted, reason=result.reason)


def _inprocess_route_order(payload: strategy_module.RoutedOrderPayload) -> strategy_module.RoutedOrderResult:
    result = execution_module.create_routed_order(
        execution_module.RoutedOrderRequest(**payload.model_dump())
    )
    return strategy_module.RoutedOrderResult(
        order_id=result.order.order_id,
        routed_mode=result.routed_mode,
        live_attempted=result.live_attempted,
        live_accepted=result.live_accepted,
        reason=result.reason,
    )


def _inprocess_get_quotes(symbols: list[str]) -> dict[str, Any]:
    normalized = [market_module.normalize_symbol(symbol) for symbol in symbols]
    try:
        quotes = market_module.provider.get_quotes(normalized)
    except Exception:
        fallback = market_module.fallback_provider
        if fallback is None:
            raise
        quotes = fallback.get_quotes(normalized)
    return {quote.symbol: quote for quote in quotes}


def _inprocess_allocate(payload: dict[str, Any]) -> Any:
    return strategy_module.allocate_portfolio(strategy_module.PortfolioAllocateRequest(**payload))


def _inprocess_execute_rebalance(payload: dict[str, Any]) -> Any:
    return strategy_module.rebalance_portfolio(strategy_module.PortfolioRebalanceRequest(**payload))


def _inprocess_ledger_snapshot(prices: dict[str, float]) -> Any:
    return ledger_module.build_snapshot(prices=prices or None)


def _inprocess_ensure_ledger(initial_cash: float) -> None:
    ledger_module.ensure_initialized(initial_cash)


def _inprocess_apply_ledger_fill(payload: dict[str, Any]) -> Any:
    return ledger_module.apply_fill(ledger_module.ApplyFillRequest(**payload))


def _inprocess_fill_order(order_id: str, payload: dict[str, Any]) -> Any:
    return execution_module.fill_order(order_id, execution_module.FillOrderRequest(**payload))


def _inprocess_ingest(payload: dict[str, Any]) -> None:
    monitoring_module.ingest_events(monitoring_module.IngestRequest(**payload))


strategy_module.resolve_active_strategy_version = _inprocess_strategy_resolver
strategy_module.resolve_strategy_rollout_config = _inprocess_rollout_resolver
strategy_module.check_order_risk = _inprocess_risk_check
strategy_module.route_order = _inprocess_route_order

orchestrator_module.wire_dependencies(
    get_quotes=_inprocess_get_quotes,
    allocate_portfolio=_inprocess_allocate,
    execute_rebalance=_inprocess_execute_rebalance,
    get_ledger_snapshot=_inprocess_ledger_snapshot,
    ensure_ledger=_inprocess_ensure_ledger,
    apply_ledger_fill=_inprocess_apply_ledger_fill,
    fill_order=_inprocess_fill_order,
    ingest_monitoring=_inprocess_ingest,
)

app = FastAPI(title="quant-platform", version="0.1.0")

_cors_origins = os.getenv(
    "CORS_ALLOW_ORIGINS",
    "http://127.0.0.1:5173,http://localhost:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _cors_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICE_NAMES = [
    "strategy",
    "risk",
    "execution",
    "market-data",
    "backtest",
    "reconciliation",
    "monitoring",
    "strategy-registry",
    "portfolio-ledger",
    "trading-orchestrator",
]


@app.get("/health")
def health() -> dict[str, Any]:
    auto_enabled = os.getenv("AUTO_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    return {
        "status": "ok",
        "service": "quant-platform",
        "mode": "single-port",
        "mounted_services": SERVICE_NAMES,
        "auto_trading_enabled": auto_enabled,
        "auto_trading_scheduler": orchestrator_module._scheduler_running,
    }


_sub_apps = [
    strategy_module.app,
    risk_module.app,
    execution_module.app,
    market_module.app,
    backtest_module.app,
    recon_module.app,
    monitoring_module.app,
    registry_module.app,
    ledger_module.app,
    orchestrator_module.app,
]

for sub_app in _sub_apps:
    for route in sub_app.routes:
        if isinstance(route, APIRoute) and route.path == "/health":
            continue
        app.routes.append(route)


if os.getenv("AUTO_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
    orchestrator_module.start_scheduler()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)

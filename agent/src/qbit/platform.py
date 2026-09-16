"""Assemble the Qbit quant-platform (10 engines) inside Personal-Trading."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_QBIT_ROOT = Path(__file__).resolve().parents[2] / "qbit"
_SERVICES = _QBIT_ROOT / "services"
_DATA_DIR = Path.home() / ".person-trading" / "qbit"
_qbit_app: FastAPI | None = None


class QuickBacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    start: str = Field(..., description="YYYY-MM-DD")
    end: str = Field(..., description="YYYY-MM-DD")
    short_window: int = Field(default=5, ge=2)
    long_window: int = Field(default=20, ge=3)
    initial_cash: float = Field(default=1_000_000.0, gt=0)


def qbit_root() -> Path:
    return _QBIT_ROOT


def _bootstrap_environment() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        "RISK_LIMITS_PATH": str(_QBIT_ROOT / "configs" / "sim-risk-limits.yaml"),
        "PORTFOLIO_LEDGER_PATH": str(_DATA_DIR / "portfolio-ledger.json"),
        "EXECUTION_LEDGER_PATH": str(_DATA_DIR / "execution-orders-ledger.json"),
        "AUTO_TRADING_CONFIG_PATH": str(_QBIT_ROOT / "configs" / "auto-trading.yaml"),
        "AUTO_TRADING_STATE_PATH": str(_DATA_DIR / "automation-state.json"),
        "VIBE_TRADING_QBIT_STRATEGIES_ROOT": str(_QBIT_ROOT / "strategies"),
        "MARKET_DATA_PROVIDER": os.getenv("MARKET_DATA_PROVIDER", "akshare"),
        "MARKET_DATA_FALLBACK_PROVIDER": os.getenv("MARKET_DATA_FALLBACK_PROVIDER", "sina"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _load_module(module_name: str, relative_path: str) -> ModuleType:
    path = _SERVICES / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load qbit module: {module_name} ({path})")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def create_qbit_app(*, start_scheduler: bool = False) -> FastAPI:
    """Build the in-process Qbit FastAPI app (all 10 engines, single mount)."""
    global _qbit_app
    if _qbit_app is not None:
        return _qbit_app

    _bootstrap_environment()

    strategy_module = _load_module("qbit_strategy_engine", "strategy-engine/main.py")
    risk_module = _load_module("qbit_risk_engine", "risk-engine/main.py")
    execution_module = _load_module("qbit_execution_engine", "execution-engine/main.py")
    market_module = _load_module("qbit_market_data", "market-data/main.py")
    backtest_module = _load_module("qbit_backtest_engine", "backtest_engine/main.py")
    recon_module = _load_module("qbit_reconciliation_engine", "reconciliation-engine/main.py")
    monitoring_module = _load_module("qbit_monitoring_engine", "monitoring-engine/main.py")
    registry_module = _load_module("qbit_strategy_registry", "strategy-registry-engine/main.py")
    ledger_module = _load_module("qbit_portfolio_ledger", "portfolio-ledger/main.py")
    orchestrator_module = _load_module("qbit_trading_orchestrator", "trading-orchestrator/main.py")

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

    app = FastAPI(title="qbit-quant-platform", version="0.1.0", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, Any]:
        auto_enabled = os.getenv("AUTO_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        return {
            "status": "ok",
            "service": "qbit-quant-platform",
            "mode": "embedded",
            "mounted_services": [
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
            ],
            "auto_trading_enabled": auto_enabled,
            "auto_trading_scheduler": orchestrator_module._scheduler_running,
        }

    for sub_app in (
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
    ):
        for route in sub_app.routes:
            if isinstance(route, APIRoute) and route.path == "/health":
                continue
            app.routes.append(route)

    if start_scheduler or os.getenv("AUTO_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        try:
            orchestrator_module.start_scheduler()
            logger.info("Qbit auto-trading scheduler started")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Qbit scheduler failed to start: %s", exc)

    # Convenience: SMA backtest that pulls bars via Personal-Trading market_data.
    @app.post("/backtest/quick")
    def quick_backtest(payload: QuickBacktestRequest) -> dict[str, Any]:
        """Fetch OHLCV via market_data and run the SMA backtest engine."""
        from src.market_data import fetch_market_data

        payload_data = fetch_market_data(
            codes=[payload.symbol],
            start_date=payload.start,
            end_date=payload.end,
            source="auto",
            interval="1D",
            max_rows=5000,
        )
        block = payload_data.get(payload.symbol)
        if isinstance(block, dict):
            rows = block.get("data") or block.get("rows") or []
        elif isinstance(block, list):
            rows = block
        else:
            rows = []
        bars: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            close = row.get("close")
            day = row.get("date") or row.get("trade_date") or row.get("time") or row.get("datetime")
            if close is None or not day:
                continue
            try:
                bars.append({"trade_date": str(day)[:10], "close": float(close)})
            except (TypeError, ValueError):
                continue
        if len(bars) < payload.long_window:
            raise HTTPException(
                status_code=400,
                detail=f"bars too few ({len(bars)}) for long_window={payload.long_window}",
            )
        result = backtest_module.run_backtest(
            backtest_module.BacktestRequest(
                strategy_id=f"quick_sma_{payload.symbol}",
                symbol=payload.symbol,
                bars=[backtest_module.BarInput(**b) for b in bars],
                initial_cash=payload.initial_cash,
                short_window=payload.short_window,
                long_window=payload.long_window,
            )
        )
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)

    _qbit_app = app
    return app


def mount_qbit_routes(app: FastAPI, *, prefix: str = "/qbit") -> None:
    """Mount the assembled Qbit app under ``/qbit`` (avoids route clashes with core API)."""
    qbit_app = create_qbit_app()
    app.mount(prefix, qbit_app)
    logger.info("Qbit quant platform mounted at %s", prefix)

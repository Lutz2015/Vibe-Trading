from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


def _load_module(module_name: str, relative_path: str) -> Any:
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_risk_reject_when_trading_disabled() -> None:
    risk = _load_module("risk_engine_main_for_test", "qbit/services/risk-engine/main.py")

    def fake_limits() -> dict[str, Any]:
        return {
            "global": {"trading_enabled": False, "kill_switch": False},
            "position": {"max_single_symbol_weight": 0.1},
            "order": {},
        }

    risk.load_risk_limits = fake_limits
    resp = risk.check_order(
        risk.OrderRiskCheckRequest(
            symbol="600519.SH",
            side="BUY",
            quantity=100,
            notional=150000,
            portfolio_value=1200000,
        )
    )
    assert resp.accepted is False
    assert resp.reason == "trading_disabled_or_killed"


def test_execution_idempotent_client_order_id() -> None:
    execution = _load_module("execution_engine_main_for_test", "qbit/services/execution-engine/main.py")
    execution._orders_by_id.clear()
    execution._orders_by_client_id.clear()

    req = execution.CreateOrderRequest(
        symbol="000001.SZ",
        side="BUY",
        quantity=100,
        limit_price=10.0,
        strategy_id="integration_p0",
        client_order_id="idem_cli_001",
        trace_id="trace_idem_001",
    )
    first = execution.create_order(req)
    second = execution.create_order(req)
    assert first.order_id == second.order_id
    assert len(execution._orders_by_id) == 1


def test_market_quotes_fallback_when_primary_fails() -> None:
    market = _load_module("market_data_main_for_test", "qbit/services/market-data/main.py")

    class FailingProvider:
        name = "primary_fail"

        def get_quotes(self, symbols: list[str]) -> list[Any]:
            raise RuntimeError(f"primary unavailable for {symbols}")

        def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Any]:
            raise NotImplementedError

    class OkFallbackProvider:
        name = "fallback_ok"

        def get_quotes(self, symbols: list[str]) -> list[Any]:
            return [
                market.Quote(
                    symbol="000001.SZ",
                    name="PingAn",
                    open=10.0,
                    high=10.2,
                    low=9.9,
                    price=10.1,
                    prev_close=9.8,
                    volume=1000000.0,
                    amount=10000000.0,
                    ts="2026-01-01 15:00:00",
                    source=self.name,
                )
            ]

        def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Any]:
            raise NotImplementedError

    original_provider = market.provider
    original_fallback = market.fallback_provider
    try:
        market.provider = FailingProvider()
        market.fallback_provider = OkFallbackProvider()
        quotes = market.market_quotes("000001.SZ")
    finally:
        market.provider = original_provider
        market.fallback_provider = original_fallback

    assert len(quotes) == 1
    assert quotes[0].source == "fallback_ok"

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_strategy_module(module_name: str):
    path = Path(__file__).resolve().parents[2] / "qbit" / "services" / "strategy-engine" / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load strategy engine module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_generate_signals_with_active_version_metadata() -> None:
    module = _load_strategy_module("strategy_engine_test_active")

    def fake_resolve(strategy_id: str) -> tuple[str | None, str | None]:
        assert strategy_id == "multifactor"
        return "v2", "jq_2026q2"

    def fake_rollout(strategy_id: str) -> tuple[str | None, float | None]:
        assert strategy_id == "multifactor"
        return "canary", 0.2

    module.resolve_active_strategy_version = fake_resolve
    module.resolve_strategy_rollout_config = fake_rollout

    resp = module.generate_signals(
        module.SignalRequest(
            strategy_id="multifactor",
            symbols=["000001.SZ", "600519.SH"],
        )
    )
    assert resp.strategy_version == "v2"
    assert resp.data_version == "jq_2026q2"
    assert resp.rollout_mode == "canary"
    assert resp.canary_ratio == 0.2
    assert len(resp.signals) == 2
    assert all(item.action == "HOLD" for item in resp.signals)


def test_generate_signals_registry_unavailable_fallback() -> None:
    module = _load_strategy_module("strategy_engine_test_fallback")

    def fake_resolve(strategy_id: str) -> tuple[str | None, str | None]:
        del strategy_id
        return None, None

    def fake_rollout(strategy_id: str) -> tuple[str | None, float | None]:
        del strategy_id
        return None, None

    module.resolve_active_strategy_version = fake_resolve
    module.resolve_strategy_rollout_config = fake_rollout

    resp = module.generate_signals(
        module.SignalRequest(
            strategy_id="unknown_strategy",
            symbols=["000001.SZ"],
        )
    )
    assert resp.strategy_version is None
    assert resp.data_version is None
    assert resp.rollout_mode is None
    assert resp.canary_ratio is None
    assert len(resp.signals) == 1
    assert resp.signals[0].action == "HOLD"


def test_portfolio_allocate_prefers_higher_score() -> None:
    module = _load_strategy_module("strategy_engine_test_allocate")
    resp = module.allocate_portfolio(
        module.PortfolioAllocateRequest(
            items=[
                module.StrategyAllocationInput(
                    strategy_id="alpha", expected_return_pct=18.0, risk_volatility_pct=10.0
                ),
                module.StrategyAllocationInput(
                    strategy_id="beta", expected_return_pct=9.0, risk_volatility_pct=12.0
                ),
            ],
            total_exposure=1.0,
            max_single_weight=0.8,
            risk_aversion=1.0,
        )
    )
    assert len(resp.weights) == 2
    weight_map = {item.strategy_id: item.weight for item in resp.weights}
    assert weight_map["alpha"] > weight_map["beta"]
    assert resp.total_weight <= 1.0


def test_portfolio_allocate_respects_max_single_weight() -> None:
    module = _load_strategy_module("strategy_engine_test_allocate_cap")
    resp = module.allocate_portfolio(
        module.PortfolioAllocateRequest(
            items=[
                module.StrategyAllocationInput(
                    strategy_id="only_one", expected_return_pct=20.0, risk_volatility_pct=1.0
                )
            ],
            total_exposure=1.0,
            max_single_weight=0.4,
        )
    )
    assert resp.weights[0].weight == 0.4
    assert resp.unallocated_weight == 0.6


def test_portfolio_rebalance_generates_buy_and_sell_orders() -> None:
    module = _load_strategy_module("strategy_engine_test_rebalance")

    routed_orders: list[module.RoutedOrderPayload] = []

    def fake_risk(symbol: str, side: str, quantity: int, limit_price: float, portfolio_value: float):
        del side, quantity, limit_price, portfolio_value
        return module.RiskCheckResult(accepted=True, reason="ok")

    def fake_route(payload: module.RoutedOrderPayload):
        routed_orders.append(payload)
        return module.RoutedOrderResult(
            order_id=f"ord_{payload.symbol}",
            routed_mode="paper",
            live_attempted=False,
            live_accepted=False,
            reason="paper_mode",
        )

    module.check_order_risk = fake_risk
    module.route_order = fake_route

    resp = module.rebalance_portfolio(
        module.PortfolioRebalanceRequest(
            weights=[
                module.StrategyWeight(strategy_id="alpha", weight=0.6, score=1.0),
                module.StrategyWeight(strategy_id="beta", weight=0.4, score=0.5),
            ],
            strategy_targets=[
                module.StrategyTargetInput(
                    strategy_id="alpha",
                    symbols=[
                        module.StrategySymbolTarget(symbol="000001.SZ", weight=1.0),
                    ],
                ),
                module.StrategyTargetInput(
                    strategy_id="beta",
                    symbols=[
                        module.StrategySymbolTarget(symbol="600519.SH", weight=1.0),
                    ],
                ),
            ],
            portfolio_value=1_000_000.0,
            prices={"000001.SZ": 10.0, "600519.SH": 1500.0},
            current_positions=[
                module.PositionInput(symbol="000001.SZ", quantity=200),
                module.PositionInput(symbol="600519.SH", quantity=300),
            ],
            execution_mode="paper",
            min_trade_lot=100,
            trace_id="trace_rebalance_001",
            rebalance_id="rb_001",
        )
    )

    assert resp.target_positions["000001.SZ"] == 60000
    assert resp.target_positions["600519.SH"] == 200
    assert len(routed_orders) == 2
    sides = {item.symbol: item.side for item in routed_orders}
    assert sides["000001.SZ"] == "BUY"
    assert sides["600519.SH"] == "SELL"
    assert all(not item.skipped for item in resp.orders)


def test_portfolio_rebalance_skips_risk_rejected_orders() -> None:
    module = _load_strategy_module("strategy_engine_test_rebalance_risk")

    def fake_risk(symbol: str, side: str, quantity: int, limit_price: float, portfolio_value: float):
        del side, quantity, limit_price, portfolio_value
        if symbol == "000001.SZ":
            return module.RiskCheckResult(accepted=False, reason="single_symbol_weight_exceeded")
        return module.RiskCheckResult(accepted=True, reason="ok")

    def fake_route(payload: module.RoutedOrderPayload):
        return module.RoutedOrderResult(
            order_id=f"ord_{payload.symbol}",
            routed_mode="paper",
            live_attempted=False,
            live_accepted=False,
            reason="paper_mode",
        )

    module.check_order_risk = fake_risk
    module.route_order = fake_route

    resp = module.rebalance_portfolio(
        module.PortfolioRebalanceRequest(
            allocation=module.PortfolioAllocateRequest(
                items=[
                    module.StrategyAllocationInput(
                        strategy_id="alpha", expected_return_pct=10.0, risk_volatility_pct=1.0
                    )
                ],
                total_exposure=1.0,
            ),
            strategy_targets=[
                module.StrategyTargetInput(
                    strategy_id="alpha",
                    symbols=[module.StrategySymbolTarget(symbol="000001.SZ", weight=1.0)],
                )
            ],
            portfolio_value=1_000_000.0,
            prices={"000001.SZ": 10.0},
            current_positions=[],
            trace_id="trace_rebalance_002",
            rebalance_id="rb_002",
        )
    )

    assert len(resp.orders) == 1
    assert resp.orders[0].skipped is True
    assert "risk_rejected" in resp.orders[0].skip_reason


def test_portfolio_rebalance_request_validation() -> None:
    module = _load_strategy_module("strategy_engine_test_rebalance_validation")
    try:
        module.PortfolioRebalanceRequest(
            strategy_targets=[
                module.StrategyTargetInput(
                    strategy_id="alpha",
                    symbols=[module.StrategySymbolTarget(symbol="000001.SZ", weight=1.0)],
                )
            ],
            portfolio_value=1_000_000.0,
            prices={"000001.SZ": 10.0},
            trace_id="trace_rebalance_003",
            rebalance_id="rb_003",
        )
    except ValueError as exc:
        assert "allocation or weights is required" in str(exc)
    else:
        raise AssertionError("expected validation error")

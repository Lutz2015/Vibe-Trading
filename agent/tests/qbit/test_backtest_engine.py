from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
import sys


def _load_backtest_module(module_name: str = "backtest_engine_main"):
    path = (
        Path(__file__).resolve().parents[2] / "qbit" / "services" / "backtest_engine" / "main.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load backtest engine module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_backtest_runs_and_closes_positions() -> None:
    module = _load_backtest_module("backtest_engine_test1")
    BacktestRequest = module.BacktestRequest
    run_backtest = module.run_backtest

    bars = []
    for day in range(1, 31):
        trade_date = f"2026-01-{day:02d}"
        close = 10.0 + (day * 0.2)
        bars.append({"trade_date": trade_date, "close": close})

    payload = BacktestRequest(
        strategy_id="sma_cross_v1",
        symbol="000001.SZ",
        bars=bars,
        initial_cash=100_000.0,
        fee_bps=1.0,
        short_window=3,
        long_window=7,
    )

    result = run_backtest(payload)

    assert result.strategy_id == "sma_cross_v1"
    assert result.symbol == "000001.SZ"
    assert result.metrics.trade_count >= 2
    assert result.final_equity > 0
    assert all(trade.position_after >= 0 for trade in result.trades)
    assert result.trades[-1].side == "SELL"


def test_backtest_rejects_invalid_windows() -> None:
    module = _load_backtest_module("backtest_engine_test2")
    BacktestRequest = module.BacktestRequest

    bars = [{"trade_date": f"2026-02-{day:02d}", "close": 10.0 + day} for day in range(1, 8)]
    try:
        BacktestRequest(
            strategy_id="bad_windows",
            symbol="600519.SH",
            bars=bars,
            short_window=5,
            long_window=5,
        )
    except ValueError as exc:
        assert "short_window must be less than long_window" in str(exc)
    else:
        raise AssertionError("Expected validation error for invalid windows.")


def test_backtest_signal_executes_on_next_bar() -> None:
    module = _load_backtest_module("backtest_engine_test3")
    BacktestRequest = module.BacktestRequest
    run_backtest = module.run_backtest

    bars = [
        {"trade_date": "2026-03-01", "close": 10.0},
        {"trade_date": "2026-03-02", "close": 10.0},
        {"trade_date": "2026-03-03", "close": 10.0},
        {"trade_date": "2026-03-04", "close": 20.0},  # signal generated here
        {"trade_date": "2026-03-05", "close": 20.0},  # buy should happen here
    ]
    payload = BacktestRequest(
        strategy_id="sma_cross_v1",
        symbol="000001.SZ",
        bars=bars,
        initial_cash=10_000.0,
        fee_bps=0.0,
        slippage_bps=0.0,
        short_window=2,
        long_window=3,
        execution_delay_bars=1,
    )
    result = run_backtest(payload)
    assert len(result.trades) >= 1
    assert result.trades[0].side == "BUY"
    assert result.trades[0].trade_date == "2026-03-05"
    assert result.trades[0].price == 20.0


def test_backtest_slippage_reduces_equity() -> None:
    module = _load_backtest_module("backtest_engine_test4")
    BacktestRequest = module.BacktestRequest
    run_backtest = module.run_backtest

    bars = []
    for day in range(1, 31):
        bars.append({"trade_date": f"2026-04-{day:02d}", "close": 10.0 + (day * 0.1)})

    no_slippage = run_backtest(
        BacktestRequest(
            strategy_id="sma_cross_v1",
            symbol="000001.SZ",
            bars=bars,
            initial_cash=100_000.0,
            fee_bps=1.0,
            slippage_bps=0.0,
            short_window=3,
            long_window=7,
            execution_delay_bars=1,
        )
    )
    with_slippage = run_backtest(
        BacktestRequest(
            strategy_id="sma_cross_v1",
            symbol="000001.SZ",
            bars=bars,
            initial_cash=100_000.0,
            fee_bps=1.0,
            slippage_bps=20.0,
            short_window=3,
            long_window=7,
            execution_delay_bars=1,
        )
    )
    assert with_slippage.final_equity < no_slippage.final_equity


def test_multifactor_backtest_runs_with_rebalance() -> None:
    module = _load_backtest_module("backtest_engine_test5")
    RequestModel = module.MultiFactorBacktestRequest
    run_multifactor_backtest = module.run_multifactor_backtest

    rows = []
    dates = ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    closes = {
        "000001.SZ": [10.0, 10.4, 10.8, 11.0],
        "000002.SZ": [10.0, 9.8, 9.7, 9.6],
        "000003.SZ": [10.0, 10.1, 10.2, 10.3],
    }
    factors = {
        "000001.SZ": {"market_cap": 100.0, "roe": 18.0},
        "000002.SZ": {"market_cap": 300.0, "roe": 5.0},
        "000003.SZ": {"market_cap": 200.0, "roe": 12.0},
    }
    for i, trade_date in enumerate(dates):
        for symbol in symbols:
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "close": closes[symbol][i],
                    "factors": factors[symbol],
                }
            )

    result = run_multifactor_backtest(
        RequestModel(
            strategy_id="multifactor_v1",
            rows=rows,
            factor_directions={"market_cap": 1, "roe": -1},
            factor_weights={"market_cap": 1.0, "roe": 1.0},
            rebalance_every=2,
            top_n=1,
            initial_cash=200_000.0,
            fee_bps=1.0,
            slippage_bps=0.0,
            execution_delay_bars=1,
            min_trade_lot=100,
        )
    )

    assert result.strategy_id == "multifactor_v1"
    assert len(result.rebalance_dates) >= 1
    assert result.metrics.trade_count >= 2
    assert result.final_equity > 0
    assert any(trade.side == "BUY" for trade in result.trades)
    assert result.trades[0].trade_date == "2026-05-02"


def test_multifactor_backtest_rejects_invalid_factor_direction() -> None:
    module = _load_backtest_module("backtest_engine_test6")
    RequestModel = module.MultiFactorBacktestRequest

    rows = [
        {
            "trade_date": "2026-06-01",
            "symbol": "000001.SZ",
            "close": 10.0,
            "factors": {"market_cap": 100.0},
        },
        {
            "trade_date": "2026-06-02",
            "symbol": "000001.SZ",
            "close": 10.1,
            "factors": {"market_cap": 100.0},
        },
    ]
    try:
        RequestModel(
            strategy_id="multifactor_v1",
            rows=rows,
            factor_directions={"market_cap": 0},
        )
    except ValueError as exc:
        assert "direction must be -1 or 1" in str(exc)
    else:
        raise AssertionError("Expected invalid factor direction to fail.")


def test_joinquant_request_rejects_factor_mismatch() -> None:
    module = _load_backtest_module("backtest_engine_test7")
    RequestModel = module.JoinquantMultiFactorBacktestRequest

    try:
        RequestModel(
            start_date="2026-01-01",
            end_date="2026-02-01",
            factors=["market_cap", "roe"],
            factor_directions={"market_cap": 1},
        )
    except ValueError as exc:
        assert "must match factors" in str(exc)
    else:
        raise AssertionError("Expected factor mismatch to fail.")


def test_joinquant_symbol_conversion() -> None:
    module = _load_backtest_module("backtest_engine_test8")
    assert module._jq_to_std_symbol("600519.XSHG") == "600519.SH"
    assert module._jq_to_std_symbol("000001.XSHE") == "000001.SZ"


def test_joinquant_template_request_validation() -> None:
    module = _load_backtest_module("backtest_engine_test9")
    RequestModel = module.JoinquantTemplateBacktestRequest
    try:
        RequestModel(
            template_id="unknown_template",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    except ValueError as exc:
        assert "template_id must be test1_multifactor or test2_futures_spread" in str(exc)
    else:
        raise AssertionError("Expected invalid template id to fail.")


def test_future_symbol_helpers() -> None:
    module = _load_backtest_module("backtest_engine_test10")
    current = module._get_current_month_future_symbol(date(2026, 5, 10), "IF")
    nxt = module._get_next_month_future_symbol(date(2026, 5, 10), "IF")
    assert current.endswith(".CCFX")
    assert nxt.endswith(".CCFX")
    assert current != nxt


def test_portfolio_template_request_validation() -> None:
    module = _load_backtest_module("backtest_engine_test11")
    RequestModel = module.PortfolioTemplateBacktestRequest
    try:
        RequestModel(
            start_date="2026-12-31",
            end_date="2026-01-01",
            items=[
                module.PortfolioTemplateItem(
                    strategy_id="s1",
                    template_id="test1_multifactor",
                    expected_return_pct=10.0,
                    risk_volatility_pct=5.0,
                )
            ],
        )
    except ValueError as exc:
        assert "start_date must be <=" in str(exc)
    else:
        raise AssertionError("Expected invalid date range to fail.")


def test_portfolio_template_response_shape_with_stub() -> None:
    module = _load_backtest_module("backtest_engine_test12")

    def fake_multifactor(_: object) -> object:
        return module.MultiFactorBacktestResponse(
            strategy_id="test1_multifactor",
            initial_cash=1_000_000.0,
            final_equity=1_100_000.0,
            metrics=module.BacktestMetrics(
                total_return_pct=10.0,
                max_drawdown_pct=5.0,
                annualized_volatility_pct=12.0,
                sharpe_like=1.0,
                win_rate_pct=50.0,
                trade_count=10,
            ),
            rebalance_dates=["2026-01-01"],
            holdings={},
            trades=[],
        )

    module.run_joinquant_test2_futures_spread = fake_multifactor
    module.build_joinquant_multifactor_rows = lambda _: [
        module.MultiFactorRowInput(
            trade_date="2026-01-01",
            symbol="000001.SZ",
            close=10.0,
            factors={"market_cap": 100.0, "roe": 10.0},
        ),
        module.MultiFactorRowInput(
            trade_date="2026-01-02",
            symbol="000001.SZ",
            close=10.1,
            factors={"market_cap": 100.0, "roe": 10.0},
        ),
    ]
    module.run_multifactor_backtest = fake_multifactor

    req = module.PortfolioTemplateBacktestRequest(
        start_date="2026-01-01",
        end_date="2026-12-31",
        initial_cash=2_000_000.0,
        items=[
            module.PortfolioTemplateItem(
                strategy_id="s1",
                template_id="test1_multifactor",
                expected_return_pct=12.0,
                risk_volatility_pct=4.0,
            ),
            module.PortfolioTemplateItem(
                strategy_id="s2",
                template_id="test2_futures_spread",
                expected_return_pct=8.0,
                risk_volatility_pct=3.0,
            ),
        ],
    )
    res = module.run_joinquant_template_portfolio(req)
    assert res.initial_cash == 2_000_000.0
    assert len(res.components) == 2
    assert res.final_equity > 0

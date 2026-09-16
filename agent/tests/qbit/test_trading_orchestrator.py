from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import yaml


def _load_module(module_name: str):
    path = Path(__file__).resolve().parents[2] / "qbit" / "services" / "trading-orchestrator" / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load trading orchestrator module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(tmpdir: Path) -> Path:
    config = {
        "enabled": True,
        "mode": "paper",
        "execution_mode": "paper",
        "poll_interval_sec": 30,
        "trading_hours": {"start": "09:30", "end": "15:00"},
        "rebalance": {
            "time_local": "14:50",
            "window_minutes": 60,
            "rebalance_every_trading_days": 15,
        },
        "portfolio": {
            "initial_cash": 1000000,
            "total_exposure": 1.0,
            "max_single_weight": 0.5,
            "risk_aversion": 1.0,
            "min_trade_lot": 100,
            "fee_bps": 3.0,
        },
        "strategies": [
            {
                "strategy_id": "test1_multifactor",
                "enabled": True,
                "expected_return_pct": 18.0,
                "risk_volatility_pct": 12.0,
                "max_weight": 0.7,
                "params": {
                    "top_n": 2,
                    "universe_symbols": ["000001.SZ", "600519.SH", "000858.SZ"],
                },
            }
        ],
    }
    config_path = tmpdir / "auto-trading.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_should_rebalance_after_interval() -> None:
    module = _load_module("trading_orchestrator_test_interval")
    config = {"rebalance": {"rebalance_every_trading_days": 2}}
    state = {"last_rebalance_trading_day": "2026-05-20"}
    assert module.should_rebalance_today(config, state, force=False) is True


def test_build_strategy_targets_picks_top_momentum() -> None:
    module = _load_module("trading_orchestrator_test_targets")

    def fake_quotes(symbols: list[str]) -> dict[str, SimpleNamespace]:
        data = {
            "000001.SZ": SimpleNamespace(price=10.5, prev_close=10.0),
            "600519.SH": SimpleNamespace(price=1500.0, prev_close=1600.0),
            "000858.SZ": SimpleNamespace(price=120.0, prev_close=100.0),
        }
        return {symbol: data[symbol] for symbol in symbols}

    targets = module.build_strategy_targets(
        {
            "strategy_id": "test1_multifactor",
            "params": {"top_n": 2, "universe_symbols": ["000001.SZ", "600519.SH", "000858.SZ"]},
        },
        fake_quotes,
    )
    symbols = [item["symbol"] for item in targets]
    assert symbols[0] == "000858.SZ"
    assert len(symbols) == 2


def test_run_cycle_executes_paper_rebalance_with_stubs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        config_path = _write_config(tmp)
        state_path = tmp / "state.json"
        ledger_path = tmp / "portfolio.json"
        order_path = tmp / "orders.json"

        module = _load_module("trading_orchestrator_test_run_cycle")
        module._state_path = state_path

        ledger_spec = importlib.util.spec_from_file_location(
            "portfolio_ledger_embedded_test",
            Path(__file__).resolve().parents[2] / "qbit" / "services" / "portfolio-ledger" / "main.py",
        )
        assert ledger_spec and ledger_spec.loader
        ledger = importlib.util.module_from_spec(ledger_spec)
        sys.modules["portfolio_ledger_embedded_test"] = ledger
        ledger_spec.loader.exec_module(ledger)

        execution_spec = importlib.util.spec_from_file_location(
            "execution_embedded_test",
            Path(__file__).resolve().parents[2] / "qbit" / "services" / "execution-engine" / "main.py",
        )
        assert execution_spec and execution_spec.loader
        execution = importlib.util.module_from_spec(execution_spec)
        sys.modules["execution_embedded_test"] = execution
        os.environ["EXECUTION_LEDGER_PATH"] = str(order_path)
        execution_spec.loader.exec_module(execution)
        execution._orders_by_id.clear()
        execution._orders_by_client_id.clear()

        ledger._ledger_path = ledger_path
        ledger.reset_ledger(1_000_000.0)

        routed: list[dict[str, str]] = []

        def fake_quotes(symbols: list[str]) -> dict[str, SimpleNamespace]:
            return {
                symbol: SimpleNamespace(
                    symbol=symbol,
                    price={"000001.SZ": 10.0, "600519.SH": 1500.0, "000858.SZ": 120.0}.get(symbol, 10.0),
                    prev_close=10.0,
                )
                for symbol in symbols
            }

        class FakeOrder:
            def __init__(self, symbol: str, side: str, quantity: int, limit_price: float, order_id: str):
                self.symbol = symbol
                self.side = side
                self.quantity = quantity
                self.limit_price = limit_price
                self.order_id = order_id
                self.skipped = False

        class FakeRebalance:
            def __init__(self, orders: list[FakeOrder]):
                self.orders = orders

        def fake_allocate(payload: dict) -> SimpleNamespace:
            del payload
            return SimpleNamespace(weights=[])

        def fake_rebalance(payload: dict) -> FakeRebalance:
            routed.append({"rebalance_id": payload["rebalance_id"]})
            order = execution.create_order(
                execution.CreateOrderRequest(
                    symbol="000858.SZ",
                    side="BUY",
                    quantity=100,
                    limit_price=120.0,
                    strategy_id="test1_multifactor",
                    client_order_id=f"{payload['rebalance_id']}:000858.SZ:BUY",
                    trace_id=payload["trace_id"],
                )
            )
            return FakeRebalance([FakeOrder("000858.SZ", "BUY", 100, 120.0, order.order_id)])

        module.wire_dependencies(
            get_quotes=fake_quotes,
            allocate_portfolio=fake_allocate,
            execute_rebalance=fake_rebalance,
            get_ledger_snapshot=lambda prices: ledger.build_snapshot(prices=prices),
            ensure_ledger=ledger.ensure_initialized,
            apply_ledger_fill=lambda payload: ledger.apply_fill(ledger.ApplyFillRequest(**payload)),
            fill_order=lambda order_id, payload: execution.fill_order(
                order_id, execution.FillOrderRequest(**payload)
            ),
        )

        original_load_config = module.load_config
        module.load_config = lambda: yaml.safe_load(config_path.read_text(encoding="utf-8"))

        tz = ZoneInfo("Asia/Shanghai")
        original_now = module._now_local
        module._now_local = lambda: datetime(2026, 5, 27, 14, 55, tzinfo=tz)

        result = module.run_cycle(force=True, rebalance=True)
        assert result.skipped is False
        assert result.orders_submitted == 1
        assert result.orders_filled == 1
        assert routed
        snapshot = ledger.build_snapshot(prices={"000858.SZ": 120.0})
        assert any(pos.symbol == "000858.SZ" and pos.quantity == 100 for pos in snapshot.positions)

        module.load_config = original_load_config
        module._now_local = original_now
        os.environ.pop("EXECUTION_LEDGER_PATH", None)

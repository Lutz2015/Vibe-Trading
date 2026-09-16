from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile

from fastapi import HTTPException


def _load_execution_module(module_name: str = "execution_engine_main"):
    path = (
        Path(__file__).resolve().parents[2] / "qbit" / "services" / "execution-engine" / "main.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load execution engine module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_fill_transition_to_partial_and_filled() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["EXECUTION_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        module = _load_execution_module("execution_engine_main_fill")
        module._orders_by_id.clear()
        module._orders_by_client_id.clear()

        created = module.create_order(
            module.CreateOrderRequest(
                symbol="000001.SZ",
                side="BUY",
                quantity=100,
                limit_price=10.0,
                strategy_id="sma_cross_v1",
                client_order_id="cli_001",
                trace_id="trace_001",
            )
        )

        partial = module.fill_order(
            created.order_id, module.FillOrderRequest(fill_quantity=40, fill_price=10.0)
        )
        assert partial.status == "PARTIAL"
        assert partial.filled_quantity == 40

        filled = module.fill_order(
            created.order_id, module.FillOrderRequest(fill_quantity=60, fill_price=10.2)
        )
        assert filled.status == "FILLED"
        assert filled.filled_quantity == 100
        assert filled.avg_fill_price > 0
        assert filled.status_history == ["ACCEPTED", "PARTIAL", "FILLED"]
    os.environ.pop("EXECUTION_LEDGER_PATH", None)


def test_terminal_order_rejects_new_transition() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["EXECUTION_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        module = _load_execution_module("execution_engine_main_terminal")
        module._orders_by_id.clear()
        module._orders_by_client_id.clear()

        created = module.create_order(
            module.CreateOrderRequest(
                symbol="600519.SH",
                side="BUY",
                quantity=100,
                limit_price=1500.0,
                strategy_id="sma_cross_v1",
                client_order_id="cli_002",
                trace_id="trace_002",
            )
        )
        module.cancel_order(created.order_id, module.CancelOrderRequest())

        try:
            module.fill_order(
                created.order_id, module.FillOrderRequest(fill_quantity=10, fill_price=1500.0)
            )
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "terminal_status" in str(exc.detail)
        else:
            raise AssertionError("Expected terminal status transition to fail")
    os.environ.pop("EXECUTION_LEDGER_PATH", None)


def test_ledger_persist_and_reload() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "execution-orders-ledger.json"
        os.environ["EXECUTION_LEDGER_PATH"] = str(ledger_path)

        module_a = _load_execution_module("execution_engine_main_persist_a")
        module_a._orders_by_id.clear()
        module_a._orders_by_client_id.clear()
        module_a.persist_ledger()

        created = module_a.create_order(
            module_a.CreateOrderRequest(
                symbol="000001.SZ",
                side="BUY",
                quantity=100,
                limit_price=10.0,
                strategy_id="persist_v1",
                client_order_id="cli_persist_001",
                trace_id="trace_persist_001",
            )
        )
        assert ledger_path.exists()

        module_b = _load_execution_module("execution_engine_main_persist_b")
        recovered = module_b.get_order(created.order_id)
        assert recovered.order_id == created.order_id
        assert module_b._orders_by_client_id["cli_persist_001"] == created.order_id
    os.environ.pop("EXECUTION_LEDGER_PATH", None)


def test_routed_order_live_disabled_falls_back_to_paper() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["EXECUTION_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        os.environ["EXECUTION_LIVE_ENABLED"] = "false"
        module = _load_execution_module("execution_engine_main_route_disabled")
        module._orders_by_id.clear()
        module._orders_by_client_id.clear()

        routed = module.create_routed_order(
            module.RoutedOrderRequest(
                symbol="000001.SZ",
                side="BUY",
                quantity=100,
                limit_price=10.0,
                strategy_id="route_v1",
                client_order_id="cli_route_001",
                trace_id="trace_route_001",
                mode="live",
            )
        )
        assert routed.routed_mode == "paper"
        assert routed.live_attempted is False
        assert routed.reason == "live_mode_disabled"
    os.environ.pop("EXECUTION_LEDGER_PATH", None)
    os.environ.pop("EXECUTION_LIVE_ENABLED", None)


def test_routed_order_paper_mode() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["EXECUTION_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        module = _load_execution_module("execution_engine_main_route_paper")
        module._orders_by_id.clear()
        module._orders_by_client_id.clear()

        routed = module.create_routed_order(
            module.RoutedOrderRequest(
                symbol="600519.SH",
                side="BUY",
                quantity=100,
                limit_price=1500.0,
                strategy_id="route_v1",
                client_order_id="cli_route_002",
                trace_id="trace_route_002",
                mode="paper",
            )
        )
        assert routed.routed_mode == "paper"
        assert routed.live_attempted is False
        assert routed.order.status == "ACCEPTED"
    os.environ.pop("EXECUTION_LEDGER_PATH", None)


def test_routed_order_live_with_sim_broker_adapter() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["EXECUTION_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        os.environ["EXECUTION_LIVE_ENABLED"] = "true"
        os.environ["EXECUTION_LIVE_CANARY_RATIO"] = "1.0"
        os.environ["BROKER_ADAPTER"] = "sim_broker"
        module = _load_execution_module("execution_engine_main_route_sim_broker")
        module._orders_by_id.clear()
        module._orders_by_client_id.clear()

        routed = module.create_routed_order(
            module.RoutedOrderRequest(
                symbol="000001.SZ",
                side="BUY",
                quantity=100,
                limit_price=10.0,
                strategy_id="route_v1",
                client_order_id="cli_route_003",
                trace_id="trace_route_003",
                mode="live",
            )
        )
        assert routed.live_attempted is True
        assert routed.live_accepted is True
        assert routed.routed_mode == "live"
    os.environ.pop("EXECUTION_LEDGER_PATH", None)
    os.environ.pop("EXECUTION_LIVE_ENABLED", None)
    os.environ.pop("EXECUTION_LIVE_CANARY_RATIO", None)
    os.environ.pop("BROKER_ADAPTER", None)

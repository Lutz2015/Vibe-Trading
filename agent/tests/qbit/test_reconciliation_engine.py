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


def test_reconcile_consistent_ledgers() -> None:
    reconcile = _load_module("reconciliation_engine_main_for_test", "qbit/services/reconciliation-engine/main.py")
    payload = reconcile.ReconcileRequest(
        orders=[
            reconcile.OrderLedgerItem(
                order_id="ord_001",
                symbol="000001.SZ",
                side="BUY",
                quantity=100,
                filled_quantity=100,
                avg_fill_price=10.0,
                filled_notional=1000.0,
                status="FILLED",
            )
        ],
        trades=[
            reconcile.TradeLedgerItem(symbol="000001.SZ", side="BUY", quantity=100, price=10.0, fee=1.0)
        ],
        positions=[reconcile.PositionLedgerItem(symbol="000001.SZ", quantity=100)],
        cash=reconcile.CashLedgerSnapshot(initial_cash=10_000.0, current_cash=8_999.0),
        tolerance=0.01,
    )

    result = reconcile.run_reconciliation(payload)
    assert result.consistent is True
    assert result.checks_failed == []
    assert "cash_consistency" in result.checks_passed


def test_reconcile_detects_mismatches() -> None:
    reconcile = _load_module("reconciliation_engine_main_for_test_mismatch", "qbit/services/reconciliation-engine/main.py")
    payload = reconcile.ReconcileRequest(
        orders=[
            reconcile.OrderLedgerItem(
                order_id="ord_002",
                symbol="600519.SH",
                side="BUY",
                quantity=100,
                filled_quantity=100,
                avg_fill_price=15.0,
                filled_notional=1500.0,
                status="FILLED",
            )
        ],
        trades=[
            reconcile.TradeLedgerItem(symbol="600519.SH", side="BUY", quantity=100, price=10.0, fee=1.0)
        ],
        positions=[reconcile.PositionLedgerItem(symbol="600519.SH", quantity=50)],
        cash=reconcile.CashLedgerSnapshot(initial_cash=10_000.0, current_cash=9_500.0),
        tolerance=0.01,
    )

    result = reconcile.run_reconciliation(payload)
    assert result.consistent is False
    assert any("orders_vs_trades_notional" in item for item in result.checks_failed)
    assert any("trades_vs_positions_quantity" in item for item in result.checks_failed)

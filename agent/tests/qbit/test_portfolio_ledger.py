from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile


def _load_module(module_name: str):
    path = Path(__file__).resolve().parents[2] / "qbit" / "services" / "portfolio-ledger" / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load portfolio ledger module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_ledger_buy_and_sell_updates_positions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["PORTFOLIO_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        module = _load_module("portfolio_ledger_test_flow")
        module.reset_ledger(1_000_000.0)

        after_buy = module.apply_fill(
            module.ApplyFillRequest(symbol="000001.SZ", side="BUY", quantity=1000, price=10.0, fee=3.0)
        )
        assert after_buy.cash < 1_000_000.0
        assert any(pos.symbol == "000001.SZ" and pos.quantity == 1000 for pos in after_buy.positions)

        after_sell = module.apply_fill(
            module.ApplyFillRequest(symbol="000001.SZ", side="SELL", quantity=500, price=10.5, fee=1.5)
        )
        assert any(pos.symbol == "000001.SZ" and pos.quantity == 500 for pos in after_sell.positions)
    os.environ.pop("PORTFOLIO_LEDGER_PATH", None)


def test_ledger_snapshot_marks_to_market() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["PORTFOLIO_LEDGER_PATH"] = str(Path(tmpdir) / "ledger.json")
        module = _load_module("portfolio_ledger_test_mtm")
        module.reset_ledger(200_000.0)
        module.apply_fill(module.ApplyFillRequest(symbol="600519.SH", side="BUY", quantity=100, price=1500.0))
        snapshot = module.build_snapshot(prices={"600519.SH": 1600.0})
        assert snapshot.position_value == 160_000.0
        assert snapshot.portfolio_value == snapshot.cash + snapshot.position_value
    os.environ.pop("PORTFOLIO_LEDGER_PATH", None)

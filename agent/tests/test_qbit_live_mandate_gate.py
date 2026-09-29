"""Qbit live mandate gate — fail-closed when execution_mode is live."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.live.mandate.model import (
    AssetClass,
    ConsentMeta,
    HardCaps,
    InstrumentType,
    Mandate,
    UniverseConstraint,
)
from src.live.qbit_gate import check_qbit_live_gate, resolve_live_broker


def _mandate() -> Mandate:
    return Mandate(
        schema_version=1,
        hard_caps=HardCaps(
            account_funding_usd=100_000.0,
            max_order_notional_usd=5_000.0,
            max_total_exposure_usd=50_000.0,
            max_leverage=1.0,
            allowed_instruments=(InstrumentType.EQUITY,),
            max_trades_per_day=10,
        ),
        universe=UniverseConstraint(
            asset_classes=(AssetClass.US_EQUITY,),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=(),
        ),
        consent=ConsentMeta(
            created_at="2026-01-01T00:00:00+00:00",
            consent_token_sha256="abc",
            broker="alpaca",
            account_ref="paper",
            expires_at="2099-12-31T23:59:59+00:00",
        ),
    )


def test_paper_mode_always_ready() -> None:
    result = check_qbit_live_gate({"execution_mode": "paper"})
    assert result.ready is True
    assert result.reasons == []


def test_live_mode_requires_broker_mandate_env(monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_LIVE_ENABLED", raising=False)
    result = check_qbit_live_gate({"execution_mode": "live"})
    assert result.ready is False
    assert "live_broker_not_configured" in result.reasons
    assert "execution_live_disabled" in result.reasons


def test_live_mode_passes_with_mandate_and_env(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_LIVE_ENABLED", "true")
    with patch("src.live.qbit_gate.load_mandate", return_value=_mandate()):
        with patch("src.live.qbit_gate.halt_flag_set", return_value=False):
            result = check_qbit_live_gate(
                {"execution_mode": "live", "live_broker": "alpaca"},
            )
    assert result.ready is True
    assert result.mandate_ok is True


def test_live_mode_blocks_on_halt(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_LIVE_ENABLED", "true")
    with patch("src.live.qbit_gate.load_mandate", return_value=_mandate()):
        with patch("src.live.qbit_gate.halt_flag_set", return_value=True):
            result = check_qbit_live_gate(
                {"execution_mode": "live", "live_broker": "alpaca"},
            )
    assert result.ready is False
    assert "halt_tripped" in result.reasons


def test_orchestrator_run_cycle_skips_when_live_gate_blocked(tmp_path: Path) -> None:
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "qbit" / "services" / "trading-orchestrator" / "main.py"
    spec = importlib.util.spec_from_file_location("orch_live_gate_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["orch_live_gate_test"] = module
    spec.loader.exec_module(module)

    config = {
        "enabled": True,
        "execution_mode": "live",
        "live_broker": "alpaca",
        "trading_hours": {"start": "00:00", "end": "23:59"},
        "portfolio": {"initial_cash": 1_000_000},
        "strategies": [{"strategy_id": "s1", "enabled": True, "params": {"universe_symbols": ["000001.SZ"]}}],
    }

    module.wire_dependencies(
        get_quotes=lambda s: {},
        allocate_portfolio=lambda p: None,
        execute_rebalance=lambda p: type("R", (), {"orders": []})(),
        get_ledger_snapshot=lambda p: type("S", (), {"positions": [], "portfolio_value": 1e6, "cash": 1e6})(),
        ensure_ledger=lambda c: None,
        apply_ledger_fill=lambda p: None,
        fill_order=lambda oid, p: None,
    )
    module.load_config = lambda: config

    with patch("src.live.qbit_gate.check_qbit_live_gate") as mock_gate:
        mock_gate.return_value = check_qbit_live_gate({"execution_mode": "live", "live_broker": ""})
        result = module.run_cycle(force=True, rebalance=True)
    assert result.skipped is True
    assert result.skip_reason == "live_gate_blocked"

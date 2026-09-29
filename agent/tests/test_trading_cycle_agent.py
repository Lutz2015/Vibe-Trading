"""Tests for trading-cycle Agent decision logs."""

from __future__ import annotations

from unittest.mock import patch

from src.insight.trading_cycle_agent import (
    CycleAgentLog,
    agent_log_enabled,
    build_cycle_brief,
    generate_cycle_decision_log,
    persist_agent_log,
)


def test_agent_log_enabled_defaults_paper_only() -> None:
    assert agent_log_enabled({"mode": "paper"}) is True
    assert agent_log_enabled({"mode": "live"}) is False
    assert agent_log_enabled({"mode": "live", "agent_log": {"enabled": True}}) is True


def test_build_cycle_brief_includes_strategies_and_orders() -> None:
    text = build_cycle_brief(
        {
            "as_of": "2026-05-27T14:55:00",
            "skipped": False,
            "orders_submitted": 1,
            "strategies": [{"strategy_id": "s1", "picked": ["000001.SZ"], "rule": "momentum"}],
            "orders": [{"side": "BUY", "symbol": "000001.SZ", "quantity": 100, "price": 10.0, "status": "filled"}],
        }
    )
    assert "000001.SZ" in text
    assert "momentum" in text
    assert "BUY" in text


def test_generate_cycle_decision_log_skipped_uses_template() -> None:
    entry = generate_cycle_decision_log(
        {"as_of": "t", "skipped": True, "skip_reason": "not_rebalance_window"},
        locale="zh-CN",
    )
    assert entry.ok is True
    assert "not_rebalance_window" in entry.text


def test_persist_agent_log_ring_buffer() -> None:
    state: dict = {}
    for idx in range(25):
        persist_agent_log(
            state,
            CycleAgentLog(as_of=f"t{idx}", ok=True, text=f"log-{idx}"),
        )
    assert len(state["agent_log_history"]) == 20
    assert state["last_agent_log"]["text"] == "log-24"


def test_finalize_cycle_response_integration() -> None:
    from pathlib import Path
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "qbit" / "services" / "trading-orchestrator" / "main.py"
    spec = importlib.util.spec_from_file_location("orch_agent_log_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["orch_agent_log_test"] = module
    spec.loader.exec_module(module)

    resp = module.RunCycleResponse(
        skipped=True,
        skip_reason="outside_trading_hours",
        as_of="2026-05-27T10:00:00",
    )
    config = {"mode": "paper", "agent_log": {"enabled": True, "locale": "zh-CN", "max_iterations": 2}}

    with patch.object(module, "load_state", return_value={}):
        with patch.object(module, "save_state"):
            out = module._finalize_cycle_response(config, resp)
    assert out.agent_log_ok is True
    assert "outside_trading_hours" in out.agent_log

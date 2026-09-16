from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import yaml
from fastapi.testclient import TestClient


def _load_module(module_name: str, relative_path: str) -> Any:
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_risk_app() -> Any:
    module = _load_module("risk_engine_main_for_ops_test", "qbit/services/risk-engine/main.py")
    module._trading_enabled_override = None
    module._kill_switch_override = None
    return module.app


def test_risk_limits_defaults() -> None:
    config_path = Path(__file__).resolve().parents[2] / "qbit" / "configs" / "risk-limits.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["global"]["trading_enabled"] is False
    assert data["global"]["kill_switch"] is False


def test_kill_switch_blocks_orders() -> None:
    client = TestClient(_load_risk_app())
    client.post("/ops/kill-switch", json={"enabled": True})

    response = client.post(
        "/risk/check-order",
        json={
            "symbol": "600519.SH",
            "side": "BUY",
            "quantity": 100,
            "notional": 150000.0,
            "portfolio_value": 1200000.0,
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["reason"] == "trading_disabled_or_killed"


def test_trading_toggle_updates_status() -> None:
    client = TestClient(_load_risk_app())
    client.post("/ops/kill-switch", json={"enabled": False})
    client.post("/ops/trading-toggle", json={"enabled": True})

    status = client.get("/ops/status")
    assert status.status_code == 200
    assert status.json()["tradingEnabled"] is True
    assert status.json()["killSwitch"] is False

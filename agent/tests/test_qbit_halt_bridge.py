"""Bidirectional Qbit kill switch ↔ live trip_halt bridge."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from src.live import halt, paths
from src.live.qbit_halt_bridge import (
    QBIT_HALT_REASON,
    clear_qbit_owned_halts,
    describe_qbit_halt_state,
    live_halt_blocks_qbit,
    sync_qbit_kill_switch,
)


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


def _load_risk_module() -> Any:
    path = Path(__file__).resolve().parents[1] / "qbit" / "services" / "risk-engine" / "main.py"
    spec = importlib.util.spec_from_file_location("qbit_risk_engine_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["qbit_risk_engine"] = module
    module._trading_enabled_override = None
    module._kill_switch_override = None
    spec.loader.exec_module(module)
    return module


def test_qbit_kill_switch_trips_live_halt(live_runtime: Path) -> None:
    sync_qbit_kill_switch(True)
    assert halt.halt_flag_set() is True
    meta = halt.read_halt()
    assert meta is not None
    assert meta["reason"] == QBIT_HALT_REASON


def test_qbit_kill_switch_off_clears_owned_halt(live_runtime: Path) -> None:
    sync_qbit_kill_switch(True)
    sync_qbit_kill_switch(False)
    assert halt.halt_flag_set() is False


def test_live_halt_trips_qbit_ops_status(live_runtime: Path) -> None:
    risk = _load_risk_module()
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(risk.app)

    halt.trip_halt(by="frontend", reason="manual emergency")

    status = client.get("/ops/status").json()
    assert status["killSwitch"] is True
    assert status["tradingEnabled"] is False


def test_live_halt_clear_resets_qbit_override(live_runtime: Path) -> None:
    risk = _load_risk_module()
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(risk.app)

    halt.trip_halt(by="frontend", reason="manual emergency")
    assert client.get("/ops/status").json()["killSwitch"] is True

    halt.clear_halt()
    status = client.get("/ops/status").json()
    assert status["killSwitch"] is False


def test_risk_engine_kill_switch_endpoint_syncs_halt(live_runtime: Path) -> None:
    risk = _load_risk_module()
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(risk.app)

    client.post("/ops/kill-switch", json={"enabled": True})
    assert halt.halt_flag_set() is True
    assert client.get("/ops/status").json()["killSwitch"] is True

    client.post("/ops/kill-switch", json={"enabled": False})
    assert halt.halt_flag_set() is False
    assert client.get("/ops/status").json()["killSwitch"] is False


def test_live_halt_blocks_qbit_without_override(live_runtime: Path) -> None:
    halt.trip_halt(by="cli", reason="external")
    assert live_halt_blocks_qbit() is True


def test_describe_halt_state_global_qbit_source(live_runtime: Path) -> None:
    sync_qbit_kill_switch(True)
    detail = describe_qbit_halt_state()
    assert detail["active"] is True
    assert detail["scope"] == "global"
    assert detail["source"] == "qbit"


def test_describe_halt_state_frontend_source(live_runtime: Path) -> None:
    halt.trip_halt(by="frontend", reason="user stop")
    detail = describe_qbit_halt_state()
    assert detail["active"] is True
    assert detail["scope"] == "global"
    assert detail["source"] == "frontend"
    assert detail["reason"] == "user stop"


def test_ops_status_includes_halt_detail(live_runtime: Path) -> None:
    risk = _load_risk_module()
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(risk.app)
    sync_qbit_kill_switch(True)
    payload = client.get("/ops/status").json()
    assert payload["halt"]["active"] is True
    assert payload["halt"]["source"] == "qbit"

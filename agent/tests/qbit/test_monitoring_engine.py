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


def test_metrics_computation_basic() -> None:
    mon = _load_module("monitoring_engine_test_basic", "qbit/services/monitoring-engine/main.py")
    events = [
        mon.TradeEvent(event_type="ORDER_SUCCESS", latency_ms=100.0, ts=1.0),
        mon.TradeEvent(event_type="ORDER_SUCCESS", latency_ms=200.0, ts=2.0),
        mon.TradeEvent(event_type="ORDER_FAIL", latency_ms=300.0, ts=3.0),
        mon.TradeEvent(event_type="RISK_REJECT", latency_ms=50.0, ts=4.0),
    ]
    metrics = mon._compute_metrics(events)

    assert metrics.window_events == 4
    assert metrics.order_success == 2
    assert metrics.order_fail == 1
    assert metrics.risk_reject == 1
    # error_rate = 1 fail / (2 success + 1 fail) = 33.33%
    assert 33.0 < metrics.error_rate_pct < 34.0
    # reject_rate = 1 reject / 4 total = 25%
    assert metrics.reject_rate_pct == 25.0


def test_alert_triggers_when_thresholds_exceeded() -> None:
    mon = _load_module("monitoring_engine_test_alerts", "qbit/services/monitoring-engine/main.py")
    events = [
        mon.TradeEvent(event_type="ORDER_FAIL", latency_ms=2000.0, ts=1.0),
        mon.TradeEvent(event_type="RISK_REJECT", latency_ms=2000.0, ts=2.0),
    ]
    metrics = mon._compute_metrics(events)
    thresholds = mon.AlertThresholds(error_rate_pct=5.0, reject_rate_pct=10.0, p99_latency_ms=1500.0)
    result = mon._evaluate_alerts(metrics, thresholds)

    assert result.has_alerts is True
    triggered = [a.metric for a in result.alerts if a.triggered]
    assert "error_rate_pct" in triggered
    assert "reject_rate_pct" in triggered
    assert "p99_latency_ms" in triggered


def test_alert_no_triggers_under_thresholds() -> None:
    mon = _load_module("monitoring_engine_test_no_alerts", "qbit/services/monitoring-engine/main.py")
    events = [
        mon.TradeEvent(event_type="ORDER_SUCCESS", latency_ms=100.0, ts=1.0),
        mon.TradeEvent(event_type="ORDER_SUCCESS", latency_ms=120.0, ts=2.0),
        mon.TradeEvent(event_type="ORDER_SUCCESS", latency_ms=80.0, ts=3.0),
    ]
    metrics = mon._compute_metrics(events)
    thresholds = mon.AlertThresholds(error_rate_pct=5.0, reject_rate_pct=10.0, p99_latency_ms=1500.0)
    result = mon._evaluate_alerts(metrics, thresholds)

    assert result.has_alerts is False
    assert all(not a.triggered for a in result.alerts)


def test_metrics_empty_events() -> None:
    mon = _load_module("monitoring_engine_test_empty", "qbit/services/monitoring-engine/main.py")
    metrics = mon._compute_metrics([])
    assert metrics.window_events == 0
    assert metrics.error_rate_pct == 0.0
    assert metrics.p99_latency_ms == 0.0

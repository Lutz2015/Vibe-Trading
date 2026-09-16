from __future__ import annotations

import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="monitoring-engine", version="0.1.0")

# ---------------------------------------------------------------------------
# Event storage (in-memory, sliding window)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_MAX_EVENTS = 10_000


class TradeEvent(BaseModel):
    event_type: str  # ORDER_SUCCESS | ORDER_FAIL | RISK_REJECT
    latency_ms: float = Field(ge=0)
    symbol: str = ""
    trace_id: str = ""
    ts: float = 0.0  # epoch seconds, filled by server if 0


_events: deque[TradeEvent] = deque(maxlen=_MAX_EVENTS)


# ---------------------------------------------------------------------------
# Alert thresholds configuration
# ---------------------------------------------------------------------------


class AlertThresholds(BaseModel):
    error_rate_pct: float = Field(default=5.0, ge=0)
    reject_rate_pct: float = Field(default=10.0, ge=0)
    p99_latency_ms: float = Field(default=1500.0, ge=0)


_thresholds = AlertThresholds()


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


class MetricsSnapshot(BaseModel):
    window_events: int
    order_success: int
    order_fail: int
    risk_reject: int
    error_rate_pct: float
    reject_rate_pct: float
    avg_latency_ms: float
    p50_latency_ms: float
    p99_latency_ms: float


def _compute_metrics(events: list[TradeEvent]) -> MetricsSnapshot:
    total = len(events)
    if total == 0:
        return MetricsSnapshot(
            window_events=0,
            order_success=0,
            order_fail=0,
            risk_reject=0,
            error_rate_pct=0.0,
            reject_rate_pct=0.0,
            avg_latency_ms=0.0,
            p50_latency_ms=0.0,
            p99_latency_ms=0.0,
        )

    success = sum(1 for e in events if e.event_type == "ORDER_SUCCESS")
    fail = sum(1 for e in events if e.event_type == "ORDER_FAIL")
    reject = sum(1 for e in events if e.event_type == "RISK_REJECT")

    order_total = success + fail
    error_rate = (fail / order_total * 100.0) if order_total > 0 else 0.0
    reject_rate = (reject / total * 100.0) if total > 0 else 0.0

    latencies = sorted(e.latency_ms for e in events)
    avg_lat = sum(latencies) / total
    p50_idx = max(0, int(total * 0.50) - 1)
    p99_idx = max(0, int(total * 0.99) - 1)

    return MetricsSnapshot(
        window_events=total,
        order_success=success,
        order_fail=fail,
        risk_reject=reject,
        error_rate_pct=round(error_rate, 4),
        reject_rate_pct=round(reject_rate, 4),
        avg_latency_ms=round(avg_lat, 4),
        p50_latency_ms=round(latencies[p50_idx], 4),
        p99_latency_ms=round(latencies[p99_idx], 4),
    )


# ---------------------------------------------------------------------------
# Alert evaluation
# ---------------------------------------------------------------------------


class AlertItem(BaseModel):
    metric: str
    current_value: float
    threshold: float
    triggered: bool


class AlertCheckResponse(BaseModel):
    has_alerts: bool
    alerts: list[AlertItem]
    metrics: MetricsSnapshot


def _evaluate_alerts(metrics: MetricsSnapshot, thresholds: AlertThresholds) -> AlertCheckResponse:
    items: list[AlertItem] = []

    error_alert = AlertItem(
        metric="error_rate_pct",
        current_value=metrics.error_rate_pct,
        threshold=thresholds.error_rate_pct,
        triggered=metrics.error_rate_pct > thresholds.error_rate_pct,
    )
    items.append(error_alert)

    reject_alert = AlertItem(
        metric="reject_rate_pct",
        current_value=metrics.reject_rate_pct,
        threshold=thresholds.reject_rate_pct,
        triggered=metrics.reject_rate_pct > thresholds.reject_rate_pct,
    )
    items.append(reject_alert)

    latency_alert = AlertItem(
        metric="p99_latency_ms",
        current_value=metrics.p99_latency_ms,
        threshold=thresholds.p99_latency_ms,
        triggered=metrics.p99_latency_ms > thresholds.p99_latency_ms,
    )
    items.append(latency_alert)

    return AlertCheckResponse(
        has_alerts=any(a.triggered for a in items),
        alerts=items,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "monitoring-engine"}


class IngestRequest(BaseModel):
    events: list[TradeEvent]


class IngestResponse(BaseModel):
    accepted: int
    total_buffered: int


@app.post("/monitoring/ingest", response_model=IngestResponse)
def ingest_events(payload: IngestRequest) -> IngestResponse:
    now = time.time()
    with _lock:
        for event in payload.events:
            if event.ts == 0.0:
                event.ts = now
            _events.append(event)
        buffered = len(_events)
    return IngestResponse(accepted=len(payload.events), total_buffered=buffered)


@app.get("/monitoring/metrics", response_model=MetricsSnapshot)
def get_metrics() -> MetricsSnapshot:
    with _lock:
        snapshot = list(_events)
    return _compute_metrics(snapshot)


@app.get("/monitoring/alerts", response_model=AlertCheckResponse)
def check_alerts() -> AlertCheckResponse:
    with _lock:
        snapshot = list(_events)
    metrics = _compute_metrics(snapshot)
    return _evaluate_alerts(metrics, _thresholds)


@app.post("/monitoring/thresholds", response_model=AlertThresholds)
def update_thresholds(payload: AlertThresholds) -> AlertThresholds:
    global _thresholds
    _thresholds = payload
    return _thresholds


@app.get("/monitoring/thresholds", response_model=AlertThresholds)
def get_thresholds() -> AlertThresholds:
    return _thresholds


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8007, reload=False)

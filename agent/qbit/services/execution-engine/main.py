from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Protocol
from urllib.request import Request, urlopen
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="execution-engine", version="0.1.0")


class CreateOrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(gt=0)
    limit_price: float = Field(gt=0)
    strategy_id: str
    client_order_id: str
    trace_id: str


class OrderResponse(BaseModel):
    order_id: str
    client_order_id: str
    status: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    limit_price: float
    filled_notional: float
    avg_fill_price: float
    created_at: str
    updated_at: str
    trace_id: str
    status_history: list[str]


class FillOrderRequest(BaseModel):
    fill_quantity: int = Field(gt=0)
    fill_price: float = Field(gt=0)


class CancelOrderRequest(BaseModel):
    reason: str = "manual_cancel"


class RejectOrderRequest(BaseModel):
    reason: str = "manual_reject"


class RoutedOrderRequest(CreateOrderRequest):
    mode: str = "paper"  # paper | live


class RoutedOrderResponse(BaseModel):
    order: OrderResponse
    routed_mode: str
    live_attempted: bool
    live_accepted: bool
    reason: str = ""


_orders_by_id: dict[str, OrderResponse] = {}
_orders_by_client_id: dict[str, str] = {}
TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED"}
_qbit_root = Path(__file__).resolve().parents[2]
_ledger_path = Path(
    os.getenv(
        "EXECUTION_LEDGER_PATH",
        str(Path.home() / ".person-trading" / "qbit" / "execution-orders-ledger.json"),
    )
)
_live_enabled = os.getenv("EXECUTION_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
_live_canary_ratio = float(os.getenv("EXECUTION_LIVE_CANARY_RATIO", "0.0"))
_broker_order_url = os.getenv("BROKER_ORDER_URL", "").strip()
_broker_adapter_name = os.getenv("BROKER_ADAPTER", "http_json").strip().lower()


class BrokerAdapter(Protocol):
    name: str

    def send_order(self, payload: CreateOrderRequest) -> bool:
        ...


class HttpJsonBrokerAdapter:
    name = "http_json"

    def send_order(self, payload: CreateOrderRequest) -> bool:
        if not _broker_order_url:
            return False
        req_body = json.dumps(payload.model_dump()).encode("utf-8")
        req = Request(
            _broker_order_url,
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=3.0) as resp:
            code = getattr(resp, "status", 200)
        return 200 <= int(code) < 300


class SimBrokerAdapter:
    name = "sim_broker"

    def send_order(self, payload: CreateOrderRequest) -> bool:
        del payload
        # Simulated dedicated broker adapter for local gray-release tests.
        return True


def _build_broker_adapter() -> BrokerAdapter:
    if _broker_adapter_name == "sim_broker":
        return SimBrokerAdapter()
    return HttpJsonBrokerAdapter()


_broker_adapter = _build_broker_adapter()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_canary_hit(client_order_id: str) -> bool:
    if _live_canary_ratio <= 0:
        return False
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < _live_canary_ratio


def _push_to_live_broker(payload: CreateOrderRequest) -> bool:
    return _broker_adapter.send_order(payload)


def persist_ledger() -> None:
    _ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"orders": [order.model_dump() for order in _orders_by_id.values()]}
    _ledger_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def load_ledger() -> None:
    _orders_by_id.clear()
    _orders_by_client_id.clear()
    if not _ledger_path.exists():
        return
    raw = _ledger_path.read_text(encoding="utf-8")
    if not raw.strip():
        return
    payload = json.loads(raw)
    for item in payload.get("orders", []):
        order = OrderResponse(**item)
        _orders_by_id[order.order_id] = order
        _orders_by_client_id[order.client_order_id] = order.order_id


def apply_transition(order: OrderResponse, next_status: str) -> None:
    current = order.status
    if current in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"invalid_transition: {current} -> {next_status} (terminal_status)",
        )

    allowed: dict[str, set[str]] = {
        "ACCEPTED": {"PARTIAL", "FILLED", "CANCELED", "REJECTED"},
        "PARTIAL": {"PARTIAL", "FILLED", "CANCELED", "REJECTED"},
    }
    if next_status not in allowed.get(current, set()):
        raise HTTPException(status_code=409, detail=f"invalid_transition: {current} -> {next_status}")

    order.status = next_status
    order.updated_at = now_iso()
    order.status_history.append(next_status)
    persist_ledger()


@app.get("/health")
def health() -> dict[str, str | float]:
    return {
        "status": "ok",
        "service": "execution-engine",
        "live_enabled": str(_live_enabled).lower(),
        "live_canary_ratio": _live_canary_ratio,
        "broker_adapter": _broker_adapter.name,
    }


@app.post("/orders", response_model=OrderResponse)
def create_order(payload: CreateOrderRequest) -> OrderResponse:
    existing_id = _orders_by_client_id.get(payload.client_order_id)
    if existing_id is not None:
        return _orders_by_id[existing_id]

    order_id = f"ord_{uuid4().hex[:16]}"
    created_at = now_iso()
    order = OrderResponse(
        order_id=order_id,
        client_order_id=payload.client_order_id,
        status="ACCEPTED",
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        filled_quantity=0,
        limit_price=payload.limit_price,
        filled_notional=0.0,
        avg_fill_price=0.0,
        created_at=created_at,
        updated_at=created_at,
        trace_id=payload.trace_id,
        status_history=["ACCEPTED"],
    )
    _orders_by_id[order_id] = order
    _orders_by_client_id[payload.client_order_id] = order_id
    persist_ledger()
    return order


@app.post("/orders/route", response_model=RoutedOrderResponse)
def create_routed_order(payload: RoutedOrderRequest) -> RoutedOrderResponse:
    create_payload = CreateOrderRequest(**payload.model_dump(exclude={"mode"}))

    if payload.mode == "paper":
        order = create_order(create_payload)
        return RoutedOrderResponse(
            order=order,
            routed_mode="paper",
            live_attempted=False,
            live_accepted=False,
            reason="paper_mode",
        )

    if payload.mode != "live":
        raise HTTPException(status_code=400, detail="invalid_mode: use paper|live")

    if not _live_enabled:
        order = create_order(create_payload)
        return RoutedOrderResponse(
            order=order,
            routed_mode="paper",
            live_attempted=False,
            live_accepted=False,
            reason="live_mode_disabled",
        )

    if not _is_canary_hit(payload.client_order_id):
        order = create_order(create_payload)
        return RoutedOrderResponse(
            order=order,
            routed_mode="paper",
            live_attempted=False,
            live_accepted=False,
            reason="canary_not_selected",
        )

    live_ok = False
    try:
        live_ok = _push_to_live_broker(create_payload)
    except Exception:
        live_ok = False

    order = create_order(create_payload)
    return RoutedOrderResponse(
        order=order,
        routed_mode="live" if live_ok else "paper",
        live_attempted=True,
        live_accepted=live_ok,
        reason="live_accepted" if live_ok else "live_push_failed_fallback_paper",
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str) -> OrderResponse:
    order = _orders_by_id.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    return order


@app.post("/orders/{order_id}/fills", response_model=OrderResponse)
def fill_order(order_id: str, payload: FillOrderRequest) -> OrderResponse:
    order = _orders_by_id.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")

    remaining = order.quantity - order.filled_quantity
    if payload.fill_quantity > remaining:
        raise HTTPException(status_code=400, detail="fill_quantity_exceeds_remaining")

    already_notional = order.filled_notional
    new_notional = payload.fill_quantity * payload.fill_price
    updated_filled_qty = order.filled_quantity + payload.fill_quantity
    updated_notional = already_notional + new_notional

    order.filled_quantity = updated_filled_qty
    order.filled_notional = updated_notional
    order.avg_fill_price = updated_notional / updated_filled_qty

    next_status = "FILLED" if updated_filled_qty == order.quantity else "PARTIAL"
    apply_transition(order, next_status)
    return order


@app.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(order_id: str, payload: CancelOrderRequest) -> OrderResponse:
    del payload  # reserved for future audit fields
    order = _orders_by_id.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    apply_transition(order, "CANCELED")
    return order


@app.post("/orders/{order_id}/reject", response_model=OrderResponse)
def reject_order(order_id: str, payload: RejectOrderRequest) -> OrderResponse:
    del payload  # reserved for future audit fields
    order = _orders_by_id.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    apply_transition(order, "REJECTED")
    return order


load_ledger()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8003, reload=False)

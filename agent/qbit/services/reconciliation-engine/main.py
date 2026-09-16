from __future__ import annotations

from collections import defaultdict

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="reconciliation-engine", version="0.1.0")


class OrderLedgerItem(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: int = Field(ge=0)
    filled_quantity: int = Field(ge=0)
    avg_fill_price: float = Field(ge=0)
    filled_notional: float = Field(ge=0)
    status: str


class TradeLedgerItem(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    fee: float = Field(ge=0)


class PositionLedgerItem(BaseModel):
    symbol: str
    quantity: int


class CashLedgerSnapshot(BaseModel):
    initial_cash: float = Field(ge=0)
    current_cash: float = Field(ge=0)


class ReconcileRequest(BaseModel):
    orders: list[OrderLedgerItem]
    trades: list[TradeLedgerItem]
    positions: list[PositionLedgerItem]
    cash: CashLedgerSnapshot
    tolerance: float = Field(default=0.01, ge=0)


class ReconcileSummary(BaseModel):
    total_orders: int
    total_trades: int
    total_position_symbols: int
    order_filled_notional: float
    trade_notional: float
    expected_cash: float
    actual_cash: float


class ReconcileResponse(BaseModel):
    consistent: bool
    checks_passed: list[str]
    checks_failed: list[str]
    summary: ReconcileSummary


def _compare_with_tolerance(left: float, right: float, tolerance: float) -> bool:
    return abs(left - right) <= tolerance


def run_reconciliation(payload: ReconcileRequest) -> ReconcileResponse:
    checks_passed: list[str] = []
    checks_failed: list[str] = []

    order_filled_notional = sum(order.filled_notional for order in payload.orders)
    trade_notional = sum(trade.quantity * trade.price for trade in payload.trades)

    if _compare_with_tolerance(order_filled_notional, trade_notional, payload.tolerance):
        checks_passed.append("orders_vs_trades_notional")
    else:
        checks_failed.append(
            "orders_vs_trades_notional: "
            f"orders={order_filled_notional:.4f}, trades={trade_notional:.4f}"
        )

    trade_positions: dict[str, int] = defaultdict(int)
    trade_cash_delta = 0.0
    trade_fee_total = 0.0

    for trade in payload.trades:
        signed_qty = trade.quantity if trade.side.upper() == "BUY" else -trade.quantity
        trade_positions[trade.symbol] += signed_qty
        signed_notional = -(trade.quantity * trade.price) if trade.side.upper() == "BUY" else (trade.quantity * trade.price)
        trade_cash_delta += signed_notional
        trade_fee_total += trade.fee

    position_map = {item.symbol: item.quantity for item in payload.positions}
    position_symbols = set(trade_positions.keys()) | set(position_map.keys())
    position_mismatches: list[str] = []
    for symbol in sorted(position_symbols):
        from_trades = trade_positions.get(symbol, 0)
        from_positions = position_map.get(symbol, 0)
        if from_trades != from_positions:
            position_mismatches.append(
                f"{symbol}: trades={from_trades}, positions={from_positions}"
            )

    if not position_mismatches:
        checks_passed.append("trades_vs_positions_quantity")
    else:
        checks_failed.append("trades_vs_positions_quantity: " + "; ".join(position_mismatches))

    expected_cash = payload.cash.initial_cash + trade_cash_delta - trade_fee_total
    if _compare_with_tolerance(expected_cash, payload.cash.current_cash, payload.tolerance):
        checks_passed.append("cash_consistency")
    else:
        checks_failed.append(
            "cash_consistency: "
            f"expected={expected_cash:.4f}, actual={payload.cash.current_cash:.4f}"
        )

    summary = ReconcileSummary(
        total_orders=len(payload.orders),
        total_trades=len(payload.trades),
        total_position_symbols=len(payload.positions),
        order_filled_notional=round(order_filled_notional, 6),
        trade_notional=round(trade_notional, 6),
        expected_cash=round(expected_cash, 6),
        actual_cash=round(payload.cash.current_cash, 6),
    )
    return ReconcileResponse(
        consistent=len(checks_failed) == 0,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
        summary=summary,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "reconciliation-engine"}


@app.post("/reconcile/run", response_model=ReconcileResponse)
def reconcile_run(payload: ReconcileRequest) -> ReconcileResponse:
    return run_reconciliation(payload)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8006, reload=False)

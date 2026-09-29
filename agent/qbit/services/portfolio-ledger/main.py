from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="portfolio-ledger", version="0.1.0")

_qbit_root = Path(__file__).resolve().parents[2]
_ledger_path = Path(
    os.getenv(
        "PORTFOLIO_LEDGER_PATH",
        str(Path.home() / ".person-trading" / "qbit" / "portfolio-ledger.json"),
    )
)


class PositionItem(BaseModel):
    symbol: str
    quantity: int = Field(ge=0)
    name: str | None = None
    avg_cost: float | None = None
    last_price: float | None = None
    market_value: float | None = None


class LedgerSnapshot(BaseModel):
    initial_cash: float
    cash: float
    positions: list[PositionItem]
    position_value: float
    portfolio_value: float
    as_of: str


class ApplyFillRequest(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    fee: float = Field(default=0.0, ge=0)
    name: str | None = None
    strategy_id: str | None = None


class ResetLedgerRequest(BaseModel):
    initial_cash: float = Field(gt=0)


class TradeRecord(BaseModel):
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float
    cash_after: float
    ts: str
    name: str | None = None
    strategy_id: str | None = None


_cash: float = 0.0
_initial_cash: float = 0.0
_positions: dict[str, int] = {}
_trades: list[TradeRecord] = []
_names: dict[str, str] = {}
_avg_cost: dict[str, float] = {}
# Optional live quote enricher: symbols -> {symbol: {"price": float|None, "name": str|None}}
_quote_lookup = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_quote_lookup(fn) -> None:
    """Register a live quote enricher used when snapshot prices are omitted."""
    global _quote_lookup
    _quote_lookup = fn


def persist_ledger() -> None:
    _ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "initial_cash": _initial_cash,
        "cash": _cash,
        "positions": [
            {
                "symbol": symbol,
                "quantity": qty,
                "name": _names.get(symbol),
                "avg_cost": _avg_cost.get(symbol),
            }
            for symbol, qty in sorted(_positions.items())
            if qty > 0
        ],
        "trades": [item.model_dump() for item in _trades[-500:]],
    }
    _ledger_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def load_ledger() -> None:
    global _cash, _initial_cash, _positions, _trades, _names, _avg_cost
    _cash = 0.0
    _initial_cash = 0.0
    _positions.clear()
    _trades.clear()
    _names.clear()
    _avg_cost.clear()
    if not _ledger_path.exists():
        return
    raw = _ledger_path.read_text(encoding="utf-8")
    if not raw.strip():
        return
    payload = json.loads(raw)
    _initial_cash = float(payload.get("initial_cash", 0.0))
    _cash = float(payload.get("cash", _initial_cash))
    for item in payload.get("positions", []):
        symbol = str(item.get("symbol", "")).strip()
        quantity = int(item.get("quantity", 0))
        if symbol and quantity > 0:
            _positions[symbol] = quantity
            name = item.get("name")
            if name:
                _names[symbol] = str(name)
            avg_cost = item.get("avg_cost")
            if avg_cost is not None:
                try:
                    _avg_cost[symbol] = float(avg_cost)
                except (TypeError, ValueError):
                    pass
    for item in payload.get("trades", []):
        record = TradeRecord(**item)
        if record.name:
            _names.setdefault(record.symbol, record.name)
        _trades.append(record)


def ensure_initialized(initial_cash: float) -> None:
    global _initial_cash, _cash
    if _initial_cash <= 0 and _cash <= 0 and not _positions:
        _initial_cash = initial_cash
        _cash = initial_cash
        persist_ledger()


def build_snapshot(prices: dict[str, float] | None = None) -> LedgerSnapshot:
    marks = dict(prices or {})
    names = dict(_names)
    if _quote_lookup and _positions:
        try:
            lookup = _quote_lookup(sorted(_positions)) or {}
        except Exception:
            lookup = {}
        for symbol, meta in lookup.items():
            if not isinstance(meta, dict):
                continue
            price = meta.get("price")
            if price is not None and symbol not in marks:
                try:
                    value = float(price)
                    if value > 0:
                        marks[symbol] = value
                except (TypeError, ValueError):
                    pass
            name = meta.get("name")
            if name:
                names.setdefault(symbol, str(name))

    position_items: list[PositionItem] = []
    position_value = 0.0
    for symbol, qty in sorted(_positions.items()):
        if qty <= 0:
            continue
        mark = marks.get(symbol, 0.0)
        market_value = qty * mark if mark > 0 else None
        if market_value is not None:
            position_value += market_value
        position_items.append(
            PositionItem(
                symbol=symbol,
                quantity=qty,
                name=names.get(symbol),
                avg_cost=_avg_cost.get(symbol),
                last_price=mark if mark > 0 else None,
                market_value=market_value,
            )
        )
    return LedgerSnapshot(
        initial_cash=round(_initial_cash, 4),
        cash=round(_cash, 4),
        positions=position_items,
        position_value=round(position_value, 4),
        portfolio_value=round(_cash + position_value, 4),
        as_of=now_iso(),
    )


def apply_fill(payload: ApplyFillRequest) -> LedgerSnapshot:
    global _cash
    side = payload.side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    symbol = payload.symbol.strip()
    if not symbol:
        raise ValueError("symbol must not be empty")
    if payload.name:
        _names[symbol] = str(payload.name).strip()

    notional = payload.quantity * payload.price
    if side == "BUY":
        total_cost = notional + payload.fee
        if total_cost > _cash + 1e-9:
            raise ValueError("insufficient_cash")
        prev_qty = _positions.get(symbol, 0)
        prev_cost = _avg_cost.get(symbol, payload.price)
        new_qty = prev_qty + payload.quantity
        if new_qty > 0:
            _avg_cost[symbol] = (prev_qty * prev_cost + payload.quantity * payload.price) / new_qty
        _cash -= total_cost
        _positions[symbol] = new_qty
    else:
        current = _positions.get(symbol, 0)
        if payload.quantity > current:
            raise ValueError("insufficient_position")
        _cash += notional - payload.fee
        remaining = current - payload.quantity
        if remaining <= 0:
            _positions.pop(symbol, None)
            _avg_cost.pop(symbol, None)
        else:
            _positions[symbol] = remaining

    _trades.append(
        TradeRecord(
            symbol=symbol,
            side=side,
            quantity=payload.quantity,
            price=payload.price,
            fee=payload.fee,
            cash_after=round(_cash, 4),
            ts=now_iso(),
            name=payload.name or _names.get(symbol),
            strategy_id=payload.strategy_id,
        )
    )
    persist_ledger()
    return build_snapshot()


def reset_ledger(initial_cash: float) -> LedgerSnapshot:
    global _cash, _initial_cash, _positions, _trades, _names, _avg_cost
    _initial_cash = initial_cash
    _cash = initial_cash
    _positions.clear()
    _trades.clear()
    _names.clear()
    _avg_cost.clear()
    persist_ledger()
    return build_snapshot()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "portfolio-ledger"}


@app.get("/ledger/snapshot", response_model=LedgerSnapshot)
def get_ledger_snapshot(
    prices_json: str | None = None,
) -> LedgerSnapshot:
    prices: dict[str, float] | None = None
    if prices_json:
        raw = json.loads(prices_json)
        prices = {str(k): float(v) for k, v in raw.items()}
    return build_snapshot(prices=prices)


@app.post("/ledger/apply-fill", response_model=LedgerSnapshot)
def post_apply_fill(payload: ApplyFillRequest) -> LedgerSnapshot:
    try:
        return apply_fill(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ledger/reset", response_model=LedgerSnapshot)
def post_reset_ledger(payload: ResetLedgerRequest) -> LedgerSnapshot:
    return reset_ledger(payload.initial_cash)


@app.get("/ledger/trades", response_model=list[TradeRecord])
def get_trades(limit: int = 100) -> list[TradeRecord]:
    if limit <= 0:
        return []
    return _trades[-limit:]


load_ledger()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8009, reload=False)

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

app = FastAPI(title="strategy-engine", version="0.1.0")


class SignalRequest(BaseModel):
    strategy_id: str
    symbols: list[str]


class SignalItem(BaseModel):
    symbol: str
    score: float
    action: str


class SignalResponse(BaseModel):
    strategy_id: str
    strategy_version: str | None
    data_version: str | None
    rollout_mode: str | None
    canary_ratio: float | None
    as_of: str
    signals: list[SignalItem]


class StrategyAllocationInput(BaseModel):
    strategy_id: str
    expected_return_pct: float
    risk_volatility_pct: float = 0.0
    max_weight: float | None = None


class PortfolioAllocateRequest(BaseModel):
    items: list[StrategyAllocationInput]
    total_exposure: float = 1.0
    max_single_weight: float = 0.5
    min_weight_floor: float = 0.0
    risk_aversion: float = 1.0


class StrategyWeight(BaseModel):
    strategy_id: str
    weight: float
    score: float


class PortfolioAllocateResponse(BaseModel):
    weights: list[StrategyWeight]
    total_weight: float
    unallocated_weight: float
    as_of: str


class StrategySymbolTarget(BaseModel):
    symbol: str
    weight: float = Field(gt=0, le=1)


class StrategyTargetInput(BaseModel):
    strategy_id: str
    symbols: list[StrategySymbolTarget]


class PositionInput(BaseModel):
    symbol: str
    quantity: int = Field(ge=0)


class PortfolioRebalanceRequest(BaseModel):
    allocation: PortfolioAllocateRequest | None = None
    weights: list[StrategyWeight] | None = None
    strategy_targets: list[StrategyTargetInput]
    portfolio_value: float = Field(gt=0)
    prices: dict[str, float]
    current_positions: list[PositionInput] = []
    execution_mode: str = "paper"
    min_trade_lot: int = Field(default=100, ge=1)
    trace_id: str
    rebalance_id: str

    @model_validator(mode="after")
    def validate_allocation_source(self) -> "PortfolioRebalanceRequest":
        if self.allocation is None and not self.weights:
            raise ValueError("allocation or weights is required")
        if not self.strategy_targets:
            raise ValueError("strategy_targets must not be empty")
        if self.execution_mode not in {"paper", "live"}:
            raise ValueError("execution_mode must be paper or live")
        return self


class RebalanceOrderResult(BaseModel):
    symbol: str
    side: str
    quantity: int
    limit_price: float
    delta_quantity: int
    target_quantity: int
    current_quantity: int
    routed_mode: str | None = None
    live_attempted: bool = False
    live_accepted: bool = False
    risk_accepted: bool = False
    risk_reason: str = ""
    order_id: str | None = None
    client_order_id: str
    skipped: bool = False
    skip_reason: str = ""


class PortfolioRebalanceResponse(BaseModel):
    allocation: PortfolioAllocateResponse
    target_positions: dict[str, int]
    orders: list[RebalanceOrderResult]
    as_of: str


class RiskCheckResult(BaseModel):
    accepted: bool
    reason: str


class RoutedOrderPayload(BaseModel):
    symbol: str
    side: str
    quantity: int
    limit_price: float
    strategy_id: str
    client_order_id: str
    trace_id: str
    mode: str


class RoutedOrderResult(BaseModel):
    order_id: str
    routed_mode: str
    live_attempted: bool
    live_accepted: bool
    reason: str = ""


def _allocate_by_score(payload: PortfolioAllocateRequest) -> PortfolioAllocateResponse:
    if not payload.items:
        return PortfolioAllocateResponse(
            weights=[],
            total_weight=0.0,
            unallocated_weight=payload.total_exposure,
            as_of=datetime.now(timezone.utc).isoformat(),
        )
    if payload.total_exposure <= 0:
        raise ValueError("total_exposure must be positive")
    if payload.max_single_weight <= 0:
        raise ValueError("max_single_weight must be positive")

    raw_scores: dict[str, float] = {}
    cap_by_strategy: dict[str, float] = {}
    for item in payload.items:
        score = item.expected_return_pct - (payload.risk_aversion * item.risk_volatility_pct)
        raw_scores[item.strategy_id] = max(score, 0.0)
        cap = item.max_weight if item.max_weight is not None else payload.max_single_weight
        cap_by_strategy[item.strategy_id] = max(0.0, min(cap, payload.max_single_weight))

    total_score = sum(raw_scores.values())
    if total_score <= 0:
        equal_weight = payload.total_exposure / len(payload.items)
        provisional = {item.strategy_id: min(equal_weight, cap_by_strategy[item.strategy_id]) for item in payload.items}
    else:
        provisional = {
            item.strategy_id: min(payload.total_exposure * (raw_scores[item.strategy_id] / total_score), cap_by_strategy[item.strategy_id])
            for item in payload.items
        }

    allocated = sum(provisional.values())
    remaining = max(0.0, payload.total_exposure - allocated)

    # Second pass: fill remaining weight to highest scores under their caps.
    if remaining > 0:
        ranked = sorted(payload.items, key=lambda x: raw_scores[x.strategy_id], reverse=True)
        for item in ranked:
            if remaining <= 0:
                break
            strategy_id = item.strategy_id
            cap = cap_by_strategy[strategy_id]
            room = max(0.0, cap - provisional[strategy_id])
            if room <= 0:
                continue
            add = min(room, remaining)
            provisional[strategy_id] += add
            remaining -= add

    weights: list[StrategyWeight] = []
    for item in payload.items:
        w = provisional[item.strategy_id]
        if w < payload.min_weight_floor:
            w = 0.0
        weights.append(
            StrategyWeight(strategy_id=item.strategy_id, weight=round(w, 6), score=round(raw_scores[item.strategy_id], 6))
        )
    total_weight = round(sum(w.weight for w in weights), 6)
    return PortfolioAllocateResponse(
        weights=weights,
        total_weight=total_weight,
        unallocated_weight=round(max(0.0, payload.total_exposure - total_weight), 6),
        as_of=datetime.now(timezone.utc).isoformat(),
    )


def _resolve_allocation(payload: PortfolioRebalanceRequest) -> PortfolioAllocateResponse:
    if payload.weights:
        total_weight = round(sum(item.weight for item in payload.weights), 6)
        total_exposure = payload.allocation.total_exposure if payload.allocation else total_weight
        return PortfolioAllocateResponse(
            weights=payload.weights,
            total_weight=total_weight,
            unallocated_weight=round(max(0.0, total_exposure - total_weight), 6),
            as_of=datetime.now(timezone.utc).isoformat(),
        )
    assert payload.allocation is not None
    return _allocate_by_score(payload.allocation)


def _normalize_strategy_symbol_weights(symbols: list[StrategySymbolTarget]) -> dict[str, float]:
    total = sum(item.weight for item in symbols)
    if total <= 0:
        raise ValueError("strategy symbol weights must be positive")
    return {item.symbol: item.weight / total for item in symbols}


def _compute_target_positions(
    allocation: PortfolioAllocateResponse,
    strategy_targets: list[StrategyTargetInput],
    portfolio_value: float,
    prices: dict[str, float],
    min_trade_lot: int,
) -> dict[str, int]:
    weight_by_strategy = {item.strategy_id: item.weight for item in allocation.weights}
    portfolio_weights: dict[str, float] = {}

    for target in strategy_targets:
        strategy_weight = weight_by_strategy.get(target.strategy_id, 0.0)
        if strategy_weight <= 0:
            continue
        normalized = _normalize_strategy_symbol_weights(target.symbols)
        for symbol, symbol_weight in normalized.items():
            portfolio_weights[symbol] = portfolio_weights.get(symbol, 0.0) + (strategy_weight * symbol_weight)

    target_positions: dict[str, int] = {}
    for symbol, weight in portfolio_weights.items():
        price = prices.get(symbol)
        if price is None or price <= 0:
            raise ValueError(f"missing_or_invalid_price:{symbol}")
        raw_qty = int((portfolio_value * weight) / price)
        lot_qty = (raw_qty // min_trade_lot) * min_trade_lot
        if lot_qty > 0:
            target_positions[symbol] = lot_qty
    return target_positions


def _rebalance_orders(
    target_positions: dict[str, int],
    current_positions: list[PositionInput],
    min_trade_lot: int,
) -> list[tuple[str, str, int, int, int, int]]:
    current_map = {item.symbol: item.quantity for item in current_positions}
    symbols = sorted(set(current_map.keys()) | set(target_positions.keys()))
    planned: list[tuple[str, str, int, int, int, int]] = []

    for symbol in symbols:
        current_qty = current_map.get(symbol, 0)
        target_qty = target_positions.get(symbol, 0)
        delta = target_qty - current_qty
        if delta == 0:
            continue
        trade_qty = abs(delta)
        if trade_qty < min_trade_lot:
            continue
        trade_qty = (trade_qty // min_trade_lot) * min_trade_lot
        if trade_qty <= 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        if side == "SELL" and trade_qty > current_qty:
            trade_qty = (current_qty // min_trade_lot) * min_trade_lot
            if trade_qty <= 0:
                continue
        planned.append((symbol, side, trade_qty, delta, target_qty, current_qty))
    return planned


def check_order_risk(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    portfolio_value: float,
) -> RiskCheckResult:
    risk_url = os.getenv("RISK_ENGINE_URL", "http://127.0.0.1:8002").rstrip("/")
    timeout = float(os.getenv("RISK_ENGINE_TIMEOUT_SEC", "1.5"))
    req_body = json.dumps(
        {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "notional": quantity * limit_price,
            "portfolio_value": portfolio_value,
        }
    ).encode("utf-8")
    req = Request(
        f"{risk_url}/risk/check-order",
        data=req_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return RiskCheckResult(
            accepted=bool(payload.get("accepted")),
            reason=str(payload.get("reason", "")),
        )
    except Exception:
        return RiskCheckResult(accepted=False, reason="risk_engine_unavailable")


def route_order(payload: RoutedOrderPayload) -> RoutedOrderResult:
    execution_url = os.getenv("EXECUTION_ENGINE_URL", "http://127.0.0.1:8003").rstrip("/")
    timeout = float(os.getenv("EXECUTION_ENGINE_TIMEOUT_SEC", "2.0"))
    req_body = json.dumps(payload.model_dump()).encode("utf-8")
    req = Request(
        f"{execution_url}/orders/route",
        data=req_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    order = raw.get("order", {})
    return RoutedOrderResult(
        order_id=str(order.get("order_id", "")),
        routed_mode=str(raw.get("routed_mode", "")),
        live_attempted=bool(raw.get("live_attempted")),
        live_accepted=bool(raw.get("live_accepted")),
        reason=str(raw.get("reason", "")),
    )


def _execute_rebalance(payload: PortfolioRebalanceRequest) -> PortfolioRebalanceResponse:
    allocation = _resolve_allocation(payload)
    target_positions = _compute_target_positions(
        allocation=allocation,
        strategy_targets=payload.strategy_targets,
        portfolio_value=payload.portfolio_value,
        prices=payload.prices,
        min_trade_lot=payload.min_trade_lot,
    )
    planned = _rebalance_orders(
        target_positions=target_positions,
        current_positions=payload.current_positions,
        min_trade_lot=payload.min_trade_lot,
    )

    orders: list[RebalanceOrderResult] = []
    for symbol, side, quantity, delta, target_qty, current_qty in planned:
        limit_price = payload.prices[symbol]
        client_order_id = f"{payload.rebalance_id}:{symbol}:{side}"
        risk = check_order_risk(
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            portfolio_value=payload.portfolio_value,
        )
        if not risk.accepted:
            orders.append(
                RebalanceOrderResult(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    limit_price=limit_price,
                    delta_quantity=delta,
                    target_quantity=target_qty,
                    current_quantity=current_qty,
                    risk_accepted=False,
                    risk_reason=risk.reason,
                    client_order_id=client_order_id,
                    skipped=True,
                    skip_reason=f"risk_rejected:{risk.reason}",
                )
            )
            continue

        strategy_id = next(
            (target.strategy_id for target in payload.strategy_targets if any(s.symbol == symbol for s in target.symbols)),
            "portfolio_rebalance",
        )
        routed = route_order(
            RoutedOrderPayload(
                symbol=symbol,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                strategy_id=strategy_id,
                client_order_id=client_order_id,
                trace_id=payload.trace_id,
                mode=payload.execution_mode,
            )
        )
        orders.append(
            RebalanceOrderResult(
                symbol=symbol,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                delta_quantity=delta,
                target_quantity=target_qty,
                current_quantity=current_qty,
                routed_mode=routed.routed_mode,
                live_attempted=routed.live_attempted,
                live_accepted=routed.live_accepted,
                risk_accepted=True,
                risk_reason=risk.reason,
                order_id=routed.order_id or None,
                client_order_id=client_order_id,
                skipped=False,
                skip_reason=routed.reason,
            )
        )

    return PortfolioRebalanceResponse(
        allocation=allocation,
        target_positions=target_positions,
        orders=orders,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


def resolve_active_strategy_version(strategy_id: str) -> tuple[str | None, str | None]:
    registry_url = os.getenv("STRATEGY_REGISTRY_URL", "http://127.0.0.1:8008").rstrip("/")
    timeout = float(os.getenv("STRATEGY_REGISTRY_TIMEOUT_SEC", "1.5"))
    try:
        active_req = Request(
            f"{registry_url}/strategy/active-versions/{strategy_id}",
            headers={"Accept": "application/json"},
        )
        with urlopen(active_req, timeout=timeout) as response:
            active_payload = json.loads(response.read().decode("utf-8"))
        version = str(active_payload.get("active_version", "")).strip()
        if not version:
            return None, None

        detail_req = Request(
            f"{registry_url}/strategy/versions/{strategy_id}/{version}",
            headers={"Accept": "application/json"},
        )
        with urlopen(detail_req, timeout=timeout) as response:
            detail_payload = json.loads(response.read().decode("utf-8"))
        data_version = str(detail_payload.get("data_version", "")).strip() or None
        return version, data_version
    except Exception:
        return None, None


def resolve_strategy_rollout_config(strategy_id: str) -> tuple[str | None, float | None]:
    registry_url = os.getenv("STRATEGY_REGISTRY_URL", "http://127.0.0.1:8008").rstrip("/")
    timeout = float(os.getenv("STRATEGY_REGISTRY_TIMEOUT_SEC", "1.5"))
    try:
        req = Request(
            f"{registry_url}/strategy/rollout/{strategy_id}",
            headers={"Accept": "application/json"},
        )
        with urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        mode = str(payload.get("mode", "")).strip() or None
        raw_ratio = payload.get("canary_ratio")
        ratio = float(raw_ratio) if raw_ratio is not None else None
        return mode, ratio
    except Exception:
        return None, None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "strategy-engine"}


@app.post("/signals/generate", response_model=SignalResponse)
def generate_signals(payload: SignalRequest) -> SignalResponse:
    strategy_version, data_version = resolve_active_strategy_version(payload.strategy_id)
    rollout_mode, canary_ratio = resolve_strategy_rollout_config(payload.strategy_id)
    # Placeholder logic: always produce HOLD signals with neutral score.
    signals = [
        SignalItem(symbol=symbol, score=0.0, action="HOLD") for symbol in payload.symbols
    ]
    as_of = datetime.now(timezone.utc).isoformat()
    return SignalResponse(
        strategy_id=payload.strategy_id,
        strategy_version=strategy_version,
        data_version=data_version,
        rollout_mode=rollout_mode,
        canary_ratio=canary_ratio,
        as_of=as_of,
        signals=signals,
    )


@app.post("/portfolio/allocate", response_model=PortfolioAllocateResponse)
def allocate_portfolio(payload: PortfolioAllocateRequest) -> PortfolioAllocateResponse:
    try:
        return _allocate_by_score(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/portfolio/rebalance", response_model=PortfolioRebalanceResponse)
def rebalance_portfolio(payload: PortfolioRebalanceRequest) -> PortfolioRebalanceResponse:
    try:
        return _execute_rebalance(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False)

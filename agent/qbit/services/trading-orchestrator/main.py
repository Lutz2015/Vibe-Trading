from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="trading-orchestrator", version="0.1.0")

_qbit_root = Path(__file__).resolve().parents[2]
_default_config_path = Path(
    os.getenv("AUTO_TRADING_CONFIG_PATH", str(_qbit_root / "configs" / "auto-trading.yaml"))
)
_state_path = Path(
    os.getenv(
        "AUTO_TRADING_STATE_PATH",
        str(Path.home() / ".person-trading" / "qbit" / "automation-state.json"),
    )
)

TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))

_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_scheduler_running = False

QuoteGetter = Callable[[list[str]], dict[str, Any]]
AllocateFn = Callable[[dict[str, Any]], Any]
ExecuteRebalanceFn = Callable[[dict[str, Any]], Any]
LedgerSnapshotFn = Callable[[dict[str, float]], Any]
EnsureLedgerFn = Callable[[float], None]
ApplyLedgerFillFn = Callable[[dict[str, Any]], Any]
FillOrderFn = Callable[[str, dict[str, Any]], Any]
IngestMonitoringFn = Callable[[dict[str, Any]], None]

_get_quotes: QuoteGetter | None = None
_allocate_portfolio: AllocateFn | None = None
_execute_rebalance: ExecuteRebalanceFn | None = None
_get_ledger_snapshot: LedgerSnapshotFn | None = None
_ensure_ledger: EnsureLedgerFn | None = None
_apply_ledger_fill: ApplyLedgerFillFn | None = None
_fill_order: FillOrderFn | None = None
_ingest_monitoring: IngestMonitoringFn | None = None


class StrategyConfigView(BaseModel):
    strategy_id: str
    enabled: bool = True
    rule: str
    top_n: int = 5
    universe_size: int = 0
    mode: str = "momentum"
    max_weight: float | None = None
    expected_return_pct: float | None = None


class StrategyPatchRequest(BaseModel):
    enabled: bool | None = None
    top_n: int | None = Field(default=None, ge=1, le=50)
    mode: str | None = None


class StrategySelectRequest(BaseModel):
    strategy_ids: list[str]


class AutomationStatus(BaseModel):
    enabled: bool
    scheduler_running: bool
    mode: str
    execution_mode: str
    poll_interval_sec: int
    last_run_at: str | None
    last_rebalance_date: str | None
    last_run_status: str | None
    last_run_detail: str | None
    strategies: list[StrategyConfigView] = []
    trading_hours: dict[str, str] = {}
    rebalance_window: dict[str, Any] = {}
    initial_cash: float | None = None


class RunCycleRequest(BaseModel):
    force: bool = False
    rebalance: bool | None = None


class OrderDetail(BaseModel):
    symbol: str
    name: str | None = None
    side: str
    quantity: int
    price: float
    strategy_id: str | None = None
    status: str = "skipped"
    skip_reason: str | None = None


class StrategyRunDetail(BaseModel):
    strategy_id: str
    rule: str
    picked: list[str] = []


class RunCycleResponse(BaseModel):
    skipped: bool
    skip_reason: str = ""
    rebalance_executed: bool = False
    orders_submitted: int = 0
    orders_filled: int = 0
    as_of: str
    message: str = ""
    strategies: list[StrategyRunDetail] = []
    orders: list[OrderDetail] = []


def wire_dependencies(
    *,
    get_quotes: QuoteGetter,
    allocate_portfolio: AllocateFn,
    execute_rebalance: ExecuteRebalanceFn,
    get_ledger_snapshot: LedgerSnapshotFn,
    ensure_ledger: EnsureLedgerFn,
    apply_ledger_fill: ApplyLedgerFillFn,
    fill_order: FillOrderFn,
    ingest_monitoring: IngestMonitoringFn | None = None,
) -> None:
    global _get_quotes, _allocate_portfolio, _execute_rebalance
    global _get_ledger_snapshot, _ensure_ledger, _apply_ledger_fill, _fill_order, _ingest_monitoring
    _get_quotes = get_quotes
    _allocate_portfolio = allocate_portfolio
    _execute_rebalance = execute_rebalance
    _get_ledger_snapshot = get_ledger_snapshot
    _ensure_ledger = ensure_ledger
    _apply_ledger_fill = apply_ledger_fill
    _fill_order = fill_order
    _ingest_monitoring = ingest_monitoring


def _now_local() -> datetime:
    return datetime.now(TZ)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config() -> dict[str, Any]:
    config_path = Path(os.getenv("AUTO_TRADING_CONFIG_PATH", str(_default_config_path)))
    if not config_path.exists():
        raise FileNotFoundError(f"auto trading config not found: {config_path}")
    return _load_yaml(config_path)


def save_config(config: dict[str, Any]) -> None:
    config_path = Path(os.getenv("AUTO_TRADING_CONFIG_PATH", str(_default_config_path)))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_state() -> dict[str, Any]:
    if not _state_path.exists():
        return {}
    raw = _state_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def save_state(state: dict[str, Any]) -> None:
    _state_path.parent.mkdir(parents=True, exist_ok=True)
    _state_path.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def is_trading_weekday(day: date) -> bool:
    return day.weekday() < 5


def parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":", 1)
    return int(hour_str), int(minute_str)


def is_within_trading_hours(config: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _now_local()
    hours = config.get("trading_hours", {})
    start_h, start_m = parse_hhmm(str(hours.get("start", "09:30")))
    end_h, end_m = parse_hhmm(str(hours.get("end", "15:00")))
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return start <= now <= end


def is_rebalance_window(config: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _now_local()
    schedule = config.get("rebalance", {})
    target_h, target_m = parse_hhmm(str(schedule.get("time_local", "14:50")))
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    window_minutes = int(schedule.get("window_minutes", 30))
    return target <= now <= target + timedelta(minutes=window_minutes)


def should_rebalance_today(config: dict[str, Any], state: dict[str, Any], force: bool) -> bool:
    if force:
        return True
    today = _now_local().date().isoformat()
    if state.get("last_rebalance_date") == today:
        return False
    every_days = int(config.get("rebalance", {}).get("rebalance_every_trading_days", 15))
    last_date_str = state.get("last_rebalance_trading_day")
    if not last_date_str:
        return True
    last_day = date.fromisoformat(str(last_date_str))
    trading_days = 0
    cursor = last_day + timedelta(days=1)
    today_date = _now_local().date()
    while cursor <= today_date:
        if is_trading_weekday(cursor):
            trading_days += 1
        cursor += timedelta(days=1)
    return trading_days >= every_days


def _score_symbol(quote: Any) -> float:
    prev_close = float(getattr(quote, "prev_close", 0.0) or 0.0)
    price = float(getattr(quote, "price", 0.0) or 0.0)
    if prev_close <= 0 or price <= 0:
        return 0.0
    return (price / prev_close) - 1.0


def _strategy_mode(strategy_cfg: dict[str, Any]) -> str:
    params = strategy_cfg.get("params", {}) or {}
    mode = str(params.get("mode") or strategy_cfg.get("mode") or "momentum").strip().lower()
    if mode in {"momentum", "mean_reversion", "equal_weight"}:
        return mode
    return "momentum"


def strategy_rule_text(strategy_cfg: dict[str, Any]) -> str:
    params = strategy_cfg.get("params", {}) or {}
    top_n = int(params.get("top_n", 5))
    universe_size = len(params.get("universe_symbols", []) or [])
    mode = _strategy_mode(strategy_cfg)
    if mode == "mean_reversion":
        return (
            f"均值回归：在 {universe_size} 只股票池中按日内涨幅升序（跌得多的靠前），"
            f"取前 {top_n} 只等权买入，再平衡窗口拉回目标权重"
        )
    if mode == "equal_weight":
        return (
            f"等权配置：股票池 {universe_size} 只全部纳入，"
            f"按等权（上限 top_n={top_n} 只时取前 N）建仓，再平衡维持权重"
        )
    return (
        f"日内动量：在 {universe_size} 只股票池中按 (现价/昨收-1) 降序，"
        f"取前 {top_n} 只等权买入，再平衡窗口拉回目标权重"
    )


def build_strategy_targets(
    strategy_cfg: dict[str, Any],
    get_quotes: QuoteGetter,
) -> list[dict[str, Any]]:
    params = strategy_cfg.get("params", {})
    universe = [str(item) for item in params.get("universe_symbols", [])]
    top_n = int(params.get("top_n", 5))
    mode = _strategy_mode(strategy_cfg)
    if not universe:
        raise ValueError(f"universe_symbols missing for strategy {strategy_cfg.get('strategy_id')}")
    quotes = get_quotes(universe)
    scored = [(symbol, _score_symbol(quotes[symbol])) for symbol in universe if symbol in quotes]
    if not scored:
        raise ValueError("no_symbols_with_quotes")
    if mode == "equal_weight":
        picked = [symbol for symbol, _ in scored[:top_n]] if top_n > 0 else [symbol for symbol, _ in scored]
    elif mode == "mean_reversion":
        ranked = sorted(scored, key=lambda item: item[1])
        picked = [symbol for symbol, _ in ranked[:top_n]]
    else:
        ranked = sorted(scored, key=lambda item: item[1], reverse=True)
        picked = [symbol for symbol, _ in ranked[:top_n]]
    if not picked:
        raise ValueError("no_symbols_with_quotes")
    weight = round(1.0 / len(picked), 6)
    return [{"symbol": symbol, "weight": weight} for symbol in picked]


def run_cycle(force: bool = False, rebalance: bool | None = None) -> RunCycleResponse:
    if (
        _get_quotes is None
        or _allocate_portfolio is None
        or _execute_rebalance is None
        or _get_ledger_snapshot is None
        or _ensure_ledger is None
        or _apply_ledger_fill is None
        or _fill_order is None
    ):
        raise RuntimeError("orchestrator dependencies are not wired")

    get_quotes = _get_quotes
    allocate_portfolio = _allocate_portfolio
    execute_rebalance = _execute_rebalance
    get_ledger_snapshot = _get_ledger_snapshot
    ensure_ledger = _ensure_ledger
    apply_ledger_fill = _apply_ledger_fill
    fill_order = _fill_order
    ingest_monitoring = _ingest_monitoring

    config = load_config()
    if not bool(config.get("enabled", False)) and not force:
        return RunCycleResponse(skipped=True, skip_reason="automation_disabled", as_of=_now_local().isoformat())

    now = _now_local()
    if not is_trading_weekday(now.date()) and not force:
        return RunCycleResponse(skipped=True, skip_reason="non_trading_weekday", as_of=now.isoformat())
    if not is_within_trading_hours(config, now) and not force:
        return RunCycleResponse(skipped=True, skip_reason="outside_trading_hours", as_of=now.isoformat())

    state = load_state()
    do_rebalance = rebalance if rebalance is not None else is_rebalance_window(config, now)
    if do_rebalance and not should_rebalance_today(config, state, force=force):
        do_rebalance = False

    if not do_rebalance and not force:
        return RunCycleResponse(skipped=True, skip_reason="not_rebalance_window", as_of=now.isoformat())

    portfolio_cfg = config.get("portfolio", {})
    initial_cash = float(portfolio_cfg.get("initial_cash", 1_000_000.0))
    fee_bps = float(portfolio_cfg.get("fee_bps", 3.0))
    ensure_ledger(initial_cash)

    strategy_cfgs = [item for item in config.get("strategies", []) if bool(item.get("enabled", True))]
    if not strategy_cfgs:
        return RunCycleResponse(skipped=True, skip_reason="no_enabled_strategies", as_of=now.isoformat())

    allocation_items: list[dict[str, Any]] = []
    strategy_targets: list[dict[str, Any]] = []
    strategy_details: list[StrategyRunDetail] = []
    symbol_strategy: dict[str, str] = {}
    for strategy_cfg in strategy_cfgs:
        strategy_id = str(strategy_cfg.get("strategy_id", "")).strip()
        if not strategy_id:
            continue
        allocation_items.append(
            {
                "strategy_id": strategy_id,
                "expected_return_pct": float(strategy_cfg.get("expected_return_pct", 10.0)),
                "risk_volatility_pct": float(strategy_cfg.get("risk_volatility_pct", 8.0)),
                "max_weight": strategy_cfg.get("max_weight"),
            }
        )
        symbols = build_strategy_targets(strategy_cfg, get_quotes)
        strategy_targets.append({"strategy_id": strategy_id, "symbols": symbols})
        picked = [str(item["symbol"]) for item in symbols]
        strategy_details.append(
            StrategyRunDetail(
                strategy_id=strategy_id,
                rule=strategy_rule_text(strategy_cfg),
                picked=picked,
            )
        )
        for symbol in picked:
            symbol_strategy[symbol] = strategy_id

    allocation_req = {
        "items": allocation_items,
        "total_exposure": float(portfolio_cfg.get("total_exposure", 1.0)),
        "max_single_weight": float(portfolio_cfg.get("max_single_weight", 0.5)),
        "risk_aversion": float(portfolio_cfg.get("risk_aversion", 1.0)),
    }
    allocate_portfolio(allocation_req)

    all_symbols = sorted(
        {pos.symbol for pos in get_ledger_snapshot({}).positions}
        | {
            target["symbol"]
            for target_bundle in strategy_targets
            for target in target_bundle["symbols"]
        }
    )
    quotes = get_quotes(all_symbols)
    prices = {symbol: float(getattr(quotes[symbol], "price", 0.0)) for symbol in quotes}
    snapshot = get_ledger_snapshot(prices)
    portfolio_value = max(snapshot.portfolio_value, snapshot.cash, initial_cash)

    rebalance_id = f"auto_{now.strftime('%Y%m%d_%H%M%S')}"
    trace_id = f"trace_{rebalance_id}"
    rebalance_req = {
        "allocation": allocation_req,
        "strategy_targets": strategy_targets,
        "portfolio_value": portfolio_value,
        "prices": prices,
        "current_positions": [{"symbol": pos.symbol, "quantity": pos.quantity} for pos in snapshot.positions],
        "execution_mode": str(config.get("execution_mode", config.get("mode", "paper"))),
        "min_trade_lot": int(portfolio_cfg.get("min_trade_lot", 100)),
        "trace_id": trace_id,
        "rebalance_id": rebalance_id,
    }
    rebalance_resp = execute_rebalance(rebalance_req)

    orders_submitted = 0
    orders_filled = 0
    order_details: list[OrderDetail] = []
    for order in rebalance_resp.orders:
        quote = quotes.get(order.symbol)
        name = getattr(quote, "name", None) if quote is not None else None
        strategy_id = symbol_strategy.get(order.symbol)
        if order.skipped:
            order_details.append(
                OrderDetail(
                    symbol=order.symbol,
                    name=name,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.limit_price,
                    strategy_id=strategy_id,
                    status="skipped",
                    skip_reason=getattr(order, "skip_reason", None) or "skipped",
                )
            )
            continue
        orders_submitted += 1
        if not order.order_id:
            order_details.append(
                OrderDetail(
                    symbol=order.symbol,
                    name=name,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.limit_price,
                    strategy_id=strategy_id,
                    status="failed",
                    skip_reason="missing_order_id",
                )
            )
            continue
        fee = round(order.quantity * order.limit_price * fee_bps / 10_000.0, 4)
        try:
            fill_order(order.order_id, {"fill_quantity": order.quantity, "fill_price": order.limit_price})
            apply_ledger_fill(
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "price": order.limit_price,
                    "fee": fee,
                    "name": name,
                    "strategy_id": strategy_id,
                }
            )
            orders_filled += 1
            order_details.append(
                OrderDetail(
                    symbol=order.symbol,
                    name=name,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.limit_price,
                    strategy_id=strategy_id,
                    status="filled",
                )
            )
            if ingest_monitoring is not None:
                ingest_monitoring(
                    {
                        "events": [
                            {
                                "event_type": "ORDER_SUCCESS",
                                "latency_ms": 0.0,
                                "symbol": order.symbol,
                                "trace_id": trace_id,
                            }
                        ]
                    }
                )
        except Exception:
            order_details.append(
                OrderDetail(
                    symbol=order.symbol,
                    name=name,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.limit_price,
                    strategy_id=strategy_id,
                    status="failed",
                    skip_reason="fill_failed",
                )
            )
            if ingest_monitoring is not None:
                ingest_monitoring(
                    {
                        "events": [
                            {
                                "event_type": "ORDER_FAIL",
                                "latency_ms": 0.0,
                                "symbol": order.symbol,
                                "trace_id": trace_id,
                            }
                        ]
                    }
                )

    strategy_ids = [item.strategy_id for item in strategy_details]
    strategy_label = "、".join(strategy_ids) if strategy_ids else "无策略"
    message = (
        f"策略 {strategy_label}：提交 {orders_submitted} 笔，成交 {orders_filled} 笔"
        f"（{rebalance_id}）"
    )
    state["last_run_at"] = now.isoformat()
    state["last_run_status"] = "ok"
    state["last_run_detail"] = (
        f"submitted={orders_submitted}, filled={orders_filled}, rebalance_id={rebalance_id}"
    )
    if orders_submitted > 0 or force:
        state["last_rebalance_date"] = now.date().isoformat()
        state["last_rebalance_trading_day"] = now.date().isoformat()
    save_state(state)

    return RunCycleResponse(
        skipped=False,
        rebalance_executed=True,
        orders_submitted=orders_submitted,
        orders_filled=orders_filled,
        as_of=now.isoformat(),
        message=message,
        strategies=strategy_details,
        orders=order_details,
    )


def _scheduler_loop() -> None:
    while not _scheduler_stop.is_set():
        try:
            with _lock:
                run_cycle(force=False)
        except Exception as exc:
            state = load_state()
            state["last_run_at"] = _now_local().isoformat()
            state["last_run_status"] = "error"
            state["last_run_detail"] = str(exc)
            save_state(state)
        config = load_config()
        interval = int(config.get("poll_interval_sec", 60))
        _scheduler_stop.wait(max(5, interval))


def start_scheduler() -> None:
    global _scheduler_thread, _scheduler_running
    with _lock:
        if _scheduler_running:
            return
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(target=_scheduler_loop, name="auto-trading-scheduler", daemon=True)
        _scheduler_thread.start()
        _scheduler_running = True


def stop_scheduler() -> None:
    global _scheduler_running
    with _lock:
        _scheduler_stop.set()
        _scheduler_running = False


def build_status() -> AutomationStatus:
    config = load_config()
    state = load_state()
    strategy_views: list[StrategyConfigView] = []
    for item in config.get("strategies", []) or []:
        params = item.get("params", {}) or {}
        strategy_views.append(
            StrategyConfigView(
                strategy_id=str(item.get("strategy_id", "")).strip() or "unknown",
                enabled=bool(item.get("enabled", True)),
                rule=strategy_rule_text(item),
                top_n=int(params.get("top_n", 5)),
                universe_size=len(params.get("universe_symbols", []) or []),
                mode=_strategy_mode(item),
                max_weight=item.get("max_weight"),
                expected_return_pct=item.get("expected_return_pct"),
            )
        )
    portfolio_cfg = config.get("portfolio", {}) or {}
    return AutomationStatus(
        enabled=bool(config.get("enabled", False)),
        scheduler_running=_scheduler_running,
        mode=str(config.get("mode", "paper")),
        execution_mode=str(config.get("execution_mode", config.get("mode", "paper"))),
        poll_interval_sec=int(config.get("poll_interval_sec", 60)),
        last_run_at=state.get("last_run_at"),
        last_rebalance_date=state.get("last_rebalance_date"),
        last_run_status=state.get("last_run_status"),
        last_run_detail=state.get("last_run_detail"),
        strategies=strategy_views,
        trading_hours={
            "start": str((config.get("trading_hours") or {}).get("start", "09:30")),
            "end": str((config.get("trading_hours") or {}).get("end", "15:00")),
        },
        rebalance_window={
            "time_local": str((config.get("rebalance") or {}).get("time_local", "14:50")),
            "window_minutes": int((config.get("rebalance") or {}).get("window_minutes", 30)),
            "every_trading_days": int(
                (config.get("rebalance") or {}).get("rebalance_every_trading_days", 15)
            ),
        },
        initial_cash=float(portfolio_cfg.get("initial_cash", 1_000_000.0)),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trading-orchestrator"}


@app.get("/automation/status", response_model=AutomationStatus)
def automation_status() -> AutomationStatus:
    try:
        return build_status()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/automation/run-cycle", response_model=RunCycleResponse)
def automation_run_cycle(payload: RunCycleRequest) -> RunCycleResponse:
    try:
        return run_cycle(force=payload.force, rebalance=payload.rebalance)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/automation/start")
def automation_start() -> dict[str, str]:
    start_scheduler()
    return {"status": "started"}


@app.post("/automation/stop")
def automation_stop() -> dict[str, str]:
    stop_scheduler()
    return {"status": "stopped"}


@app.patch("/automation/strategies/{strategy_id}", response_model=AutomationStatus)
def patch_strategy(strategy_id: str, payload: StrategyPatchRequest) -> AutomationStatus:
    """Enable/disable or retune one strategy; persists to auto-trading.yaml."""
    config = load_config()
    items = config.get("strategies", []) or []
    updated = False
    for item in items:
        if str(item.get("strategy_id", "")).strip() != strategy_id:
            continue
        if payload.enabled is not None:
            item["enabled"] = bool(payload.enabled)
        params = item.setdefault("params", {})
        if payload.top_n is not None:
            params["top_n"] = int(payload.top_n)
        if payload.mode is not None:
            mode = str(payload.mode).strip().lower()
            if mode not in {"momentum", "mean_reversion", "equal_weight"}:
                raise HTTPException(status_code=400, detail=f"unsupported mode: {payload.mode}")
            params["mode"] = mode
        updated = True
        break
    if not updated:
        raise HTTPException(status_code=404, detail=f"strategy not found: {strategy_id}")
    config["strategies"] = items
    save_config(config)
    return build_status()


@app.post("/automation/strategies/select", response_model=AutomationStatus)
def select_strategies(payload: StrategySelectRequest) -> AutomationStatus:
    """Exclusive-select: only the listed strategy ids stay enabled."""
    wanted = {sid.strip() for sid in payload.strategy_ids if sid and sid.strip()}
    config = load_config()
    items = config.get("strategies", []) or []
    seen: set[str] = set()
    for item in items:
        sid = str(item.get("strategy_id", "")).strip()
        if not sid:
            continue
        item["enabled"] = sid in wanted
        if sid in wanted:
            seen.add(sid)
    missing = sorted(wanted - seen)
    if missing:
        raise HTTPException(status_code=404, detail=f"unknown strategies: {', '.join(missing)}")
    config["strategies"] = items
    save_config(config)
    return build_status()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8010, reload=False)

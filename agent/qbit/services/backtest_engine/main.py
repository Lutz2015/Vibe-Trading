from __future__ import annotations

from dataclasses import dataclass
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

try:
    import jqdatasdk as jq
except ImportError:  # optional provider
    jq = None

app = FastAPI(title="backtest-engine", version="0.1.0")


class BarInput(BaseModel):
    trade_date: str
    close: float = Field(gt=0)


class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str
    bars: list[BarInput]
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=3.0, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    short_window: int = Field(default=5, ge=2)
    long_window: int = Field(default=20, ge=3)
    execution_delay_bars: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_windows(self) -> "BacktestRequest":
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be less than long_window")
        if len(self.bars) < self.long_window:
            raise ValueError("bars length must be >= long_window")
        return self


class Trade(BaseModel):
    trade_date: str
    side: str
    quantity: int
    price: float
    fee: float
    cash_after: float
    position_after: int


class BacktestMetrics(BaseModel):
    total_return_pct: float
    max_drawdown_pct: float
    annualized_volatility_pct: float
    sharpe_like: float
    win_rate_pct: float
    trade_count: int


class BacktestResponse(BaseModel):
    strategy_id: str
    symbol: str
    initial_cash: float
    final_equity: float
    metrics: BacktestMetrics
    trades: list[Trade]


class MultiFactorRowInput(BaseModel):
    trade_date: str
    symbol: str = Field(min_length=1)
    close: float = Field(gt=0)
    factors: dict[str, float] = Field(default_factory=dict)


class MultiFactorBacktestRequest(BaseModel):
    strategy_id: str
    rows: list[MultiFactorRowInput]
    factor_directions: dict[str, int]
    factor_weights: dict[str, float] = Field(default_factory=dict)
    rebalance_every: int = Field(default=15, ge=1)
    top_n: int = Field(default=20, ge=1)
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    fee_bps: float = Field(default=3.0, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    execution_delay_bars: int = Field(default=1, ge=1)
    min_trade_lot: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_multifactor_inputs(self) -> "MultiFactorBacktestRequest":
        if len(self.rows) < 2:
            raise ValueError("rows length must be >= 2")
        if not self.factor_directions:
            raise ValueError("factor_directions must not be empty")
        for factor, direction in self.factor_directions.items():
            if direction not in {-1, 1}:
                raise ValueError(f"factor {factor} direction must be -1 or 1")
        unique_dates = {row.trade_date for row in self.rows}
        if len(unique_dates) < 2:
            raise ValueError("rows must include at least 2 trade dates")
        return self


class MultiFactorTrade(BaseModel):
    trade_date: str
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float
    cash_after: float
    position_after_symbol: int


class MultiFactorBacktestResponse(BaseModel):
    strategy_id: str
    initial_cash: float
    final_equity: float
    metrics: BacktestMetrics
    rebalance_dates: list[str]
    holdings: dict[str, int]
    trades: list[MultiFactorTrade]


class JoinquantMultiFactorBacktestRequest(BaseModel):
    strategy_id: str = "jq_multifactor_v1"
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    index_symbol: str = "000300.XSHG"
    factors: list[str] = Field(default_factory=lambda: ["market_cap", "roe"])
    factor_directions: dict[str, int] = Field(
        default_factory=lambda: {"market_cap": 1, "roe": -1}
    )
    factor_weights: dict[str, float] = Field(default_factory=dict)
    rebalance_every: int = Field(default=15, ge=1)
    top_n: int = Field(default=20, ge=1)
    initial_cash: float = Field(default=2_000_000.0, gt=0)
    fee_bps: float = Field(default=3.0, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    execution_delay_bars: int = Field(default=1, ge=1)
    min_trade_lot: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_joinquant_request(self) -> "JoinquantMultiFactorBacktestRequest":
        if not self.factors:
            raise ValueError("factors must not be empty")
        if set(self.factors) != set(self.factor_directions.keys()):
            raise ValueError("factor_directions must match factors")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        for factor, direction in self.factor_directions.items():
            if direction not in {-1, 1}:
                raise ValueError(f"factor {factor} direction must be -1 or 1")
        return self


class JoinquantTemplateBacktestRequest(BaseModel):
    template_id: str = Field(description="test1_multifactor | test2_futures_spread")
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    initial_cash: float = Field(default=2_000_000.0, gt=0)

    # test1 (A-share multifactor) params
    index_symbol: str = "000300.XSHG"
    factors: list[str] = Field(default_factory=lambda: ["market_cap", "roe"])
    factor_directions: dict[str, int] = Field(
        default_factory=lambda: {"market_cap": 1, "roe": -1}
    )
    factor_weights: dict[str, float] = Field(default_factory=dict)
    rebalance_every: int = Field(default=15, ge=1)
    top_n: int = Field(default=20, ge=1)

    # common trading params
    fee_bps: float = Field(default=3.0, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    execution_delay_bars: int = Field(default=1, ge=1)
    min_trade_lot: int = Field(default=100, ge=1)

    # test2 (futures spread) params
    yb: int = Field(default=63, ge=20)
    z_open_threshold: float = 1.0
    z_close_threshold: float = 1.0

    @model_validator(mode="after")
    def validate_template_request(self) -> "JoinquantTemplateBacktestRequest":
        if self.template_id not in {"test1_multifactor", "test2_futures_spread"}:
            raise ValueError("template_id must be test1_multifactor or test2_futures_spread")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        if set(self.factors) != set(self.factor_directions.keys()):
            raise ValueError("factor_directions must match factors")
        for factor, direction in self.factor_directions.items():
            if direction not in {-1, 1}:
                raise ValueError(f"factor {factor} direction must be -1 or 1")
        return self


class PortfolioTemplateItem(BaseModel):
    strategy_id: str
    template_id: str = Field(description="test1_multifactor | test2_futures_spread")
    expected_return_pct: float
    risk_volatility_pct: float = 0.0
    weight_cap: float | None = None

    # template params override
    index_symbol: str = "000300.XSHG"
    factors: list[str] = Field(default_factory=lambda: ["market_cap", "roe"])
    factor_directions: dict[str, int] = Field(default_factory=lambda: {"market_cap": 1, "roe": -1})
    factor_weights: dict[str, float] = Field(default_factory=dict)
    rebalance_every: int = Field(default=15, ge=1)
    top_n: int = Field(default=20, ge=1)
    yb: int = Field(default=63, ge=20)
    z_open_threshold: float = 1.0
    z_close_threshold: float = 1.0


class PortfolioTemplateBacktestRequest(BaseModel):
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    initial_cash: float = Field(default=2_000_000.0, gt=0)
    fee_bps: float = Field(default=3.0, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    execution_delay_bars: int = Field(default=1, ge=1)
    min_trade_lot: int = Field(default=100, ge=1)
    total_exposure: float = 1.0
    max_single_weight: float = 0.7
    risk_aversion: float = 1.0
    items: list[PortfolioTemplateItem]

    @model_validator(mode="after")
    def validate_portfolio_templates(self) -> "PortfolioTemplateBacktestRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        if not self.items:
            raise ValueError("items must not be empty")
        for item in self.items:
            if item.template_id not in {"test1_multifactor", "test2_futures_spread"}:
                raise ValueError("template_id must be test1_multifactor or test2_futures_spread")
        return self


class PortfolioTemplateComponent(BaseModel):
    strategy_id: str
    template_id: str
    weight: float
    score: float
    component_return_pct: float
    component_final_equity: float
    trade_count: int


class PortfolioTemplateBacktestResponse(BaseModel):
    initial_cash: float
    final_equity: float
    total_return_pct: float
    weighted_return_pct: float
    components: list[PortfolioTemplateComponent]
    unallocated_weight: float
    as_of: str


def _jq_to_std_symbol(symbol: str) -> str:
    if symbol.endswith(".XSHG"):
        return f"{symbol[:6]}.SH"
    if symbol.endswith(".XSHE"):
        return f"{symbol[:6]}.SZ"
    return symbol


def _to_date_str(value: Any) -> str:
    if hasattr(value, "date"):
        return str(value.date())
    text = str(value)
    if len(text) >= 10:
        return text[:10]
    return text


def _ensure_joinquant_auth() -> None:
    if jq is None:
        raise RuntimeError("jqdatasdk is not installed")
    username = os.getenv("JQ_USERNAME", "").strip()
    password = os.getenv("JQ_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("missing JQ_USERNAME/JQ_PASSWORD environment variables")
    jq.auth(username, password)


@dataclass
class EquityPoint:
    trade_date: str
    equity: float


def rolling_mean(values: list[float], window: int, end_idx: int) -> float:
    start = end_idx - window + 1
    segment = values[start : end_idx + 1]
    return sum(segment) / window


def max_drawdown_pct(equity_curve: list[EquityPoint], initial_cash: float) -> float:
    peak = initial_cash
    max_drawdown = 0.0
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        if peak > 0:
            drawdown = (peak - point.equity) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
    return max_drawdown * 100.0


def daily_returns(equity_curve: list[EquityPoint]) -> list[float]:
    returns: list[float] = []
    for idx in range(1, len(equity_curve)):
        prev = equity_curve[idx - 1].equity
        curr = equity_curve[idx].equity
        if prev <= 0:
            returns.append(0.0)
            continue
        returns.append((curr - prev) / prev)
    return returns


def annualized_volatility_pct(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean: float = float(sum(returns) / len(returns))
    variance: float = float(sum((x - mean) ** 2 for x in returns) / (len(returns) - 1))
    daily_vol: float = math.sqrt(variance)
    return float(daily_vol * math.sqrt(252.0) * 100.0)


def sharpe_like_ratio(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean: float = float(sum(returns) / len(returns))
    variance: float = float(sum((x - mean) ** 2 for x in returns) / (len(returns) - 1))
    std: float = math.sqrt(variance)
    if std == 0:
        return 0.0
    return float((mean / std) * math.sqrt(252.0))


def compute_win_rate(trades: list[Trade]) -> float:
    closed_trade_pnls: list[float] = []
    open_lot_cost = 0.0
    open_lot_qty = 0

    for trade in trades:
        if trade.side == "BUY":
            open_lot_cost += (trade.price * trade.quantity) + trade.fee
            open_lot_qty += trade.quantity
            continue

        if open_lot_qty <= 0:
            continue
        avg_cost = open_lot_cost / open_lot_qty
        pnl = (trade.price * trade.quantity) - trade.fee - (avg_cost * trade.quantity)
        closed_trade_pnls.append(pnl)
        open_lot_qty -= trade.quantity
        open_lot_cost -= avg_cost * trade.quantity

    if not closed_trade_pnls:
        return 0.0
    wins = sum(1 for pnl in closed_trade_pnls if pnl > 0)
    return (wins / len(closed_trade_pnls)) * 100.0


def compute_multifactor_win_rate(trades: list[MultiFactorTrade]) -> float:
    open_cost: dict[str, float] = {}
    open_qty: dict[str, int] = {}
    closed_trade_pnls: list[float] = []

    for trade in trades:
        if trade.side == "BUY":
            open_cost[trade.symbol] = open_cost.get(trade.symbol, 0.0) + (
                (trade.price * trade.quantity) + trade.fee
            )
            open_qty[trade.symbol] = open_qty.get(trade.symbol, 0) + trade.quantity
            continue

        qty = open_qty.get(trade.symbol, 0)
        if qty <= 0:
            continue
        cost = open_cost.get(trade.symbol, 0.0)
        avg_cost = cost / qty
        pnl = (trade.price * trade.quantity) - trade.fee - (avg_cost * trade.quantity)
        closed_trade_pnls.append(pnl)
        open_qty[trade.symbol] = max(0, qty - trade.quantity)
        open_cost[trade.symbol] = max(0.0, cost - (avg_cost * trade.quantity))

    if not closed_trade_pnls:
        return 0.0
    wins = sum(1 for pnl in closed_trade_pnls if pnl > 0)
    return (wins / len(closed_trade_pnls)) * 100.0


def build_joinquant_multifactor_rows(
    payload: JoinquantMultiFactorBacktestRequest,
) -> list[MultiFactorRowInput]:
    _ensure_joinquant_auth()

    # Import inside function so tests can patch/mimic jq gracefully.
    from jqdatasdk import indicator, query, valuation  # type: ignore

    universe_jq = list(jq.get_index_stocks(payload.index_symbol, date=payload.end_date))
    if not universe_jq:
        raise ValueError("empty_universe_from_index")

    price_frame = jq.get_price(
        universe_jq,
        start_date=payload.start_date,
        end_date=payload.end_date,
        frequency="daily",
        fields=["close"],
        panel=False,
    )
    if price_frame is None or len(price_frame) == 0:
        raise ValueError("empty_price_data_from_joinquant")

    rows_by_key: dict[tuple[str, str], MultiFactorRowInput] = {}

    # panel=False should return columns: time, code, close
    for _, row in price_frame.iterrows():
        trade_date = _to_date_str(row["time"])
        symbol = _jq_to_std_symbol(str(row["code"]))
        close = float(row["close"])
        if not math.isfinite(close) or close <= 0:
            continue
        rows_by_key[(trade_date, symbol)] = MultiFactorRowInput(
            trade_date=trade_date,
            symbol=symbol,
            close=close,
            factors={},
        )

    trade_days = list(jq.get_trade_days(start_date=payload.start_date, end_date=payload.end_date))
    rebalance_dates = [
        _to_date_str(day) for idx, day in enumerate(trade_days) if idx % payload.rebalance_every == 0
    ]

    if not rebalance_dates:
        raise ValueError("no_rebalance_dates_in_range")

    supported = {"market_cap", "roe"}
    for factor in payload.factors:
        if factor not in supported:
            raise ValueError(f"unsupported_factor: {factor}")

    for rebalance_date in rebalance_dates:
        stocks_on_day = list(jq.get_index_stocks(payload.index_symbol, date=rebalance_date))
        if not stocks_on_day:
            continue
        if set(payload.factors) == {"market_cap", "roe"}:
            q = query(valuation.code, valuation.market_cap, indicator.roe)
        elif payload.factors == ["market_cap"] or set(payload.factors) == {"market_cap"}:
            q = query(valuation.code, valuation.market_cap)
        elif payload.factors == ["roe"] or set(payload.factors) == {"roe"}:
            q = query(valuation.code, indicator.roe)
        else:
            raise ValueError("unsupported_factor_combination")
        q = q.filter(valuation.code.in_(stocks_on_day))
        factors_frame = jq.get_fundamentals(q, date=rebalance_date)
        if factors_frame is None or len(factors_frame) == 0:
            continue
        for _, item in factors_frame.iterrows():
            symbol = _jq_to_std_symbol(str(item["code"]))
            key = (rebalance_date, symbol)
            row = rows_by_key.get(key)
            if row is None:
                continue
            factor_payload: dict[str, float] = {}
            if "market_cap" in payload.factors:
                val = item.get("market_cap")
                if val is not None:
                    factor_payload["market_cap"] = float(val)
            if "roe" in payload.factors:
                val = item.get("roe")
                if val is not None:
                    factor_payload["roe"] = float(val)
            row.factors = factor_payload

    rows = sorted(rows_by_key.values(), key=lambda r: (r.trade_date, r.symbol))
    return rows


def _third_friday(year: int, month: int) -> date:
    first_day = date(year, month, 1)
    month_begin_day = first_day.isoweekday()
    day = 20 - month_begin_day + (7 if month_begin_day > 5 else 0)
    return date(year, month, day)


def _get_current_month_future_symbol(current_day: date, symbol: str = "IF") -> str:
    third_friday = _third_friday(current_day.year, current_day.month)
    target_year = current_day.year
    target_month = current_day.month
    if current_day > third_friday:
        if target_month == 12:
            target_year += 1
            target_month = 1
        else:
            target_month += 1
    return f"{symbol}{str(target_year)[2:]}{target_month:02d}.CCFX"


def _get_next_month_future_symbol(current_day: date, symbol: str = "IF") -> str:
    third_friday = _third_friday(current_day.year, current_day.month)
    month_shift = 1 if current_day <= third_friday else 2
    target_year = current_day.year
    target_month = current_day.month
    for _ in range(month_shift):
        if target_month == 12:
            target_year += 1
            target_month = 1
        else:
            target_month += 1
    return f"{symbol}{str(target_year)[2:]}{target_month:02d}.CCFX"


def _futures_margin_rate(dt: date) -> float:
    if dt < date(2015, 8, 28):
        return 0.12
    return 0.4


def _max_daily_lots(dt: date) -> int:
    if dt < date(2015, 9, 6):
        return 10**9
    return 5


def run_joinquant_test2_futures_spread(
    payload: JoinquantTemplateBacktestRequest,
) -> MultiFactorBacktestResponse:
    _ensure_joinquant_auth()
    start = datetime.strptime(payload.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(payload.end_date, "%Y-%m-%d").date()
    trade_days = [d.date() for d in jq.get_trade_days(start_date=payload.start_date, end_date=payload.end_date)]
    if not trade_days:
        raise ValueError("no_trade_days_in_range")

    contracts: set[str] = set()
    for day in trade_days:
        contracts.add(_get_current_month_future_symbol(day, "IF"))
        contracts.add(_get_next_month_future_symbol(day, "IF"))

    start_fetch = (start - timedelta(days=7)).isoformat()
    frame = jq.get_price(
        list(contracts),
        start_date=start_fetch,
        end_date=payload.end_date,
        frequency="daily",
        fields=["close"],
        panel=False,
    )
    if frame is None or len(frame) == 0:
        raise ValueError("empty_futures_data_from_joinquant")

    close_map: dict[tuple[date, str], float] = {}
    for _, row in frame.iterrows():
        d = datetime.strptime(_to_date_str(row["time"]), "%Y-%m-%d").date()
        close_map[(d, str(row["code"]))] = float(row["close"])

    cash = payload.initial_cash
    multiplier = 300.0
    spread_hist: list[float] = []
    equity_curve: list[EquityPoint] = []
    trades: list[MultiFactorTrade] = []
    rebalance_dates: list[str] = []

    # 0=flat, 1=long near short next, -1=short near long next
    position_side = 0
    lots = 0
    near_symbol = ""
    next_symbol = ""
    near_entry = 0.0
    next_entry = 0.0

    for day in trade_days:
        near = _get_current_month_future_symbol(day, "IF")
        nxt = _get_next_month_future_symbol(day, "IF")
        near_close = close_map.get((day, near))
        next_close = close_map.get((day, nxt))
        if near_close is None or next_close is None:
            equity_curve.append(EquityPoint(trade_date=day.isoformat(), equity=cash))
            continue

        spread = math.log(next_close) - math.log(near_close)
        spread_hist.append(spread)
        window = spread_hist[-payload.yb :]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / max(1, len(window) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        zscore = (spread - mean) / std if std > 0 else 0.0

        if len(window) >= payload.yb:
            rebalance_dates.append(day.isoformat())
            if position_side == 0:
                margin_rate = _futures_margin_rate(day)
                max_lots_by_margin = int((cash / 2.0) / (multiplier * margin_rate * max(near_close, next_close)))
                open_lots = max(0, min(max_lots_by_margin, _max_daily_lots(day)))
                if open_lots > 0:
                    if payload.z_open_threshold < zscore < 2.0:
                        position_side = 1
                    elif -2.0 < zscore < -payload.z_open_threshold:
                        position_side = -1
                    if position_side != 0:
                        lots = open_lots
                        near_symbol = near
                        next_symbol = nxt
                        near_entry = near_close
                        next_entry = next_close
                        # Entry fees for both legs.
                        notional = (near_close + next_close) * multiplier * lots
                        fee = notional * payload.fee_bps / 10_000.0
                        cash -= fee
                        # Represent pair entry using two synthetic legs.
                        trades.append(
                            MultiFactorTrade(
                                trade_date=day.isoformat(),
                                symbol=near_symbol,
                                side="BUY" if position_side == 1 else "SELL",
                                quantity=lots,
                                price=near_close,
                                fee=fee / 2.0,
                                cash_after=cash,
                                position_after_symbol=lots * position_side,
                            )
                        )
                        trades.append(
                            MultiFactorTrade(
                                trade_date=day.isoformat(),
                                symbol=next_symbol,
                                side="SELL" if position_side == 1 else "BUY",
                                quantity=lots,
                                price=next_close,
                                fee=fee / 2.0,
                                cash_after=cash,
                                position_after_symbol=-lots * position_side,
                            )
                        )
            else:
                should_close = abs(zscore) < payload.z_close_threshold or abs(zscore) > 2.0
                if should_close and lots > 0:
                    near_pnl = (near_close - near_entry) * multiplier * lots * position_side
                    next_pnl = (next_entry - next_close) * multiplier * lots * position_side
                    pnl = near_pnl + next_pnl
                    notional = (near_close + next_close) * multiplier * lots
                    fee = notional * payload.fee_bps / 10_000.0
                    cash += pnl - fee
                    trades.append(
                        MultiFactorTrade(
                            trade_date=day.isoformat(),
                            symbol=near_symbol,
                            side="SELL" if position_side == 1 else "BUY",
                            quantity=lots,
                            price=near_close,
                            fee=fee / 2.0,
                            cash_after=cash,
                            position_after_symbol=0,
                        )
                    )
                    trades.append(
                        MultiFactorTrade(
                            trade_date=day.isoformat(),
                            symbol=next_symbol,
                            side="BUY" if position_side == 1 else "SELL",
                            quantity=lots,
                            price=next_close,
                            fee=fee / 2.0,
                            cash_after=cash,
                            position_after_symbol=0,
                        )
                    )
                    position_side = 0
                    lots = 0

        unrealized = 0.0
        if position_side != 0 and lots > 0:
            unrealized += (near_close - near_entry) * multiplier * lots * position_side
            unrealized += (next_entry - next_close) * multiplier * lots * position_side
        equity_curve.append(EquityPoint(trade_date=day.isoformat(), equity=cash + unrealized))

    # Force close open position on last day.
    if position_side != 0 and lots > 0 and trade_days:
        day = trade_days[-1]
        near = _get_current_month_future_symbol(day, "IF")
        nxt = _get_next_month_future_symbol(day, "IF")
        near_close = close_map.get((day, near))
        next_close = close_map.get((day, nxt))
        if near_close is not None and next_close is not None:
            near_pnl = (near_close - near_entry) * multiplier * lots * position_side
            next_pnl = (next_entry - next_close) * multiplier * lots * position_side
            pnl = near_pnl + next_pnl
            notional = (near_close + next_close) * multiplier * lots
            fee = notional * payload.fee_bps / 10_000.0
            cash += pnl - fee
            position_side = 0
            lots = 0
            equity_curve[-1] = EquityPoint(trade_date=day.isoformat(), equity=cash)

    returns = daily_returns(equity_curve)
    final_equity = cash
    total_return_pct = ((final_equity / payload.initial_cash) - 1.0) * 100.0
    metrics = BacktestMetrics(
        total_return_pct=round(total_return_pct, 4),
        max_drawdown_pct=round(max_drawdown_pct(equity_curve, payload.initial_cash), 4),
        annualized_volatility_pct=round(annualized_volatility_pct(returns), 4),
        sharpe_like=round(sharpe_like_ratio(returns), 4),
        win_rate_pct=0.0,
        trade_count=len(trades),
    )
    return MultiFactorBacktestResponse(
        strategy_id=payload.template_id,
        initial_cash=payload.initial_cash,
        final_equity=round(final_equity, 4),
        metrics=metrics,
        rebalance_dates=rebalance_dates,
        holdings={},
        trades=trades,
    )


def run_joinquant_template_portfolio(
    payload: PortfolioTemplateBacktestRequest,
) -> PortfolioTemplateBacktestResponse:
    def allocate_weights() -> tuple[dict[str, float], dict[str, float], float]:
        scores: dict[str, float] = {}
        caps: dict[str, float] = {}
        for item in payload.items:
            score = max(item.expected_return_pct - (payload.risk_aversion * item.risk_volatility_pct), 0.0)
            scores[item.strategy_id] = score
            cap = item.weight_cap if item.weight_cap is not None else payload.max_single_weight
            caps[item.strategy_id] = max(0.0, min(cap, payload.max_single_weight))

        total_score = sum(scores.values())
        if total_score <= 0:
            provisional = {
                item.strategy_id: min(payload.total_exposure / len(payload.items), caps[item.strategy_id])
                for item in payload.items
            }
        else:
            provisional = {
                item.strategy_id: min(payload.total_exposure * (scores[item.strategy_id] / total_score), caps[item.strategy_id])
                for item in payload.items
            }
        allocated = sum(provisional.values())
        remaining = max(0.0, payload.total_exposure - allocated)
        ranked = sorted(payload.items, key=lambda x: scores[x.strategy_id], reverse=True)
        for item in ranked:
            if remaining <= 0:
                break
            sid = item.strategy_id
            room = max(0.0, caps[sid] - provisional[sid])
            add = min(room, remaining)
            provisional[sid] += add
            remaining -= add
        return provisional, scores, max(0.0, payload.total_exposure - sum(provisional.values()))

    weights, scores, unallocated_weight = allocate_weights()
    components: list[PortfolioTemplateComponent] = []
    weighted_return_pct = 0.0
    for item in payload.items:
        base_req = JoinquantTemplateBacktestRequest(
            template_id=item.template_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_cash=1_000_000.0,
            index_symbol=item.index_symbol,
            factors=item.factors,
            factor_directions=item.factor_directions,
            factor_weights=item.factor_weights,
            rebalance_every=item.rebalance_every,
            top_n=item.top_n,
            fee_bps=payload.fee_bps,
            slippage_bps=payload.slippage_bps,
            execution_delay_bars=payload.execution_delay_bars,
            min_trade_lot=payload.min_trade_lot,
            yb=item.yb,
            z_open_threshold=item.z_open_threshold,
            z_close_threshold=item.z_close_threshold,
        )
        if item.template_id == "test1_multifactor":
            rows = build_joinquant_multifactor_rows(
                JoinquantMultiFactorBacktestRequest(
                    strategy_id=item.strategy_id,
                    start_date=base_req.start_date,
                    end_date=base_req.end_date,
                    index_symbol=base_req.index_symbol,
                    factors=base_req.factors,
                    factor_directions=base_req.factor_directions,
                    factor_weights=base_req.factor_weights,
                    rebalance_every=base_req.rebalance_every,
                    top_n=base_req.top_n,
                    initial_cash=base_req.initial_cash,
                    fee_bps=base_req.fee_bps,
                    slippage_bps=base_req.slippage_bps,
                    execution_delay_bars=base_req.execution_delay_bars,
                    min_trade_lot=base_req.min_trade_lot,
                )
            )
            result = run_multifactor_backtest(
                MultiFactorBacktestRequest(
                    strategy_id=item.strategy_id,
                    rows=rows,
                    factor_directions=base_req.factor_directions,
                    factor_weights=base_req.factor_weights,
                    rebalance_every=base_req.rebalance_every,
                    top_n=base_req.top_n,
                    initial_cash=base_req.initial_cash,
                    fee_bps=base_req.fee_bps,
                    slippage_bps=base_req.slippage_bps,
                    execution_delay_bars=base_req.execution_delay_bars,
                    min_trade_lot=base_req.min_trade_lot,
                )
            )
        else:
            result = run_joinquant_test2_futures_spread(base_req)

        weight = weights.get(item.strategy_id, 0.0)
        weighted_return_pct += weight * result.metrics.total_return_pct
        component_equity = payload.initial_cash * weight * (1.0 + result.metrics.total_return_pct / 100.0)
        components.append(
            PortfolioTemplateComponent(
                strategy_id=item.strategy_id,
                template_id=item.template_id,
                weight=round(weight, 6),
                score=round(scores.get(item.strategy_id, 0.0), 6),
                component_return_pct=round(result.metrics.total_return_pct, 4),
                component_final_equity=round(component_equity, 4),
                trade_count=result.metrics.trade_count,
            )
        )

    final_equity = payload.initial_cash * (
        (1.0 - payload.total_exposure)
        + (unallocated_weight)
        + (1.0 + weighted_return_pct / 100.0) * (payload.total_exposure - unallocated_weight)
    )
    total_return_pct = ((final_equity / payload.initial_cash) - 1.0) * 100.0
    return PortfolioTemplateBacktestResponse(
        initial_cash=payload.initial_cash,
        final_equity=round(final_equity, 4),
        total_return_pct=round(total_return_pct, 4),
        weighted_return_pct=round(weighted_return_pct, 4),
        components=components,
        unallocated_weight=round(unallocated_weight, 6),
        as_of=datetime.now(timezone.utc).isoformat(),
    )


def run_backtest(payload: BacktestRequest) -> BacktestResponse:
    def apply_slippage(price: float, side: str) -> float:
        if payload.slippage_bps <= 0:
            return price
        factor = payload.slippage_bps / 10_000.0
        if side == "BUY":
            return price * (1.0 + factor)
        return price * (1.0 - factor)

    closes = [bar.close for bar in payload.bars]
    cash = payload.initial_cash
    position = 0
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []
    pending_action: str | None = None
    pending_execute_idx = -1

    for idx, bar in enumerate(payload.bars):
        if pending_action is not None and idx >= pending_execute_idx:
            if pending_action == "BUY" and position == 0:
                exec_price = apply_slippage(bar.close, "BUY")
                buyable = int(cash // exec_price)
                if buyable > 0:
                    fee = buyable * exec_price * payload.fee_bps / 10_000.0
                    cash -= (buyable * exec_price) + fee
                    position += buyable
                    trades.append(
                        Trade(
                            trade_date=bar.trade_date,
                            side="BUY",
                            quantity=buyable,
                            price=exec_price,
                            fee=fee,
                            cash_after=cash,
                            position_after=position,
                        )
                    )
            elif pending_action == "SELL" and position > 0:
                exec_price = apply_slippage(bar.close, "SELL")
                fee = position * exec_price * payload.fee_bps / 10_000.0
                cash += (position * exec_price) - fee
                trades.append(
                    Trade(
                        trade_date=bar.trade_date,
                        side="SELL",
                        quantity=position,
                        price=exec_price,
                        fee=fee,
                        cash_after=cash,
                        position_after=0,
                    )
                )
                position = 0
            pending_action = None
            pending_execute_idx = -1

        if idx >= payload.long_window - 1:
            short_ma = rolling_mean(closes, payload.short_window, idx)
            long_ma = rolling_mean(closes, payload.long_window, idx)

            if pending_action is None:
                if short_ma > long_ma and position == 0:
                    pending_action = "BUY"
                    pending_execute_idx = idx + payload.execution_delay_bars
                elif short_ma < long_ma and position > 0:
                    pending_action = "SELL"
                    pending_execute_idx = idx + payload.execution_delay_bars

        equity_curve.append(EquityPoint(trade_date=bar.trade_date, equity=cash + (position * bar.close)))

    if position > 0:
        last = payload.bars[-1]
        exit_price = apply_slippage(last.close, "SELL")
        fee = position * exit_price * payload.fee_bps / 10_000.0
        cash += (position * exit_price) - fee
        trades.append(
            Trade(
                trade_date=last.trade_date,
                side="SELL",
                quantity=position,
                price=exit_price,
                fee=fee,
                cash_after=cash,
                position_after=0,
            )
        )
        position = 0
        equity_curve[-1] = EquityPoint(trade_date=last.trade_date, equity=cash)

    returns = daily_returns(equity_curve)
    final_equity = cash
    total_return_pct = ((final_equity / payload.initial_cash) - 1.0) * 100.0
    metrics = BacktestMetrics(
        total_return_pct=round(total_return_pct, 4),
        max_drawdown_pct=round(max_drawdown_pct(equity_curve, payload.initial_cash), 4),
        annualized_volatility_pct=round(annualized_volatility_pct(returns), 4),
        sharpe_like=round(sharpe_like_ratio(returns), 4),
        win_rate_pct=round(compute_win_rate(trades), 4),
        trade_count=len(trades),
    )
    return BacktestResponse(
        strategy_id=payload.strategy_id,
        symbol=payload.symbol,
        initial_cash=payload.initial_cash,
        final_equity=round(final_equity, 4),
        metrics=metrics,
        trades=trades,
    )


def run_multifactor_backtest(payload: MultiFactorBacktestRequest) -> MultiFactorBacktestResponse:
    def apply_slippage(price: float, side: str) -> float:
        if payload.slippage_bps <= 0:
            return price
        factor = payload.slippage_bps / 10_000.0
        if side == "BUY":
            return price * (1.0 + factor)
        return price * (1.0 - factor)

    def lot_floor(quantity: int) -> int:
        if quantity <= 0:
            return 0
        return (quantity // payload.min_trade_lot) * payload.min_trade_lot

    rows_by_date: dict[str, list[MultiFactorRowInput]] = {}
    for row in payload.rows:
        rows_by_date.setdefault(row.trade_date, []).append(row)
    dates = sorted(rows_by_date.keys())

    cash = payload.initial_cash
    positions: dict[str, int] = {}
    trades: list[MultiFactorTrade] = []
    equity_curve: list[EquityPoint] = []
    rebalance_dates: list[str] = []
    pending_targets: set[str] | None = None
    pending_execute_idx = -1
    last_close: dict[str, float] = {}

    for idx, trade_date in enumerate(dates):
        day_rows = rows_by_date[trade_date]
        close_by_symbol = {row.symbol: row.close for row in day_rows}
        for symbol, close in close_by_symbol.items():
            last_close[symbol] = close

        if pending_targets is not None and idx >= pending_execute_idx:
            target_symbols = pending_targets
            all_symbols = set(positions.keys()) | set(target_symbols)
            equity = cash
            for symbol, qty in positions.items():
                if qty <= 0:
                    continue
                mark_price = close_by_symbol.get(symbol, last_close.get(symbol))
                if mark_price is None:
                    continue
                equity += qty * mark_price

            target_value = equity / max(1, len(target_symbols))

            for symbol, qty in list(positions.items()):
                if qty <= 0:
                    continue
                if symbol in target_symbols:
                    continue
                price = close_by_symbol.get(symbol, last_close.get(symbol))
                if price is None:
                    continue
                exec_price = apply_slippage(price, "SELL")
                fee = qty * exec_price * payload.fee_bps / 10_000.0
                cash += (qty * exec_price) - fee
                trades.append(
                    MultiFactorTrade(
                        trade_date=trade_date,
                        symbol=symbol,
                        side="SELL",
                        quantity=qty,
                        price=exec_price,
                        fee=fee,
                        cash_after=cash,
                        position_after_symbol=0,
                    )
                )
                positions[symbol] = 0

            for symbol in all_symbols:
                if symbol not in target_symbols:
                    continue
                price = close_by_symbol.get(symbol, last_close.get(symbol))
                if price is None:
                    continue
                exec_buy_price = apply_slippage(price, "BUY")
                desired_qty = lot_floor(int(target_value // exec_buy_price))
                current_qty = positions.get(symbol, 0)
                delta = desired_qty - current_qty
                if delta == 0:
                    continue
                if delta < 0:
                    sell_qty = lot_floor(abs(delta))
                    if sell_qty <= 0:
                        continue
                    exec_sell_price = apply_slippage(price, "SELL")
                    fee = sell_qty * exec_sell_price * payload.fee_bps / 10_000.0
                    cash += (sell_qty * exec_sell_price) - fee
                    positions[symbol] = max(0, current_qty - sell_qty)
                    trades.append(
                        MultiFactorTrade(
                            trade_date=trade_date,
                            symbol=symbol,
                            side="SELL",
                            quantity=sell_qty,
                            price=exec_sell_price,
                            fee=fee,
                            cash_after=cash,
                            position_after_symbol=positions[symbol],
                        )
                    )
                else:
                    affordable_qty = lot_floor(
                        int(cash // (exec_buy_price * (1.0 + payload.fee_bps / 10_000.0)))
                    )
                    buy_qty = min(lot_floor(delta), affordable_qty)
                    if buy_qty <= 0:
                        continue
                    fee = buy_qty * exec_buy_price * payload.fee_bps / 10_000.0
                    cash -= (buy_qty * exec_buy_price) + fee
                    positions[symbol] = current_qty + buy_qty
                    trades.append(
                        MultiFactorTrade(
                            trade_date=trade_date,
                            symbol=symbol,
                            side="BUY",
                            quantity=buy_qty,
                            price=exec_buy_price,
                            fee=fee,
                            cash_after=cash,
                            position_after_symbol=positions[symbol],
                        )
                    )

            pending_targets = None
            pending_execute_idx = -1

        if idx % payload.rebalance_every == 0:
            symbol_rows: list[tuple[str, dict[str, float]]] = []
            for row in day_rows:
                if all(factor in row.factors for factor in payload.factor_directions):
                    symbol_rows.append((row.symbol, row.factors))

            if symbol_rows:
                ranked_symbols: list[tuple[str, float]] = []
                total = len(symbol_rows)
                for symbol, _ in symbol_rows:
                    ranked_symbols.append((symbol, 0.0))
                score_by_symbol = dict(ranked_symbols)

                for factor, direction in payload.factor_directions.items():
                    factor_weight = payload.factor_weights.get(factor, 1.0)
                    sorted_rows = sorted(
                        symbol_rows,
                        key=lambda item: item[1][factor],
                        reverse=(direction == -1),
                    )
                    for rank_idx, (symbol, _) in enumerate(sorted_rows):
                        rank_score = float(total - rank_idx)
                        score_by_symbol[symbol] += rank_score * factor_weight

                sorted_symbols = sorted(
                    score_by_symbol.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
                selected = [symbol for symbol, _ in sorted_symbols[: payload.top_n]]
                if selected:
                    pending_targets = set(selected)
                    pending_execute_idx = idx + payload.execution_delay_bars
                    rebalance_dates.append(trade_date)

        equity = cash
        for symbol, qty in positions.items():
            if qty <= 0:
                continue
            mark_price = close_by_symbol.get(symbol, last_close.get(symbol))
            if mark_price is None:
                continue
            equity += qty * mark_price
        equity_curve.append(EquityPoint(trade_date=trade_date, equity=equity))

    if dates:
        final_date = dates[-1]
        final_rows = rows_by_date[final_date]
        final_close_by_symbol = {row.symbol: row.close for row in final_rows}
        for symbol, qty in list(positions.items()):
            if qty <= 0:
                continue
            price = final_close_by_symbol.get(symbol, last_close.get(symbol))
            if price is None:
                continue
            exec_price = apply_slippage(price, "SELL")
            fee = qty * exec_price * payload.fee_bps / 10_000.0
            cash += (qty * exec_price) - fee
            positions[symbol] = 0
            trades.append(
                MultiFactorTrade(
                    trade_date=final_date,
                    symbol=symbol,
                    side="SELL",
                    quantity=qty,
                    price=exec_price,
                    fee=fee,
                    cash_after=cash,
                    position_after_symbol=0,
                )
            )
        if equity_curve:
            equity_curve[-1] = EquityPoint(trade_date=final_date, equity=cash)

    returns = daily_returns(equity_curve)
    final_equity = cash
    total_return_pct = ((final_equity / payload.initial_cash) - 1.0) * 100.0
    metrics = BacktestMetrics(
        total_return_pct=round(total_return_pct, 4),
        max_drawdown_pct=round(max_drawdown_pct(equity_curve, payload.initial_cash), 4),
        annualized_volatility_pct=round(annualized_volatility_pct(returns), 4),
        sharpe_like=round(sharpe_like_ratio(returns), 4),
        win_rate_pct=round(compute_multifactor_win_rate(trades), 4),
        trade_count=len(trades),
    )
    normalized_holdings = {symbol: qty for symbol, qty in positions.items() if qty > 0}
    return MultiFactorBacktestResponse(
        strategy_id=payload.strategy_id,
        initial_cash=payload.initial_cash,
        final_equity=round(final_equity, 4),
        metrics=metrics,
        rebalance_dates=rebalance_dates,
        holdings=normalized_holdings,
        trades=trades,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "backtest-engine"}


@app.post("/backtest/run", response_model=BacktestResponse)
def backtest_run(payload: BacktestRequest) -> BacktestResponse:
    try:
        return run_backtest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backtest/run-multifactor", response_model=MultiFactorBacktestResponse)
def backtest_run_multifactor(payload: MultiFactorBacktestRequest) -> MultiFactorBacktestResponse:
    try:
        return run_multifactor_backtest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backtest/run-multifactor-joinquant", response_model=MultiFactorBacktestResponse)
def backtest_run_multifactor_joinquant(
    payload: JoinquantMultiFactorBacktestRequest,
) -> MultiFactorBacktestResponse:
    try:
        rows = build_joinquant_multifactor_rows(payload)
        request = MultiFactorBacktestRequest(
            strategy_id=payload.strategy_id,
            rows=rows,
            factor_directions=payload.factor_directions,
            factor_weights=payload.factor_weights,
            rebalance_every=payload.rebalance_every,
            top_n=payload.top_n,
            initial_cash=payload.initial_cash,
            fee_bps=payload.fee_bps,
            slippage_bps=payload.slippage_bps,
            execution_delay_bars=payload.execution_delay_bars,
            min_trade_lot=payload.min_trade_lot,
        )
        return run_multifactor_backtest(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backtest/run-joinquant-template", response_model=MultiFactorBacktestResponse)
def backtest_run_joinquant_template(
    payload: JoinquantTemplateBacktestRequest,
) -> MultiFactorBacktestResponse:
    try:
        if payload.template_id == "test1_multifactor":
            request = JoinquantMultiFactorBacktestRequest(
                strategy_id="test1_multifactor",
                start_date=payload.start_date,
                end_date=payload.end_date,
                index_symbol=payload.index_symbol,
                factors=payload.factors,
                factor_directions=payload.factor_directions,
                factor_weights=payload.factor_weights,
                rebalance_every=payload.rebalance_every,
                top_n=payload.top_n,
                initial_cash=payload.initial_cash,
                fee_bps=payload.fee_bps,
                slippage_bps=payload.slippage_bps,
                execution_delay_bars=payload.execution_delay_bars,
                min_trade_lot=payload.min_trade_lot,
            )
            rows = build_joinquant_multifactor_rows(request)
            return run_multifactor_backtest(
                MultiFactorBacktestRequest(
                    strategy_id=request.strategy_id,
                    rows=rows,
                    factor_directions=request.factor_directions,
                    factor_weights=request.factor_weights,
                    rebalance_every=request.rebalance_every,
                    top_n=request.top_n,
                    initial_cash=request.initial_cash,
                    fee_bps=request.fee_bps,
                    slippage_bps=request.slippage_bps,
                    execution_delay_bars=request.execution_delay_bars,
                    min_trade_lot=request.min_trade_lot,
                )
            )
        return run_joinquant_test2_futures_spread(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backtest/run-portfolio-templates", response_model=PortfolioTemplateBacktestResponse)
def backtest_run_portfolio_templates(
    payload: PortfolioTemplateBacktestRequest,
) -> PortfolioTemplateBacktestResponse:
    try:
        return run_joinquant_template_portfolio(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8005, reload=False)

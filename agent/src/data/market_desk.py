"""Market Desk data layer — Tushare-first with Eastmoney/Sina fallback."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.market_routes import MarketOverviewResponse, QuoteItem

logger = logging.getLogger(__name__)

# Tushare index ts_code → Eastmoney secid (UI stable keys)
_TUSHARE_INDEX_MAP: tuple[tuple[str, str, str, str], ...] = (
    ("000001.SH", "1.000001", "上证指数", "CN"),
    ("399001.SZ", "0.399001", "深证成指", "CN"),
    ("399006.SZ", "0.399006", "创业板指", "CN"),
    ("000300.SH", "1.000300", "沪深300", "CN"),
)


def _tushare_configured() -> bool:
    from backtest.loaders.tushare import TUSHARE_TOKEN_PLACEHOLDERS
    from src.config.accessor import get_env_config

    return get_env_config().data.tushare_token.strip() not in TUSHARE_TOKEN_PLACEHOLDERS


def _get_tushare_pro():
    import tushare as ts

    from src.config.accessor import get_env_config

    token = get_env_config().data.tushare_token.strip()
    ts.set_token(token)
    return ts.pro_api()


def fetch_indices_tushare() -> List["QuoteItem"]:
    """Fetch A-share major indices via Tushare index_daily (latest bar)."""
    from src.api.market_routes import QuoteItem

    if not _tushare_configured():
        return []

    end = date.today()
    start = end - timedelta(days=14)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    try:
        pro = _get_tushare_pro()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tushare pro init failed for indices: %s", exc)
        return []

    items: List[QuoteItem] = []
    for ts_code, secid, name, market in _TUSHARE_INDEX_MAP:
        try:
            frame = pro.index_daily(ts_code=ts_code, start_date=start_s, end_date=end_s)
            if frame is None or getattr(frame, "empty", True):
                continue
            row = frame.sort_values("trade_date").iloc[-1]
            close = float(row.get("close"))
            pre_close = float(row.get("pre_close") or row.get("close"))
            change_pct = ((close / pre_close) - 1.0) * 100.0 if pre_close else None
            items.append(
                QuoteItem(
                    symbol=secid,
                    name=name,
                    market=market,
                    price=close,
                    change_pct=round(change_pct, 2) if change_pct is not None else None,
                    source="tushare",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("tushare index_daily failed for %s: %s", ts_code, exc)
    return items


def merge_index_quotes(
    tushare_items: List["QuoteItem"],
    fallback_items: List["QuoteItem"],
) -> List["QuoteItem"]:
    """Prefer Tushare for A-share indices; keep HK / missing from fallback."""
    from src.api.market_routes import QuoteItem

    by_symbol: Dict[str, QuoteItem] = {item.symbol: item for item in fallback_items}
    for item in tushare_items:
        if item.price is not None and item.error is None:
            by_symbol[item.symbol] = item
    from src.api.market_routes import INDEX_SECIDS

    order = [secid for secid, _, _ in INDEX_SECIDS]
    merged: List[QuoteItem] = []
    for secid in order:
        if secid in by_symbol:
            merged.append(by_symbol[secid])
    return merged


def fetch_northbound_tushare() -> Optional[Dict[str, Any]]:
    """Northbound snapshot via Tushare moneyflow_hsgt."""
    if not _tushare_configured():
        return None
    try:
        from src.tools import tushare_fallbacks

        payload = tushare_fallbacks.fetch_northbound_flow(lookback_days=5)
        realtime = payload.get("realtime") or {}
        if not any(realtime.get(k) is not None for k in ("total", "shanghai_connect", "shenzhen_connect")):
            return None
        history = payload.get("history") or []
        trade_date = history[-1].get("trade_date") if history else None
        return {
            "sh_net": realtime.get("shanghai_connect"),
            "sz_net": realtime.get("shenzhen_connect"),
            "total_net": realtime.get("total"),
            "unit": payload.get("unit", "10k CNY"),
            "trade_date": trade_date,
            "source": "tushare",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("tushare northbound fetch failed: %s", exc)
        return None


def fetch_northbound_with_fallback(fallback_fn) -> Optional[Dict[str, Any]]:
    """Tushare-first northbound; Eastmoney fallback."""
    row = fetch_northbound_tushare()
    if row:
        return row
    return fallback_fn()


def build_market_overview() -> "MarketOverviewResponse":
    """Build Market Desk overview via Data Service (Tushare-first)."""
    from src.api import market_routes as mr

    tushare_indices = fetch_indices_tushare()
    em_indices = mr._fetch_indices()
    indices = merge_index_quotes(tushare_indices, em_indices)

    industries = mr._fetch_board_list("industry", 5)
    concepts = mr._fetch_board_list("concept", 3)
    boards = industries + concepts
    for board in boards:
        board.leaders = mr._fetch_board_leaders(board.board_code, 4)
    hot_stocks = mr._fetch_hot_stocks(10)
    northbound = fetch_northbound_with_fallback(mr._fetch_northbound)
    limit_stats = mr._fetch_limit_stats()

    notes: List[str] = []
    index_sources = {item.source for item in indices if item.source}
    if "tushare" in index_sources:
        notes.append("A股指数优先来自 Tushare")
    if "sina" in index_sources:
        notes.append("部分指数来自新浪（东财限流降级）")
    if any(item.source == "eastmoney" for item in indices):
        notes.append("板块/港股指数来自东方财富")
    if northbound and northbound.get("source") == "tushare":
        notes.append("北向资金来自 Tushare（单位：万元）")
    elif northbound:
        notes.append("北向资金为公开接口快照，单位以面板为准")

    primary = "tushare" if tushare_indices or (
        northbound and northbound.get("source") == "tushare"
    ) else "eastmoney"

    return mr.MarketOverviewResponse(
        indices=indices,
        hot_boards=boards,
        hot_stocks=hot_stocks,
        as_of=date.today().isoformat(),
        source=primary,
        northbound=northbound,
        limit_stats=limit_stats,
        data_notes=notes,
        primary_provider=primary,
    )

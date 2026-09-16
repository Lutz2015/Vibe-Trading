"""Macro sentiment thermometer from Polymarket + Kalshi public prediction markets."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SENTIMENT_TTL_S = 300.0
_PREDICTION_PROBE_TTL_S = 600.0
_KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_GAMMA_PROBE_URL = "https://gamma-api.polymarket.com/events"
_cache_lock = threading.Lock()
_sentiment_cache: Tuple[float, Optional["SentimentResponse"]] = (0.0, None)
_prediction_probe_at: float = 0.0
_prediction_reachable: Optional[bool] = None

# Curated macro queries — provider: polymarket (default) or kalshi.
_TOPIC_SPECS: List[Dict[str, Any]] = [
    {
        "id": "fed_cut",
        "title": "美联储年内至少再降息一次",
        "category": "货币政策",
        "query": "fed rate cut",
        "horizon": "2026 年内",
        "weight": 1.0,
        "invert": False,
        "provider": "polymarket",
    },
    {
        "id": "us_recession",
        "title": "美国未来 12 个月陷入衰退",
        "category": "增长",
        "query": "recession",
        "horizon": "未来 12 个月",
        "weight": 1.3,
        "invert": True,
        "provider": "kalshi",
    },
    {
        "id": "cn_stimulus",
        "title": "中国出台超预期稳增长一揽子",
        "category": "中国政策",
        "query": "China stimulus",
        "horizon": "未来 2 个季度",
        "weight": 1.2,
        "invert": False,
        "provider": "polymarket",
    },
    {
        "id": "ai_capex",
        "title": "全球 AI 资本开支维持高增",
        "category": "科技",
        "query": "AI capex",
        "horizon": "2026 全年",
        "weight": 1.4,
        "invert": False,
        "provider": "kalshi",
    },
    {
        "id": "oil_90",
        "title": "布伦特原油年内触及 90 美元",
        "category": "大宗",
        "query": "oil price 90",
        "horizon": "2026 年内",
        "weight": 0.6,
        "invert": False,
        "provider": "polymarket",
    },
    {
        "id": "btc_ath",
        "title": "比特币再创历史新高",
        "category": "加密",
        "query": "Bitcoin all time high",
        "horizon": "未来 6 个月",
        "weight": 1.2,
        "invert": False,
        "provider": "polymarket",
        "query": "bitcoin all time high",
    },
]


class SentimentItem(BaseModel):
    id: str
    title: str
    source: str = "Polymarket"
    category: str
    probability: float
    delta24h: float = 0.0
    horizon: str = ""
    note: Optional[str] = None
    event_id: Optional[str] = None
    market_id: Optional[str] = None
    error: Optional[str] = None


class SentimentResponse(BaseModel):
    items: List[SentimentItem] = Field(default_factory=list)
    composite: float = 50.0
    as_of: str
    source: str = "polymarket"
    mode: str = "prediction"  # prediction | market_proxy | hybrid
    cached: bool = False
    partial: bool = False
    source_notes: List[str] = Field(default_factory=list)


def _yes_probability(
    markets: List[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Extract Yes implied probability (0-100) and 24h change (pp) from event markets."""
    if not markets:
        return None, None, None, None
    market = markets[0]
    question = market.get("question")
    market_id = market.get("market_id")
    delta_raw = market.get("one_day_probability_change")
    delta_pp: Optional[float] = None
    if delta_raw is not None:
        try:
            delta_pp = round(float(delta_raw) * 100.0, 2)
        except (TypeError, ValueError):
            delta_pp = None

    outcomes = market.get("outcomes") or []
    for row in outcomes:
        if not isinstance(row, dict):
            continue
        label = str(row.get("outcome") or "").strip().lower()
        if label in ("yes", "是"):
            pct = row.get("implied_probability_pct")
            if pct is not None:
                return float(pct), delta_pp, str(question) if question else None, market_id

    if outcomes and isinstance(outcomes[0], dict):
        pct = outcomes[0].get("implied_probability_pct")
        if pct is not None:
            return float(pct), delta_pp, str(question) if question else None, market_id
    return None, delta_pp, str(question) if question else None, market_id


def _dollar_prob(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value) * 100.0, 1)
    except (TypeError, ValueError):
        return None


def _kalshi_market_prob(market: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Return (probability_pct, delta24_pp) from a Kalshi market row."""
    prob = _dollar_prob(market.get("last_price_dollars"))
    if prob is None:
        prob = _dollar_prob(market.get("yes_bid_dollars"))
    prev = _dollar_prob(market.get("previous_price_dollars"))
    delta_pp: Optional[float] = None
    if prob is not None and prev is not None:
        delta_pp = round(prob - prev, 1)
    return prob, delta_pp


def _fetch_kalshi_topic(spec: Dict[str, Any]) -> SentimentItem:
    import httpx

    base = SentimentItem(
        id=str(spec["id"]),
        title=str(spec["title"]),
        source="Kalshi",
        category=str(spec["category"]),
        horizon=str(spec.get("horizon") or ""),
        probability=0.0,
        delta24h=0.0,
    )
    try:
        params: Dict[str, Any] = {
            "status": "open",
            "with_nested_markets": "true",
            "limit": 80,
        }
        series = spec.get("kalshi_series")
        if series:
            params["series_ticker"] = str(series)
        with httpx.Client(timeout=4.0, follow_redirects=True) as client:
            response = client.get(f"{_KALSHI_BASE}/events", params=params)
            response.raise_for_status()
            payload = response.json()
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return base.model_copy(update={"error": "unexpected Kalshi payload"})
        needle = str(spec.get("query") or "").strip().lower()
        if needle and not series:
            events = [
                e
                for e in events
                if isinstance(e, dict)
                and needle
                in f"{e.get('title') or ''} {e.get('sub_title') or ''}".lower()
            ]
        for event in events:
            if not isinstance(event, dict):
                continue
            title = str(event.get("title") or base.title)[:120]
            markets = event.get("markets") or []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                prob, delta_pp = _kalshi_market_prob(market)
                if prob is None:
                    continue
                note = str(market.get("title") or market.get("subtitle") or "")[:160] or None
                return base.model_copy(
                    update={
                        "title": title,
                        "probability": prob,
                        "delta24h": round(delta_pp or 0.0, 1),
                        "event_id": str(event.get("event_ticker") or event.get("ticker") or ""),
                        "market_id": str(market.get("ticker") or ""),
                        "note": note,
                    }
                )
        return base.model_copy(update={"error": "no matching Kalshi market"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("kalshi topic %s failed: %s", spec.get("id"), exc)
        return base.model_copy(update={"error": str(exc)[:120]})


def _fetch_polymarket_topic(spec: Dict[str, Any]) -> SentimentItem:
    from src.tools.prediction_market_tool import PredictionMarketTool

    tool = PredictionMarketTool()
    base = SentimentItem(
        id=str(spec["id"]),
        title=str(spec["title"]),
        source="Polymarket",
        category=str(spec["category"]),
        horizon=str(spec.get("horizon") or ""),
        probability=0.0,
        delta24h=0.0,
    )
    try:
        search_raw = json.loads(
            tool.execute(mode="search", query=str(spec["query"]), limit=3, status="open")
        )
        if not search_raw.get("ok"):
            return base.model_copy(update={"error": str(search_raw.get("error") or "search failed")})
        events = (search_raw.get("data") or {}).get("events") or []
        event_id = None
        for row in events:
            if isinstance(row, dict) and row.get("event_id"):
                event_id = str(row["event_id"])
                if row.get("title"):
                    base = base.model_copy(update={"title": str(row["title"])[:120]})
                break
        if not event_id:
            return base.model_copy(update={"error": "no matching open event"})

        event_raw = json.loads(tool.execute(mode="event", ids=[event_id]))
        if not event_raw.get("ok"):
            return base.model_copy(
                update={"event_id": event_id, "error": str(event_raw.get("error") or "event failed")}
            )
        event_rows = (event_raw.get("data") or {}).get("events") or []
        if not event_rows or not isinstance(event_rows[0], dict):
            return base.model_copy(update={"event_id": event_id, "error": "empty event payload"})
        event = event_rows[0]
        if event.get("error"):
            return base.model_copy(update={"event_id": event_id, "error": str(event["error"])})
        markets = event.get("markets") or []
        prob, delta_pp, question, market_id = _yes_probability(markets)
        if prob is None:
            return base.model_copy(update={"event_id": event_id, "error": "no priced outcome"})
        note = None
        if question and question != base.title:
            note = question[:160]
        return base.model_copy(
            update={
                "probability": round(prob, 1),
                "delta24h": round(delta_pp or 0.0, 1),
                "event_id": event_id,
                "market_id": market_id,
                "note": note,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("polymarket topic %s failed: %s", spec.get("id"), exc)
        return base.model_copy(update={"error": str(exc)[:120]})


def _prediction_markets_reachable() -> bool:
    """Fast probe — skip slow Polymarket/Kalshi when the host cannot reach them."""
    global _prediction_probe_at, _prediction_reachable
    now = time.monotonic()
    if _prediction_reachable is not None and now - _prediction_probe_at < _PREDICTION_PROBE_TTL_S:
        return _prediction_reachable
    try:
        import httpx

        with httpx.Client(timeout=httpx.Timeout(2.5, connect=2.0), follow_redirects=True) as client:
            response = client.get(_GAMMA_PROBE_URL, params={"limit": 1})
            _prediction_reachable = response.status_code == 200
    except Exception:  # noqa: BLE001
        _prediction_reachable = False
    _prediction_probe_at = now
    if not _prediction_reachable:
        logger.info("Polymarket probe failed — sentiment will use A-share market proxy")
    return bool(_prediction_reachable)


def _avg_float(values: List[Any]) -> Optional[float]:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _clamp_prob(value: float, lo: float = 8.0, hi: float = 92.0) -> float:
    return round(max(lo, min(hi, value)), 1)


def _heat_prob(
    change: Optional[float],
    *,
    center: float = 50.0,
    scale: float = 9.0,
) -> float:
    if change is None:
        return center
    return _clamp_prob(center + change * scale)


def _board_heat(boards: List[Dict[str, Any]], *keywords: str) -> Optional[float]:
    hits: List[float] = []
    for row in boards:
        if not isinstance(row, dict):
            continue
        name = str(row.get("board_name") or "")
        if not any(k in name for k in keywords):
            continue
        chg = row.get("change_pct")
        if isinstance(chg, (int, float)):
            hits.append(float(chg))
    return _avg_float(hits)


def _fetch_market_proxy_items() -> List[SentimentItem]:
    """Domestic fallback when Polymarket/Kalshi are unreachable (typical CN networks)."""
    from src.api.market_routes import get_overview_snapshot

    snap = get_overview_snapshot()
    indices = snap.get("indices") or []
    boards = snap.get("hot_boards") or []
    stocks = snap.get("hot_stocks") or []

    cn = _avg_float([i.get("change_pct") for i in indices if i.get("market") == "CN"])
    us = _avg_float([i.get("change_pct") for i in indices if i.get("market") == "US"])
    board_avg = _avg_float([b.get("change_pct") for b in boards])
    stock_avg = _avg_float([s.get("change_pct") for s in stocks])
    breadth = board_avg if board_avg is not None else stock_avg if stock_avg is not None else cn

    ai_h = _board_heat(boards, "人工智能", "AI", "算力", "芯片", "半导体", "光模块", "智能")
    oil_h = _board_heat(boards, "石油", "油气", "煤炭", "化工", "能源")
    crypto_h = _board_heat(boards, "比特币", "加密", "区块链", "数字货币")

    delta_proxy = round((cn or board_avg or 0.0) * 0.35, 1)

    proxy_specs: Dict[str, Tuple[float, str]] = {
        "fed_cut": (
            _heat_prob(-(us if us is not None else 0.0), center=48.0, scale=11.0),
            "代理：美股越强 → 降息预期越低",
        ),
        "us_recession": (
            _heat_prob(-(us if us is not None else breadth or 0.0), center=38.0, scale=10.0),
            "代理：美股/市场越弱 → 衰退担忧越高",
        ),
        "cn_stimulus": (
            _heat_prob(-(cn if cn is not None else breadth or 0.0), center=42.0, scale=9.0),
            "代理：A股偏弱 → 稳增长政策预期升温",
        ),
        "ai_capex": (
            _heat_prob(ai_h if ai_h is not None else breadth, center=58.0, scale=10.0),
            "代理：AI/算力/半导体等板块涨幅",
        ),
        "oil_90": (
            _heat_prob(oil_h if oil_h is not None else (breadth or 0.0) * 0.6, center=32.0, scale=12.0),
            "代理：油气/能源板块热度",
        ),
        "btc_ath": (
            _heat_prob(crypto_h if crypto_h is not None else stock_avg if stock_avg is not None else breadth, center=52.0, scale=11.0),
            "代理：题材股/风险偏好热度",
        ),
    }

    items: List[SentimentItem] = []
    for spec in _TOPIC_SPECS:
        spec_id = str(spec["id"])
        prob, note = proxy_specs.get(spec_id, (50.0, "A股行情代理"))
        items.append(
            SentimentItem(
                id=spec_id,
                title=str(spec["title"]),
                source="A股行情",
                category=str(spec["category"]),
                horizon=str(spec.get("horizon") or ""),
                probability=prob,
                delta24h=delta_proxy,
                note=note,
            )
        )
    return items


def _fetch_topic(spec: Dict[str, Any]) -> SentimentItem:
    provider = str(spec.get("provider") or "polymarket").lower()
    if provider == "kalshi":
        item = _fetch_kalshi_topic(spec)
        if item.error:
            fallback = _fetch_polymarket_topic({**spec, "provider": "polymarket"})
            if not fallback.error:
                return fallback.model_copy(update={"note": "Kalshi 不可用，已回退 Polymarket"})
        return item
    return _fetch_polymarket_topic(spec)


def _composite(items: List[SentimentItem]) -> float:
    by_id = {item.id: item for item in items}
    total_w = 0.0
    total_v = 0.0
    for spec in _TOPIC_SPECS:
        item = by_id.get(str(spec["id"]))
        if not item or item.error or item.probability <= 0:
            continue
        w = abs(float(spec.get("weight") or 1.0))
        value = 100.0 - item.probability if spec.get("invert") else item.probability
        total_v += value * w
        total_w += w
    if total_w <= 0:
        return 50.0
    return round(total_v / total_w, 1)


def _empty_sentiment(notes: List[str]) -> SentimentResponse:
    proxy = _fetch_market_proxy_items()
    return SentimentResponse(
        items=proxy,
        composite=_composite(proxy),
        as_of=date.today().isoformat(),
        source="a-share-proxy",
        mode="market_proxy",
        partial=True,
        source_notes=notes + ["fallback: A-share market proxy"],
    )


def _fetch_prediction_items() -> Tuple[List[SentimentItem], List[str]]:
    from concurrent.futures import TimeoutError as FuturesTimeoutError
    from concurrent.futures import ThreadPoolExecutor, as_completed

    notes: List[str] = []
    items: List[SentimentItem] = []
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_topic, spec): spec for spec in _TOPIC_SPECS}

        def _append_error(spec: Dict[str, Any], message: str) -> None:
            spec_id = str(spec["id"])
            if spec_id in seen:
                return
            seen.add(spec_id)
            notes.append(f"{spec_id}: {message[:80]}")

        try:
            for fut in as_completed(futures, timeout=18):
                spec = futures[fut]
                try:
                    item = fut.result()
                    seen.add(item.id)
                    items.append(item)
                except Exception as exc:  # noqa: BLE001
                    _append_error(spec, str(exc))
        except FuturesTimeoutError:
            notes.append("prediction markets timeout")
            for spec in _TOPIC_SPECS:
                if str(spec["id"]) not in seen:
                    _append_error(spec, "timeout")
    return items, notes


def _merge_with_market_proxy(
    prediction_items: List[SentimentItem],
    notes: List[str],
) -> Tuple[List[SentimentItem], str, str, List[str]]:
    by_id = {item.id: item for item in prediction_items}
    proxy_items = _fetch_market_proxy_items()
    proxy_by_id = {item.id: item for item in proxy_items}
    merged: List[SentimentItem] = []
    prediction_ok = 0
    for spec in _TOPIC_SPECS:
        spec_id = str(spec["id"])
        live = by_id.get(spec_id)
        if live and not live.error and live.probability > 0:
            merged.append(live)
            prediction_ok += 1
            continue
        proxy = proxy_by_id.get(spec_id)
        if proxy:
            if live and live.error:
                notes.append(f"{spec_id}: 已切换 A 股行情代理")
            merged.append(proxy)
        elif live:
            merged.append(live)
    if prediction_ok == 0:
        mode = "market_proxy"
        source_label = "a-share-proxy"
        notes.append("Polymarket/Kalshi 不可达，使用东财热点板块代理指标")
    elif prediction_ok < len(_TOPIC_SPECS):
        mode = "hybrid"
        source_label = "polymarket+a-share-proxy"
    else:
        mode = "prediction"
        sources = sorted({i.source for i in merged if not i.error and i.probability > 0})
        source_label = "+".join(s.lower().replace(" ", "") for s in sources) or "polymarket"
    return merged, mode, source_label, notes


def _build_sentiment() -> SentimentResponse:
    notes: List[str] = []
    try:
        if _prediction_markets_reachable():
            prediction_items, notes = _fetch_prediction_items()
        else:
            prediction_items = []
            notes.append("prediction markets skipped (network probe failed)")
        items, mode, source_label, notes = _merge_with_market_proxy(prediction_items, notes)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sentiment build failed")
        return _empty_sentiment([str(exc)[:200]])

    ok_count = sum(1 for i in items if not i.error and i.probability > 0)
    return SentimentResponse(
        items=items,
        composite=_composite(items),
        as_of=date.today().isoformat(),
        source=source_label,
        mode=mode,
        partial=mode != "prediction" or ok_count < len(_TOPIC_SPECS),
        source_notes=notes[:8],
    )


def _get_sentiment_cached() -> SentimentResponse:
    global _sentiment_cache
    with _cache_lock:
        ts, cached = _sentiment_cache
        if cached is not None and time.monotonic() - ts < _SENTIMENT_TTL_S:
            return cached.model_copy(update={"cached": True})
    fresh = _build_sentiment()
    with _cache_lock:
        _sentiment_cache = (time.monotonic(), fresh)
    return fresh


def register_sentiment_routes(
    app: FastAPI,
    require_local_or_auth=None,
) -> None:
    deps = [Depends(require_local_or_auth)] if require_local_or_auth else []

    @app.get("/sentiment/thermometer", response_model=SentimentResponse, dependencies=deps)
    async def sentiment_thermometer() -> SentimentResponse:
        """Macro expectation probabilities from Polymarket + Kalshi (read-only public APIs)."""
        import asyncio

        try:
            return await asyncio.to_thread(_get_sentiment_cached)
        except Exception as exc:  # noqa: BLE001 — page keeps working with partial payload
            logger.exception("sentiment thermometer failed")
            return _empty_sentiment([str(exc)[:200]])

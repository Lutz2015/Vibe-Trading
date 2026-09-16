"""Dynamic market overview + news-radar endpoints.

Hot sectors / hot stocks and headlines are pulled live from Eastmoney
public APIs. A short in-process TTL cache + request spacing keeps the
UI responsive without hammering the host (which resets burst connections).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}

# Major indices (secid for eastmoney stock/get).
INDEX_SECIDS = [
    ("1.000001", "上证指数", "CN"),
    ("0.399001", "深证成指", "CN"),
    ("0.399006", "创业板指", "CN"),
    ("1.000300", "沪深300", "CN"),
    ("100.DJIA", "道琼斯", "US"),
    ("100.NDX", "纳斯达克", "US"),
    ("100.SPX", "标普500", "US"),
]

_EM_MIN_INTERVAL_S = 1.2
_OVERVIEW_TTL_S = 45.0
_news_TTL_S = 25.0

_em_lock = threading.Lock()
_em_last_at = 0.0
_cache_lock = threading.Lock()
_overview_cache: Tuple[float, Optional["MarketOverviewResponse"]] = (0.0, None)
_overview_refreshing = False
_news_cache: Dict[str, Tuple[float, "NewsRadarResponse"]] = {}

# Topic chip → keyword filters for fast sources (Sina roll has no native topic API).
_TOPIC_FILTER_KEYWORDS: Dict[str, List[str]] = {
    "global": ["宏观", "经济", "政策", "央行", "GDP", "通胀", "美联储", "财政"],
    "ai": ["人工智能", "AI", "算力", "大模型", "芯片", "GPU", "DeepSeek"],
    "crypto": ["比特币", "加密", "区块链", "BTC", "以太坊", "数字货币"],
    "robot": ["机器人", "人形", "减速器", "具身"],
    "semiconductor": ["半导体", "芯片", "晶圆", "光刻", "存储"],
    "newenergy": ["新能源", "光伏", "锂电", "储能", "电动车", "电池"],
}


class QuoteItem(BaseModel):
    symbol: str
    name: str
    market: Optional[str] = None
    sub: Optional[str] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    source: Optional[str] = None
    error: Optional[str] = None


class HotBoard(BaseModel):
    board_code: str
    board_name: str
    change_pct: Optional[float] = None
    kind: str = "industry"  # industry | concept
    leaders: List[QuoteItem] = Field(default_factory=list)


class MarketOverviewResponse(BaseModel):
    indices: List[QuoteItem] = Field(default_factory=list)
    hot_boards: List[HotBoard] = Field(default_factory=list)
    hot_stocks: List[QuoteItem] = Field(default_factory=list)
    as_of: str
    source: str = "eastmoney"
    cached: bool = False


class NewsArticle(BaseModel):
    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    published: Optional[str] = None
    snippet: Optional[str] = None
    topic: Optional[str] = None
    signal: Optional[str] = None


class NewsRadarResponse(BaseModel):
    articles: List[NewsArticle] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    as_of: str
    source_notes: List[str] = Field(default_factory=list)
    cached: bool = False


_POS_WORDS = (
    "上涨", "大涨", "涨停", "突破", "创新高", "超预期", "利好", "增长", "获批", "中标",
    "surge", "rally", "record high", "beat", "upgrade", "bullish",
)
_NEG_WORDS = (
    "下跌", "大跌", "跌停", "破位", "新低", "低于预期", "利空", "下滑", "处罚", "调查",
    "暴跌", "plunge", "selloff", "miss", "downgrade", "bearish", "probe",
)


def _normalize_published(value: Any) -> Optional[str]:
    """Format unix timestamps / numeric strings from Sina et al. for UI display."""
    if value is None:
        return None
    from datetime import datetime

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return str(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        ts = int(text)
        if ts > 1e12:
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return text
    return text[:19] if len(text) > 19 else text


def _topic_filter_keywords(topic: Optional[str]) -> List[str]:
    if not topic:
        return []
    return list(_TOPIC_FILTER_KEYWORDS.get(topic.strip(), ()))


def _row_matches_topic(row: Dict[str, Any], topic: Optional[str]) -> bool:
    if not topic:
        return True
    keywords = _topic_filter_keywords(topic)
    if not keywords:
        return True
    hay = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    return any(kw.lower() in hay for kw in keywords)


def _strip_tags(text: str) -> str:
    out: List[str] = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return " ".join("".join(out).split())


def _headline_signal(title: str, snippet: str = "") -> str:
    text = f"{title} {snippet}".lower()
    pos = sum(1 for w in _POS_WORDS if w.lower() in text)
    neg = sum(1 for w in _NEG_WORDS if w.lower() in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


# push2 is often reset under burst; push2delay is the delayed mirror that stays up.
_EM_HOSTS = (
    "https://push2delay.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
)


def _em_get(path: str, params: Dict[str, Any]) -> Any:
    """GET Eastmoney JSON with process-wide spacing, host fallback, retries."""
    import httpx

    global _em_last_at
    last: Exception | None = None
    for attempt in range(3):
        with _em_lock:
            wait = _EM_MIN_INTERVAL_S - (time.monotonic() - _em_last_at)
            if wait > 0:
                time.sleep(wait)
            _em_last_at = time.monotonic()
        host = _EM_HOSTS[attempt % len(_EM_HOSTS)]
        url = f"{host}{path}"
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(url, params=params, headers=_HEADERS)
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.debug("eastmoney %s failed: %s", url, exc)
            time.sleep(0.4 * (attempt + 1))
    raise last if last else RuntimeError("eastmoney request failed")


def _symbol_with_suffix(code: str) -> str:
    code = (code or "").strip()
    if not code or "." in code or code.startswith("^"):
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


def _fetch_indices() -> List[QuoteItem]:
    """One clist call for major CN/US indices (fewer round-trips than per-secid)."""
    try:
        # m:1 = 上证指数等；m:100 = 国际指数；fid=f3 按涨跌幅，这里只要字段。
        payload = _em_get(
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": 50,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": "m:1+s:2,m:0+s:2,m:0+s:32,m:100",
                "fields": "f12,f14,f2,f3",
            },
        )
        rows = ((payload or {}).get("data") or {}).get("diff") or []
        by_code: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                by_code[str(row.get("f12") or "")] = row
        items: List[QuoteItem] = []
        for secid, name, market in INDEX_SECIDS:
            raw_code = secid.split(".")[-1]
            raw = by_code.get(raw_code)
            if not isinstance(raw, dict):
                items.append(QuoteItem(symbol=secid, name=name, market=market, error="unavailable"))
                continue
            try:
                price = float(raw.get("f2"))
            except (TypeError, ValueError):
                price = None
            try:
                chg = float(raw.get("f3"))
            except (TypeError, ValueError):
                chg = None
            items.append(
                QuoteItem(
                    symbol=secid,
                    name=str(raw.get("f14") or name),
                    market=market,
                    price=price,
                    change_pct=chg,
                    source="eastmoney",
                )
            )
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("index fetch failed: %s", exc)
        return [
            QuoteItem(symbol=s, name=n, market=m, error=str(exc)[:120])
            for s, n, m in INDEX_SECIDS
        ]


def _fetch_board_list(kind: str, limit: int) -> List[HotBoard]:
    fs = "m:90+t:2" if kind == "industry" else "m:90+t:3"
    try:
        payload = _em_get(
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": max(limit * 3, 20),
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": fs,
                "fields": "f12,f14,f3",
            },
        )
        rows = ((payload or {}).get("data") or {}).get("diff") or []
        boards: List[HotBoard] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("f14") or "").strip()
            code = str(row.get("f12") or "").strip()
            if not name or not code:
                continue
            if any(x in name for x in ("昨日", "近一月", "近三月", "融资", "融券", "预增", "预减")):
                continue
            try:
                chg = float(row.get("f3"))
            except (TypeError, ValueError):
                chg = None
            boards.append(
                HotBoard(board_code=code, board_name=name, change_pct=chg, kind=kind)
            )
            if len(boards) >= limit:
                break
        return boards
    except Exception as exc:  # noqa: BLE001
        logger.warning("board list fetch failed (%s): %s", kind, exc)
        return []


def _fetch_board_leaders(board_code: str, limit: int = 5) -> List[QuoteItem]:
    try:
        payload = _em_get(
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": f"b:{board_code}",
                "fields": "f12,f14,f2,f3",
            },
        )
        rows = ((payload or {}).get("data") or {}).get("diff") or []
        items: List[QuoteItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _symbol_with_suffix(str(row.get("f12") or ""))
            try:
                price = float(row.get("f2"))
            except (TypeError, ValueError):
                price = None
            try:
                chg = float(row.get("f3"))
            except (TypeError, ValueError):
                chg = None
            items.append(
                QuoteItem(
                    symbol=code,
                    name=str(row.get("f14") or code),
                    price=price,
                    change_pct=chg,
                    source="eastmoney",
                )
            )
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("board leaders failed for %s: %s", board_code, exc)
        return []


def _fetch_hot_stocks(limit: int = 12) -> List[QuoteItem]:
    try:
        payload = _em_get(
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": max(limit * 2, 16),
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:1+t:2,m:0+t:6,m:0+t:80",
                "fields": "f12,f14,f2,f3",
            },
        )
        rows = ((payload or {}).get("data") or {}).get("diff") or []
        items: List[QuoteItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("f14") or "")
            if "ST" in name.upper():
                continue
            code = _symbol_with_suffix(str(row.get("f12") or ""))
            try:
                price = float(row.get("f2"))
            except (TypeError, ValueError):
                price = None
            try:
                chg = float(row.get("f3"))
            except (TypeError, ValueError):
                chg = None
            items.append(
                QuoteItem(
                    symbol=code,
                    name=name or code,
                    price=price,
                    change_pct=chg,
                    source="eastmoney",
                )
            )
            if len(items) >= limit:
                break
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("hot stocks failed: %s", exc)
        return []


def _build_overview() -> MarketOverviewResponse:
    indices = _fetch_indices()
    industries = _fetch_board_list("industry", 5)
    concepts = _fetch_board_list("concept", 3)
    boards = industries + concepts
    # Leaders for the top board only — one extra EM round-trip under throttle.
    if boards:
        boards[0].leaders = _fetch_board_leaders(boards[0].board_code, 4)
    hot_stocks = _fetch_hot_stocks(10)
    return MarketOverviewResponse(
        indices=indices,
        hot_boards=boards,
        hot_stocks=hot_stocks,
        as_of=date.today().isoformat(),
        source="eastmoney",
    )


def _fetch_sina_roll(limit: int = 20) -> List[Dict[str, Any]]:
    """Sina roll-news — usually the fastest free CN finance feed."""
    import httpx

    # cat_1a = 要闻 / 证券 / 产经 mix; page size ~20.
    url = "https://feed.mix.sina.com.cn/api/roll/get"
    params = {
        "pageid": "153",
        "lid": "2516",
        "k": "",
        "num": str(limit),
        "page": "1",
    }
    headers = {
        "User-Agent": _HEADERS["User-Agent"],
        "Referer": "https://finance.sina.com.cn/",
    }
    with httpx.Client(timeout=4.0, follow_redirects=True) as client:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    result = payload.get("result") or {}
    rows = result.get("data") or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _strip_tags(str(row.get("title") or ""))
        if not title:
            continue
        out.append(
            {
                "title": title,
                "url": row.get("url"),
                "source": row.get("media_name") or "新浪财经",
                "published": _normalize_published(row.get("ctime") or row.get("create_time")),
                "snippet": _strip_tags(str(row.get("intro") or row.get("summary") or "")),
                "topic": "新浪财经",
            }
        )
    return out[:limit]


def _fetch_tushare_news(limit: int = 20) -> List[Dict[str, Any]]:
    """Optional Tushare news (requires TUSHARE_TOKEN). Very timely when configured."""
    import os

    token = (os.getenv("TUSHARE_TOKEN") or "").strip()
    if not token:
        return []
    try:
        import tushare as ts

        pro = ts.pro_api(token)
        # news_sp: 新闻快讯 — recent + src filter for speed
        df = pro.news_sp(start_date="", end_date="", src="新浪财经")
        if df is None or getattr(df, "empty", True):
            return []
        out: List[Dict[str, Any]] = []
        for _, row in df.head(limit).iterrows():
            title = _strip_tags(str(row.get("content") or row.get("title") or ""))
            if not title:
                continue
            out.append(
                {
                    "title": title[:160],
                    "url": row.get("url"),
                    "source": str(row.get("src") or "Tushare"),
                    "published": str(row.get("datetime") or row.get("create_time") or ""),
                    "snippet": "",
                    "topic": "Tushare",
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("tushare news failed: %s", exc)
        return []


def _fetch_parallel_news(limit: int, q: Optional[str], topic: Optional[str]) -> NewsRadarResponse:
    """Race several free news sources; first successful batches win.

    Sina roll is typically <1s; Eastmoney is slower/rate-limited. Tushare is
    used only when TUSHARE_TOKEN is set.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    notes: List[str] = []
    raw: List[Dict[str, Any]] = []

    def _sina() -> List[Dict[str, Any]]:
        return _fetch_sina_roll(max(limit, 15))

    def _east() -> List[Dict[str, Any]]:
        return [
            {
                "title": a.title,
                "url": a.url,
                "source": a.source,
                "published": a.published,
                "snippet": a.snippet,
                "topic": a.topic or "东方财富",
            }
            for a in _build_news(min(limit, 12), q, topic).articles
        ]

    def _ts() -> List[Dict[str, Any]]:
        return _fetch_tushare_news(limit)

    jobs = {"sina": _sina, "tushare": _ts}
    # Eastmoney when searching or when a topic chip needs keyword-specific headlines.
    if q or topic:
        jobs["eastmoney"] = _east

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        try:
            for fut in as_completed(futures, timeout=6):
                name = futures[fut]
                try:
                    rows = fut.result() or []
                    raw.extend(rows)
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"{name}: {exc}")
                    logger.debug("news source %s failed: %s", name, exc)
        except TimeoutError:
            notes.append("部分源超时")

    # Keyword filter when user searched.
    if q:
        needle = q.lower()
        raw = [
            r
            for r in raw
            if needle in f"{r.get('title')} {r.get('snippet')}".lower()
        ]
    elif topic:
        raw = [r for r in raw if _row_matches_topic(r, topic)]

    articles: List[NewsArticle] = []
    seen: set[str] = set()
    for row in raw:
        key = (row.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        articles.append(
            NewsArticle(
                title=row["title"],
                url=row.get("url"),
                source=row.get("source"),
                published=row.get("published"),
                snippet=row.get("snippet"),
                topic=row.get("topic"),
                signal=_headline_signal(row["title"], str(row.get("snippet") or "")),
            )
        )
        if len(articles) >= limit:
            break

    return NewsRadarResponse(
        articles=articles,
        topics=sorted({a.topic for a in articles if a.topic}),
        as_of=date.today().isoformat(),
        source_notes=notes,
    )


def _build_news(limit: int, q: Optional[str], topic: Optional[str]) -> NewsRadarResponse:
    """Eastmoney-only news (slower path used as fallback / explicit search)."""
    import json as _json

    from src.tools.stock_news_tool import StockNewsTool

    notes: List[str] = []
    raw: List[Dict[str, Any]] = []
    queries: List[str] = []
    if q and q.strip():
        queries.append(q.strip())
    else:
        queries.append("财经")
        if topic in ("ai", "AI算力", "semiconductor", "半导体"):
            queries.append("人工智能")
        elif topic in ("crypto", "加密"):
            queries.append("加密货币")
        elif topic in ("robot", "机器人"):
            queries.append("人形机器人")
        elif topic in ("global", "全球财经", "宏观经济"):
            queries.append("宏观经济")
        elif topic in ("newenergy", "新能源"):
            queries.append("新能源")

    tool = StockNewsTool()
    for query in queries:
        try:
            looks_like_code = bool(query) and (
                query[0].isdigit()
                or "." in query
                or query.endswith(("SH", "SZ", "BJ", "US", "HK"))
            )
            if looks_like_code:
                envelope = tool.execute(scope="stock", code=query, limit=limit)
            else:
                envelope = tool.execute(scope="global", limit=limit)
            payload = _json.loads(envelope)
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error") or "eastmoney news failed"))
            data = payload.get("data") or {}
            needle = "" if query in ("财经",) else query.lower()
            for row in data.get("articles") or []:
                if not isinstance(row, dict):
                    continue
                title = _strip_tags(str(row.get("title") or ""))
                snippet = _strip_tags(str(row.get("snippet") or ""))
                if not title:
                    continue
                if needle and needle not in f"{title} {snippet}".lower():
                    continue
                raw.append(
                    {
                        "title": title,
                        "url": row.get("url"),
                        "source": row.get("source"),
                        "published": row.get("published"),
                        "snippet": snippet,
                        "topic": query,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{query}: {exc}")
            logger.warning("news query %r failed: %s", query, exc)

    articles: List[NewsArticle] = []
    seen: set[str] = set()
    for row in raw:
        key = (row.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        articles.append(
            NewsArticle(
                title=row["title"],
                url=row.get("url"),
                source=row.get("source"),
                published=row.get("published"),
                snippet=row.get("snippet"),
                topic=row.get("topic"),
                signal=_headline_signal(row["title"], str(row.get("snippet") or "")),
            )
        )
        if len(articles) >= limit:
            break

    return NewsRadarResponse(
        articles=articles,
        topics=sorted({a.topic for a in articles if a.topic}),
        as_of=date.today().isoformat(),
        source_notes=notes,
    )


def get_overview_snapshot() -> Dict[str, Any]:
    """JSON-safe overview for insight / logic-chain prompts."""
    overview = _get_overview_cached()
    return overview.model_dump()


def _refresh_overview_background() -> None:
    """Rebuild overview cache without blocking request handlers."""
    global _overview_refreshing, _overview_cache
    with _cache_lock:
        if _overview_refreshing:
            return
        _overview_refreshing = True
    try:
        fresh = _build_overview()
        with _cache_lock:
            _overview_cache = (time.monotonic(), fresh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("overview background refresh failed: %s", exc)
    finally:
        with _cache_lock:
            _overview_refreshing = False


def warmup_market_caches() -> None:
    """Kick off background overview prefetch (non-blocking startup helper)."""
    threading.Thread(target=_refresh_overview_background, daemon=True, name="market-warmup").start()


def _get_overview_cached(*, allow_stale: bool = True) -> MarketOverviewResponse:
    global _overview_cache
    stale: Optional[MarketOverviewResponse] = None
    with _cache_lock:
        ts, cached = _overview_cache
        if cached is not None and time.monotonic() - ts < _OVERVIEW_TTL_S:
            return cached.model_copy(update={"cached": True})
        if cached is not None:
            stale = cached
    if stale is not None and allow_stale:
        threading.Thread(
            target=_refresh_overview_background,
            daemon=True,
            name="market-overview-refresh",
        ).start()
        return stale.model_copy(update={"cached": True})
    fresh = _build_overview()
    with _cache_lock:
        _overview_cache = (time.monotonic(), fresh)
    return fresh


def register_market_routes(
    app: FastAPI,
    require_local_or_auth=None,
) -> None:
    deps = [Depends(require_local_or_auth)] if require_local_or_auth else []

    @app.get("/market/overview", response_model=MarketOverviewResponse, dependencies=deps)
    async def market_overview() -> MarketOverviewResponse:
        """Live indices + hot industry/concept boards + hot A-share gainers."""
        import asyncio

        return await asyncio.to_thread(_get_overview_cached)

    @app.get("/news/radar", response_model=NewsRadarResponse, dependencies=deps)
    async def news_radar(
        q: Optional[str] = Query(default=None),
        limit: int = Query(default=16, ge=1, le=40),
        topic: Optional[str] = Query(default=None),
    ) -> NewsRadarResponse:
        """Fast multi-source news: Sina roll + optional Tushare + Eastmoney.

        Returns cached results immediately; refreshes in the background so
        the UI never waits on the slowest source.
        """
        limit = max(1, min(int(limit or 16), 40))
        cache_key = f"{q or ''}|{topic or ''}|{limit}"
        with _cache_lock:
            entry = _news_cache.get(cache_key)
            if entry and time.monotonic() - entry[0] < _news_TTL_S:
                return entry[1].model_copy(update={"cached": True})

        import asyncio

        def _load_news() -> NewsRadarResponse:
            fresh = _fetch_parallel_news(limit, q, topic)
            if not fresh.articles:
                fresh = _build_news(limit, q, topic)
            return fresh

        fresh = await asyncio.to_thread(_load_news)
        with _cache_lock:
            _news_cache[cache_key] = (time.monotonic(), fresh)
            if len(_news_cache) > 32:
                oldest = sorted(_news_cache.items(), key=lambda kv: kv[1][0])[:16]
                for key, _ in oldest:
                    _news_cache.pop(key, None)
        return fresh

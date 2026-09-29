"""DuckDB local cache — daily bars + news incremental sync."""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_conn = None

_DEFAULT_BAR_SYMBOLS = (
    "000001.SZ",
    "600519.SH",
    "000858.SZ",
    "601318.SH",
    "600036.SH",
    "000333.SZ",
    "00700.HK",
    "09988.HK",
)


def cache_db_path() -> Path:
    from src.config.paths import get_runtime_root

    path = get_runtime_root() / "cache" / "data.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect():
    import duckdb

    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is None:
            _conn = duckdb.connect(str(cache_db_path()))
            _init_schema(_conn)
    return _conn


def _init_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            source VARCHAR,
            fetched_at TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            article_id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL,
            url VARCHAR,
            source VARCHAR,
            published VARCHAR,
            snippet VARCHAR,
            topic VARCHAR,
            signal VARCHAR,
            ingested_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            stream VARCHAR PRIMARY KEY,
            watermark VARCHAR,
            last_run_at TIMESTAMP,
            rows_added INTEGER DEFAULT 0,
            detail VARCHAR
        )
        """
    )


def _article_id(row: dict[str, Any]) -> str:
    key = (str(row.get("url") or "").strip() or str(row.get("title") or "").strip()).lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _today() -> date:
    return date.today()


def _yesterday() -> date:
    return _today() - timedelta(days=1)


def default_bar_symbols() -> list[str]:
    """Universe from auto-trading.yaml when present, else built-in core list."""
    try:
        import os
        import yaml

        from src.config.paths import get_runtime_root

        env_path = os.getenv("AUTO_TRADING_CONFIG_PATH", "").strip()
        cfg_path = Path(env_path) if env_path else Path(__file__).resolve().parents[2] / "qbit" / "configs" / "auto-trading.yaml"
        if not cfg_path.exists():
            cfg_path = get_runtime_root() / "qbit" / "auto-trading.yaml"
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            symbols: list[str] = []
            for strat in raw.get("strategies") or []:
                params = strat.get("params") or {}
                for sym in params.get("universe_symbols") or []:
                    sym_s = str(sym).strip()
                    if sym_s and sym_s not in symbols:
                        symbols.append(sym_s)
            if symbols:
                return symbols
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_bar_symbols config read failed: %s", exc)
    return list(_DEFAULT_BAR_SYMBOLS)


def _last_bar_date(conn, symbol: str) -> date | None:
    row = conn.execute(
        "SELECT max(trade_date) FROM daily_bars WHERE symbol = ?",
        [symbol],
    ).fetchone()
    if not row or row[0] is None:
        return None
    val = row[0]
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def sync_daily_bars(symbols: list[str] | None = None) -> dict[str, Any]:
    """Incremental sync of daily OHLCV bars into DuckDB (Tushare when configured)."""
    symbols = symbols or default_bar_symbols()
    conn = _connect()
    end = _yesterday()
    added = 0
    errors: list[str] = []

    try:
        from backtest.loaders.tushare import DataLoader
        from backtest.loaders.tushare import TUSHARE_TOKEN_PLACEHOLDERS
        from src.config.accessor import get_env_config

        token = get_env_config().data.tushare_token.strip()
        if token in TUSHARE_TOKEN_PLACEHOLDERS:
            return {"ok": False, "added": 0, "error": "tushare_not_configured", "symbols": symbols}
        loader = DataLoader()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "added": 0, "error": str(exc)[:200], "symbols": symbols}

    now_ts = datetime.now(timezone.utc)
    for symbol in symbols:
        last = _last_bar_date(conn, symbol)
        start = (last + timedelta(days=1)) if last else (end - timedelta(days=400))
        if start > end:
            continue
        start_s = start.isoformat()
        end_s = end.isoformat()
        try:
            frames = loader.fetch([symbol], start_s, end_s, interval="1D")
            frame = frames.get(symbol)
            if frame is None or getattr(frame, "empty", True):
                continue
            for _, row in frame.iterrows():
                trade_date = row.get("date") or row.name
                if hasattr(trade_date, "date"):
                    trade_date = trade_date.date()
                else:
                    trade_date = date.fromisoformat(str(trade_date)[:10])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_bars
                    (symbol, trade_date, open, high, low, close, volume, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        symbol,
                        trade_date,
                        float(row.get("open") or 0),
                        float(row.get("high") or 0),
                        float(row.get("low") or 0),
                        float(row.get("close") or 0),
                        float(row.get("volume") or 0),
                        "tushare",
                        now_ts,
                    ],
                )
                added += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
            logger.debug("bar sync failed for %s: %s", symbol, exc)

    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state (stream, watermark, last_run_at, rows_added, detail)
        VALUES ('daily_bars', ?, ?, ?, ?)
        """,
        [end.isoformat(), now_ts, added, "; ".join(errors[:3])],
    )
    return {"ok": True, "added": added, "symbols": symbols, "errors": errors}


def upsert_news_rows(rows: list[dict[str, Any]]) -> int:
    """Persist news rows (dedupe by article_id)."""
    if not rows:
        return 0
    conn = _connect()
    now_ts = datetime.now(timezone.utc)
    added = 0
    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        aid = _article_id(row)
        conn.execute(
            """
            INSERT OR IGNORE INTO news_articles
            (article_id, title, url, source, published, snippet, topic, signal, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                aid,
                title,
                row.get("url"),
                row.get("source"),
                row.get("published"),
                row.get("snippet"),
                row.get("topic"),
                row.get("signal"),
                now_ts,
            ],
        )
        added += 1
    return added


def sync_news_incremental(limit: int = 30) -> dict[str, Any]:
    """Fetch live headlines and upsert into DuckDB."""
    from src.api.market_routes import _fetch_parallel_news

    fresh = _fetch_parallel_news(limit, None, None)
    rows = [item.model_dump() for item in fresh.articles]
    added = upsert_news_rows(rows)
    conn = _connect()
    watermark = fresh.as_of or _today().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state (stream, watermark, last_run_at, rows_added, detail)
        VALUES ('news', ?, ?, ?, ?)
        """,
        [watermark, datetime.now(timezone.utc), added, "; ".join(fresh.source_notes or [])[:500]],
    )
    return {"ok": True, "fetched": len(rows), "upserted": added, "as_of": fresh.as_of}


def query_news(
    *,
    limit: int = 20,
    q: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    """Read cached news from DuckDB with optional filter."""
    conn = _connect()
    limit = max(1, min(limit, 100))
    rows = conn.execute(
        """
        SELECT title, url, source, published, snippet, topic, signal
        FROM news_articles
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        [limit * 3],
    ).fetchall()
    out: list[dict[str, Any]] = []
    needle = (q or "").strip().lower()
    for title, url, source, published, snippet, row_topic, signal in rows:
        if needle and needle not in f"{title} {snippet or ''}".lower():
            continue
        if topic and row_topic and topic.lower() not in str(row_topic).lower():
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "source": source,
                "published": published,
                "snippet": snippet,
                "topic": row_topic,
                "signal": signal,
            }
        )
        if len(out) >= limit:
            break
    return out


def query_bars(symbol: str, *, limit: int = 120) -> list[dict[str, Any]]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT trade_date, open, high, low, close, volume, source
        FROM daily_bars
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        [symbol, max(1, min(limit, 500))],
    ).fetchall()
    return [
        {
            "trade_date": str(r[0]),
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
            "volume": r[5],
            "source": r[6],
        }
        for r in rows
    ]


def cache_stats() -> dict[str, Any]:
    conn = _connect()
    bars = conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
    news = conn.execute("SELECT count(*) FROM news_articles").fetchone()[0]
    sync_rows = conn.execute(
        "SELECT stream, watermark, last_run_at, rows_added, detail FROM sync_state"
    ).fetchall()
    streams = {
        str(r[0]): {
            "watermark": r[1],
            "last_run_at": str(r[2]) if r[2] else None,
            "rows_added": r[3],
            "detail": r[4],
        }
        for r in sync_rows
    }
    return {
        "db_path": str(cache_db_path()),
        "daily_bars": int(bars or 0),
        "news_articles": int(news or 0),
        "streams": streams,
    }


def run_full_sync() -> dict[str, Any]:
    """Sync bars + news in one call."""
    bars = sync_daily_bars()
    news = sync_news_incremental()
    return {"bars": bars, "news": news, "stats": cache_stats()}

"""Tushare-first data gateway — single entry for modules and agents."""

from __future__ import annotations

import logging
from typing import Any

from src.data.tushare_probe import probe_tushare_capabilities

logger = logging.getLogger(__name__)


class DataGateway:
    """Unified read-only data access for Market Desk and agents."""

    @staticmethod
    def market_overview(*, allow_stale: bool = True) -> Any:
        """Return Market Desk overview (Tushare-first, cached)."""
        from src.api.market_routes import _get_overview_cached

        return _get_overview_cached(allow_stale=allow_stale)

    @staticmethod
    def capabilities() -> dict[str, Any]:
        """Return provider capability snapshot (Tushare probe)."""
        tushare = probe_tushare_capabilities()
        return {
            "primary_provider": "tushare",
            "tushare": tushare,
            "fallback_providers": ["tencent", "mootdx", "eastmoney", "akshare"],
        }

    @staticmethod
    def health() -> dict[str, str]:
        cap = probe_tushare_capabilities()
        if cap.get("connected"):
            return {"status": "ok", "provider": "tushare"}
        if cap.get("configured"):
            return {"status": "degraded", "provider": "fallback"}
        return {"status": "degraded", "provider": "none"}

    @staticmethod
    def news_feed(
        *,
        q: str | None = None,
        topic: str | None = None,
        limit: int = 20,
        prefer_cache: bool = True,
    ) -> Any:
        """News feed — DuckDB cache first, live multi-source fallback."""
        from datetime import date

        from src.api.market_routes import NewsArticle, NewsRadarResponse
        from src.data.cache_store import query_news, upsert_news_rows

        limit = max(1, min(int(limit or 20), 40))
        articles_raw: list[dict] = []
        notes: list[str] = []

        if prefer_cache:
            try:
                articles_raw = query_news(limit=limit, q=q, topic=topic)
                if articles_raw:
                    pass  # cached flag on response; omit internal cache label from UI notes
            except Exception as exc:  # noqa: BLE001
                notes.append(f"cache: {exc}")

        if len(articles_raw) < max(4, limit // 2):
            from src.api.market_routes import _fetch_parallel_news

            fresh = _fetch_parallel_news(limit, q, topic)
            if fresh.articles:
                try:
                    upsert_news_rows([a.model_dump() for a in fresh.articles])
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"cache_write: {exc}")
                return fresh.model_copy(update={"source_notes": notes + (fresh.source_notes or [])})
            articles_raw = [a.model_dump() for a in fresh.articles]
            notes.extend(fresh.source_notes or [])

        articles = [NewsArticle(**row) for row in articles_raw[:limit]]
        return NewsRadarResponse(
            articles=articles,
            topics=sorted({a.topic for a in articles if a.topic}),
            as_of=date.today().isoformat(),
            source_notes=notes,
            cached=bool(prefer_cache and articles),
        )

    @staticmethod
    def sync_cache(*, bars: bool = True, news: bool = True) -> dict[str, Any]:
        from src.data.cache_store import run_full_sync, sync_daily_bars, sync_news_incremental

        if bars and news:
            return run_full_sync()
        out: dict[str, Any] = {}
        if bars:
            out["bars"] = sync_daily_bars()
        if news:
            out["news"] = sync_news_incremental()
        from src.data.cache_store import cache_stats

        out["stats"] = cache_stats()
        return out

    @staticmethod
    def cache_stats() -> dict[str, Any]:
        from src.data.cache_store import cache_stats

        return cache_stats()

    @staticmethod
    def intelligence_snapshot(**kwargs: Any) -> dict[str, Any]:
        from src.data.intelligence import build_intelligence_snapshot

        return build_intelligence_snapshot(**kwargs)

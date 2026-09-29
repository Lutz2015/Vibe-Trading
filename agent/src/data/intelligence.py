"""Intelligence snapshot — market + news + sentiment aggregation."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional


def build_intelligence_snapshot(
    *,
    news_limit: int = 12,
    q: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate multi-source intelligence for the Intelligence Center."""
    from src.data.cache_store import cache_stats, query_news, sync_news_incremental
    from src.data.gateway import DataGateway

    notes: List[str] = []

    # Refresh news cache best-effort (non-blocking failures).
    try:
        sync_news_incremental(limit=max(news_limit, 20))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"news_sync: {exc}")

    articles = query_news(limit=news_limit, q=q, topic=topic)
    if not articles:
        try:
            from src.api.market_routes import _fetch_parallel_news

            fresh = _fetch_parallel_news(news_limit, q, topic)
            articles = [a.model_dump() for a in fresh.articles]
            notes.extend(fresh.source_notes or [])
        except Exception as exc:  # noqa: BLE001
            notes.append(f"news_live: {exc}")

    market_summary: Dict[str, Any] = {}
    try:
        overview = DataGateway.market_overview(allow_stale=True)
        market_summary = {
            "as_of": getattr(overview, "as_of", date.today().isoformat()),
            "primary_provider": getattr(overview, "primary_provider", None)
            or getattr(overview, "source", None),
            "indices": [
                {"name": i.name, "change_pct": i.change_pct, "price": i.price}
                for i in (getattr(overview, "indices", None) or [])[:6]
            ],
            "hot_boards": [
                {"name": b.board_name, "change_pct": b.change_pct}
                for b in (getattr(overview, "hot_boards", None) or [])[:5]
            ],
        }
    except Exception as exc:  # noqa: BLE001
        notes.append(f"market: {exc}")

    sentiment_summary: Dict[str, Any] = {}
    try:
        from src.api.sentiment_routes import _get_sentiment_cached

        sent = _get_sentiment_cached()
        sentiment_summary = {
            "composite": sent.composite,
            "mode": sent.mode,
            "as_of": sent.as_of,
            "items": [
                {"title": it.title, "probability": it.probability, "delta24h": it.delta24h}
                for it in (sent.items or [])[:6]
            ],
        }
    except Exception as exc:  # noqa: BLE001
        notes.append(f"sentiment: {exc}")

    stats = cache_stats()
    pos = sum(1 for a in articles if a.get("signal") == "positive")
    neg = sum(1 for a in articles if a.get("signal") == "negative")

    return {
        "as_of": date.today().isoformat(),
        "market": market_summary,
        "news": {
            "articles": articles,
            "positive_count": pos,
            "negative_count": neg,
            "neutral_count": max(0, len(articles) - pos - neg),
        },
        "sentiment": sentiment_summary,
        "cache": stats,
        "source_notes": notes,
    }

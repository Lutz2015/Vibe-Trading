"""Unified data gateway (Tushare-first) for market, news, and fundamentals."""

from src.data.cache_store import cache_stats, run_full_sync, sync_daily_bars, sync_news_incremental
from src.data.gateway import DataGateway
from src.data.intelligence import build_intelligence_snapshot
from src.data.market_desk import build_market_overview
from src.data.tushare_probe import probe_tushare_capabilities

__all__ = [
    "DataGateway",
    "build_intelligence_snapshot",
    "build_market_overview",
    "cache_stats",
    "probe_tushare_capabilities",
    "run_full_sync",
    "sync_daily_bars",
    "sync_news_incremental",
]

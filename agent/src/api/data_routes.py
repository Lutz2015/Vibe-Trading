"""Unified data service routes (Tushare-first gateway)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from src.api.market_routes import MarketOverviewResponse, NewsRadarResponse
from src.data.gateway import DataGateway


class DataCapabilityItem(BaseModel):
    id: str
    label: str
    min_points: int = 0
    available: bool = False
    error: Optional[str] = None


class TushareCapabilities(BaseModel):
    configured: bool
    connected: bool
    message: str = ""
    capabilities: List[DataCapabilityItem] = Field(default_factory=list)


class DataCapabilitiesResponse(BaseModel):
    primary_provider: str
    tushare: TushareCapabilities
    fallback_providers: List[str] = Field(default_factory=list)


class DataHealthResponse(BaseModel):
    status: str
    provider: str


class CacheStatsResponse(BaseModel):
    db_path: str
    daily_bars: int
    news_articles: int
    streams: dict = Field(default_factory=dict)


class SyncCacheResponse(BaseModel):
    bars: dict = Field(default_factory=dict)
    news: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)


class IntelligenceSnapshotResponse(BaseModel):
    as_of: str
    market: dict = Field(default_factory=dict)
    news: dict = Field(default_factory=dict)
    sentiment: dict = Field(default_factory=dict)
    cache: dict = Field(default_factory=dict)
    source_notes: list[str] = Field(default_factory=list)


def register_data_routes(app: FastAPI, require_local_or_auth=None) -> None:
    """Mount ``/api/data/*`` routes."""

    deps = [Depends(require_local_or_auth)] if require_local_or_auth else []

    @app.get("/api/data/capabilities", response_model=DataCapabilitiesResponse, dependencies=deps)
    async def data_capabilities() -> Dict[str, Any]:
        return DataGateway.capabilities()

    @app.get("/api/data/health", response_model=DataHealthResponse, dependencies=deps)
    async def data_health() -> Dict[str, str]:
        return DataGateway.health()

    @app.get("/api/data/market/overview", response_model=MarketOverviewResponse, dependencies=deps)
    async def data_market_overview() -> MarketOverviewResponse:
        """Market Desk overview — Tushare-first indices/northbound + Eastmoney boards."""
        import asyncio

        return await asyncio.to_thread(DataGateway.market_overview)

    @app.get("/api/data/news/feed", response_model=NewsRadarResponse, dependencies=deps)
    async def data_news_feed(
        q: str | None = None,
        topic: str | None = None,
        limit: int = 20,
    ) -> NewsRadarResponse:
        import asyncio

        return await asyncio.to_thread(
            DataGateway.news_feed,
            q=q,
            topic=topic,
            limit=limit,
        )

    @app.post("/api/data/sync", response_model=SyncCacheResponse, dependencies=deps)
    async def data_sync_cache(bars: bool = True, news: bool = True) -> dict:
        import asyncio

        return await asyncio.to_thread(DataGateway.sync_cache, bars=bars, news=news)

    @app.get("/api/data/cache/stats", response_model=CacheStatsResponse, dependencies=deps)
    async def data_cache_stats() -> dict:
        return DataGateway.cache_stats()

    @app.get("/api/intelligence/snapshot", response_model=IntelligenceSnapshotResponse, dependencies=deps)
    async def intelligence_snapshot(
        q: str | None = None,
        topic: str | None = None,
        limit: int = 12,
    ) -> dict:
        import asyncio

        return await asyncio.to_thread(
            DataGateway.intelligence_snapshot,
            news_limit=limit,
            q=q,
            topic=topic,
        )

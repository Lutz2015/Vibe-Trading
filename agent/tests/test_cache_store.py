"""Tests for DuckDB local cache store."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data import cache_store as cs


@pytest.fixture()
def isolated_cache(tmp_path: Path, monkeypatch):
    db = tmp_path / "data.duckdb"
    monkeypatch.setattr(cs, "cache_db_path", lambda: db)
    monkeypatch.setattr(cs, "_conn", None)
    yield db
    monkeypatch.setattr(cs, "_conn", None)


def test_upsert_and_query_news(isolated_cache: Path) -> None:
    added = cs.upsert_news_rows(
        [
            {
                "title": "Test headline A",
                "url": "https://example.com/a",
                "source": "test",
                "published": "2026-05-27",
                "signal": "neutral",
            },
            {
                "title": "Test headline B",
                "url": "https://example.com/b",
                "source": "test",
                "published": "2026-05-27",
                "signal": "positive",
            },
        ]
    )
    assert added == 2
    rows = cs.query_news(limit=10)
    assert len(rows) == 2
    assert rows[0]["title"].startswith("Test headline")


def test_cache_stats_after_news(isolated_cache: Path) -> None:
    cs.upsert_news_rows([{"title": "One", "source": "sina"}])
    stats = cs.cache_stats()
    assert stats["news_articles"] >= 1
    assert str(isolated_cache) in stats["db_path"]


def test_default_bar_symbols_fallback() -> None:
    symbols = cs.default_bar_symbols()
    assert "000001.SZ" in symbols
    assert len(symbols) >= 4

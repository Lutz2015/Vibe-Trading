"""Tests for Market Desk data service (Tushare-first merge)."""

from __future__ import annotations

from unittest.mock import patch

from src.api.market_routes import QuoteItem
from src.data.market_desk import merge_index_quotes


def test_merge_index_quotes_prefers_tushare_for_a_share() -> None:
    tushare = [
        QuoteItem(
            symbol="1.000001",
            name="上证指数",
            market="CN",
            price=3900.0,
            change_pct=1.2,
            source="tushare",
        )
    ]
    em = [
        QuoteItem(symbol="1.000001", name="上证指数", market="CN", price=3800.0, change_pct=-1.0, source="eastmoney"),
        QuoteItem(symbol="100.HSI", name="恒生指数", market="HK", price=26000.0, change_pct=0.5, source="eastmoney"),
    ]
    merged = merge_index_quotes(tushare, em)
    by_symbol = {item.symbol: item for item in merged}
    assert by_symbol["1.000001"].source == "tushare"
    assert by_symbol["1.000001"].price == 3900.0
    assert by_symbol["100.HSI"].price == 26000.0


def test_build_market_overview_delegates_to_layers() -> None:
    from src.data.market_desk import build_market_overview

    fake_indices = [
        QuoteItem(symbol="1.000001", name="上证指数", market="CN", price=1.0, change_pct=0.1, source="tushare"),
    ]
    with patch("src.data.market_desk.fetch_indices_tushare", return_value=fake_indices):
        with patch("src.data.market_desk.merge_index_quotes", return_value=fake_indices):
            with patch("src.api.market_routes._fetch_indices", return_value=[]):
                with patch("src.api.market_routes._fetch_board_list", return_value=[]):
                    with patch("src.api.market_routes._fetch_hot_stocks", return_value=[]):
                        with patch("src.data.market_desk.fetch_northbound_with_fallback", return_value={"source": "tushare", "total_net": 1.0}):
                            with patch("src.api.market_routes._fetch_limit_stats", return_value=None):
                                overview = build_market_overview()
    assert overview.primary_provider == "tushare"
    assert overview.indices[0].source == "tushare"

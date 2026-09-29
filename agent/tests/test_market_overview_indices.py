"""Market overview index + board-leader fetch contracts (Eastmoney).

Domestic-first: A-share + HK majors only (US indices are out of scope).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

from src.api import market_routes as mr


def test_fetch_indices_uses_ulist_secids_and_maps_all_majors() -> None:
    rows = [
        {"f12": "000001", "f13": 1, "f14": "上证指数", "f2": 3823.25, "f3": -1.67},
        {"f12": "399001", "f13": 0, "f14": "深证成指", "f2": 12858.58, "f3": -3.44},
        {"f12": "399006", "f13": 0, "f14": "创业板指", "f2": 3139.66, "f3": -4.54},
        {"f12": "000300", "f13": 1, "f14": "沪深300", "f2": 4339.75, "f3": -2.24},
        {"f12": "HSI", "f13": 100, "f14": "恒生指数", "f2": 26000.0, "f3": 0.52},
        {"f12": "HSTECH", "f13": 100, "f14": "恒生科技", "f2": 5600.0, "f3": 0.88},
    ]
    calls: List[Dict[str, Any]] = []

    def fake_em_get(path: str, params: Dict[str, Any], **kwargs: Any) -> Any:
        calls.append({"path": path, "params": params, "kwargs": kwargs})
        assert path == "/api/qt/ulist.np/get"
        return {"data": {"diff": rows}}

    with patch.object(mr, "_em_get", side_effect=fake_em_get):
        items = mr._fetch_indices()

    assert [item.symbol for item in items] == [secid for secid, _, _ in mr.INDEX_SECIDS]
    by_name = {item.name: item for item in items}
    assert by_name["上证指数"].price == 3823.25
    assert by_name["沪深300"].change_pct == -2.24
    assert by_name["恒生指数"].price == 26000.0
    assert by_name["恒生科技"].change_pct == 0.88
    assert all(item.error is None for item in items)
    assert "100.HSI" in calls[0]["params"]["secids"]
    assert "100.DJIA" not in calls[0]["params"]["secids"]


def test_fetch_indices_falls_back_to_stock_get_for_missing_row() -> None:
    def fake_em_get(path: str, params: Dict[str, Any], **kwargs: Any) -> Any:
        if path == "/api/qt/ulist.np/get":
            return {
                "data": {
                    "diff": [
                        {"f12": "HSTECH", "f13": 100, "f14": "恒生科技", "f2": 5600.0, "f3": 0.88},
                    ]
                }
            }
        assert path == "/api/qt/stock/get"
        secid = params["secid"]
        if secid == "1.000001":
            return {"data": {"f43": 3823.96, "f58": "上证指数", "f170": -1.66}}
        return {"data": {}}

    with patch.object(mr, "_em_get", side_effect=fake_em_get):
        items = mr._fetch_indices()

    by_symbol = {item.symbol: item for item in items}
    assert by_symbol["1.000001"].price == 3823.96
    assert by_symbol["100.HSTECH"].price == 5600.0
    assert by_symbol["0.399001"].error == "unavailable"


def test_fetch_indices_falls_back_to_sina_when_eastmoney_down() -> None:
    sina_body = (
        'var hq_str_s_sh000001="上证指数,3825.2807,-63.0931,-1.62,4254565,75902151";\n'
        'var hq_str_s_sz399001="深证成指,12868.63,-448.340,-3.37,507813513,84699102";\n'
        'var hq_str_s_sz399006="创业板指,3142.81,-146.13,-4.45,1,1";\n'
        'var hq_str_s_sh000300="沪深300,4339.75,-98.19,-2.21,1,1";\n'
        'var hq_str_int_hsi="恒生指数,26000.0,135.0,0.52";\n'
        'var hq_str_int_hstech="恒生科技,5600.0,49.0,0.88";\n'
    ).encode("gbk")

    class FakeResponse:
        def read(self) -> bytes:
            return sina_body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    with (
        patch.object(mr, "_em_get", side_effect=RuntimeError("eastmoney down")),
        patch("urllib.request.urlopen", return_value=FakeResponse()),
    ):
        items = mr._fetch_indices()

    assert [item.name for item in items] == [
        "上证指数",
        "深证成指",
        "创业板指",
        "沪深300",
        "恒生指数",
        "恒生科技",
    ]
    assert items[0].price == 3825.2807
    assert items[4].price == 26000.0
    assert all(item.source == "sina" for item in items)


def test_build_overview_fetches_leaders_for_every_board() -> None:
    boards = [
        mr.HotBoard(board_code="BK1", board_name="A", kind="industry"),
        mr.HotBoard(board_code="BK2", board_name="B", kind="industry"),
        mr.HotBoard(board_code="BK3", board_name="C", kind="concept"),
    ]

    with (
        patch.object(mr, "_fetch_indices", return_value=[]),
        patch.object(
            mr,
            "_fetch_board_list",
            side_effect=[
                boards[:2],
                [boards[2]],
            ],
        ),
        patch.object(
            mr,
            "_fetch_board_leaders",
            side_effect=lambda code, limit=4: [
                mr.QuoteItem(symbol=f"{code}.X", name=f"{code}-lead", price=1.0, change_pct=1.0)
            ],
        ) as leaders,
        patch.object(mr, "_fetch_hot_stocks", return_value=[]),
    ):
        overview = mr._build_overview()

    assert [board.board_code for board in overview.hot_boards] == ["BK1", "BK2", "BK3"]
    assert leaders.call_count == 3
    assert all(board.leaders for board in overview.hot_boards)

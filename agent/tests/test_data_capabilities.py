"""Tests for unified data service / Tushare capability probe."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data.tushare_probe import probe_tushare_capabilities


def test_probe_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    from src.config.accessor import reset_env_config

    reset_env_config()
    result = probe_tushare_capabilities()
    assert result["configured"] is False
    assert result["connected"] is False
    assert result["capabilities"] == []


def test_probe_with_mock_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    from src.config.accessor import reset_env_config

    reset_env_config()

    mock_pro = MagicMock()

    def _daily(**kwargs):  # noqa: ANN003
        import pandas as pd

        return pd.DataFrame({"close": [10.0]})

    mock_pro.trade_cal.return_value = MagicMock(empty=False)
    mock_pro.daily.side_effect = _daily
    mock_pro.hk_daily.side_effect = _daily
    mock_pro.index_daily.side_effect = _daily
    mock_pro.moneyflow_hsgt.side_effect = Exception("积分不足")
    mock_pro.news.side_effect = Exception("积分不足")
    mock_pro.stk_mins.side_effect = Exception("积分不足")

    with patch("src.data.tushare_probe._get_pro", return_value=mock_pro):
        result = probe_tushare_capabilities()

    assert result["configured"] is True
    assert result["connected"] is True
    available = [c for c in result["capabilities"] if c["available"]]
    assert len(available) >= 4

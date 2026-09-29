"""Probe Tushare Pro capabilities for the Settings / Trading Center UI."""

from __future__ import annotations

import logging
from typing import Any

from backtest.loaders.tushare import TUSHARE_TOKEN_PLACEHOLDERS

logger = logging.getLogger(__name__)

# Lightweight probes ordered by typical point tier (low → high).
_PROBE_CALLS: tuple[tuple[str, str, int, dict[str, Any]], ...] = (
    (
        "trade_cal",
        "交易日历",
        0,
        {"exchange": "SSE", "start_date": "20250101", "end_date": "20250105"},
    ),
    (
        "daily",
        "A股日线",
        120,
        {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250105"},
    ),
    (
        "hk_daily",
        "港股日线",
        120,
        {"ts_code": "00700.HK", "start_date": "20250101", "end_date": "20250105"},
    ),
    (
        "index_daily",
        "指数日线",
        120,
        {"ts_code": "000300.SH", "start_date": "20250101", "end_date": "20250105"},
    ),
    (
        "moneyflow_hsgt",
        "北向资金",
        2000,
        {"start_date": "20250101", "end_date": "20250105"},
    ),
    (
        "news",
        "新闻快讯",
        2000,
        {"src": "sina", "start_date": "20250101", "end_date": "20250102"},
    ),
    (
        "stk_mins",
        "A股分钟线",
        2000,
        {
            "ts_code": "000001.SZ",
            "freq": "1min",
            "start_date": "20250102 09:30:00",
            "end_date": "20250102 09:35:00",
        },
    ),
)


def _token_configured() -> bool:
    from src.config.accessor import get_env_config

    return (
        get_env_config().data.tushare_token.strip() not in TUSHARE_TOKEN_PLACEHOLDERS
    )


def _get_pro():
    import tushare as ts

    from src.config.accessor import get_env_config

    token = get_env_config().data.tushare_token.strip()
    ts.set_token(token)
    return ts.pro_api()


def probe_tushare_capabilities() -> dict[str, Any]:
    """Return structured Tushare capability probe for UI display."""
    configured = _token_configured()
    if not configured:
        return {
            "configured": False,
            "connected": False,
            "message": "TUSHARE_TOKEN 未配置",
            "capabilities": [],
        }

    try:
        pro = _get_pro()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tushare pro_api init failed: %s", exc)
        return {
            "configured": True,
            "connected": False,
            "message": f"初始化失败: {exc}",
            "capabilities": [],
        }

    capabilities: list[dict[str, Any]] = []
    connected = False
    for api_name, label, min_points, kwargs in _PROBE_CALLS:
        entry: dict[str, Any] = {
            "id": api_name,
            "label": label,
            "min_points": min_points,
            "available": False,
            "error": None,
        }
        try:
            fn = getattr(pro, api_name, None)
            if fn is None:
                entry["error"] = "接口不存在"
            else:
                frame = fn(**kwargs)
                if frame is not None and not getattr(frame, "empty", True):
                    entry["available"] = True
                    connected = True
                elif frame is not None and getattr(frame, "empty", False):
                    # Auth OK but no rows for the probe window — still counts.
                    entry["available"] = True
                    connected = True
                else:
                    entry["error"] = "无数据返回"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:200]
        capabilities.append(entry)

    available_count = sum(1 for c in capabilities if c["available"])
    return {
        "configured": True,
        "connected": connected,
        "message": (
            f"已探测 {available_count}/{len(capabilities)} 项接口"
            if connected
            else "Token 已配置但接口探测失败，请检查积分或网络"
        ),
        "capabilities": capabilities,
    }

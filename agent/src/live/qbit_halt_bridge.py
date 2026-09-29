"""Bidirectional sync between Qbit risk-engine kill switch and live ``trip_halt``."""

from __future__ import annotations

import logging
from typing import Any

from src.live.halt import clear_halt, halt_flag_set, read_halt, trip_halt
from src.live.qbit_gate import load_qbit_automation_config, resolve_live_broker

logger = logging.getLogger(__name__)

QBIT_HALT_REASON = "qbit_risk_kill_switch"


def resolve_qbit_live_broker() -> str:
    """Best-effort live broker key from Qbit automation config."""
    try:
        return resolve_live_broker(load_qbit_automation_config())
    except Exception:
        return ""


def live_halt_blocks_qbit(broker: str | None = None) -> bool:
    """True when a live HALT sentinel would block Qbit automation."""
    broker_key = (broker or resolve_qbit_live_broker()).strip().lower()
    if halt_flag_set(None):
        return True
    return bool(broker_key and halt_flag_set(broker_key))


def _set_qbit_kill_override(enabled: bool) -> None:
    """Set in-process Qbit risk-engine kill switch override (embedded platform)."""
    try:
        import sys

        mod = sys.modules.get("qbit_risk_engine")
        if mod is None:
            return
        mod._kill_switch_override = enabled
        if enabled:
            mod._trading_enabled_override = False
    except Exception:
        logger.debug("qbit kill override skipped", exc_info=True)


def _is_qbit_owned_halt(broker: str | None) -> bool:
    meta = read_halt(broker)
    return bool(meta and meta.get("reason") == QBIT_HALT_REASON)


def trip_live_halt_for_qbit(*, broker: str | None = None) -> None:
    """Trip live HALT when Qbit kill switch is enabled."""
    broker_key = (broker or resolve_qbit_live_broker()).strip().lower()
    target = broker_key or None
    if target and not _is_qbit_owned_halt(target):
        trip_halt(by="file", reason=QBIT_HALT_REASON, broker=target)
    elif not target and not _is_qbit_owned_halt(None):
        trip_halt(by="file", reason=QBIT_HALT_REASON)
    elif target and halt_flag_set(None):
        # Global halt already blocks; avoid redundant broker sentinel.
        pass


def clear_qbit_owned_halts(*, broker: str | None = None) -> None:
    """Clear HALT sentinels written by the Qbit kill switch (never user/cli halts)."""
    broker_key = (broker or resolve_qbit_live_broker()).strip().lower()
    if _is_qbit_owned_halt(None):
        clear_halt(None)
    if broker_key and _is_qbit_owned_halt(broker_key):
        clear_halt(broker_key)


def on_halt_changed(
    *,
    tripped: bool,
    broker: str | None,
    reason: str = "",
    by: str = "",
) -> None:
    """React to live HALT trip/clear — keep Qbit risk-engine kill switch aligned."""
    del by  # attribution only; sync is existence-based
    if reason == QBIT_HALT_REASON and tripped:
        return
    if tripped:
        _set_qbit_kill_override(True)
        return
    broker_key = resolve_qbit_live_broker()
    if live_halt_blocks_qbit(broker_key):
        return
    _set_qbit_kill_override(False)


def sync_qbit_kill_switch(enabled: bool) -> None:
    """Apply Qbit kill switch and mirror to live HALT sentinels."""
    _set_qbit_kill_override(enabled)
    if enabled:
        trip_live_halt_for_qbit()
    else:
        clear_qbit_owned_halts()


def effective_qbit_kill_switch(yaml_kill: bool, override: bool | None) -> bool:
    """Merge YAML, in-memory override, and live HALT into one kill-switch flag."""
    base = override if override is not None else yaml_kill
    if live_halt_blocks_qbit():
        return True
    return bool(base)


def _source_from_meta(meta: dict[str, Any]) -> str:
    reason = str(meta.get("reason") or "")
    by = str(meta.get("by") or "")
    if reason == QBIT_HALT_REASON:
        return "qbit"
    if by == "frontend":
        return "frontend"
    if by == "cli":
        return "cli"
    if by == "file":
        return "file"
    return by or "unknown"


def describe_qbit_halt_state() -> dict[str, Any]:
    """Structured halt snapshot for Qbit ops UI (scope + attribution)."""
    broker = resolve_qbit_live_broker()
    if halt_flag_set(None):
        meta = read_halt(None) or {}
        return {
            "active": True,
            "scope": "global",
            "source": _source_from_meta(meta),
            "broker": "",
            "reason": str(meta.get("reason") or ""),
            "by": str(meta.get("by") or ""),
        }
    if broker and halt_flag_set(broker):
        meta = read_halt(broker) or {}
        return {
            "active": True,
            "scope": "broker",
            "source": _source_from_meta(meta),
            "broker": broker,
            "reason": str(meta.get("reason") or ""),
            "by": str(meta.get("by") or ""),
        }
    return {
        "active": False,
        "scope": "none",
        "source": "none",
        "broker": broker,
        "reason": "",
        "by": "",
    }

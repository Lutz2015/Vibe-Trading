"""Qbit automation live gate — mandate + halt + env fail-closed checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.live.halt import halt_flag_set
from src.live.mandate.store import load_mandate
from src.live.sdk_order_gate import _is_expired


@dataclass
class QbitLiveGateResult:
    ready: bool
    execution_mode: str
    broker: str = ""
    mandate_ok: bool = False
    halt_ok: bool = True
    live_env_ok: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "execution_mode": self.execution_mode,
            "broker": self.broker,
            "mandate_ok": self.mandate_ok,
            "halt_ok": self.halt_ok,
            "live_env_ok": self.live_env_ok,
            "reasons": list(self.reasons),
        }


def resolve_live_broker(config: dict[str, Any] | None) -> str:
    cfg = config or {}
    return str(cfg.get("live_broker") or cfg.get("broker") or "").strip().lower()


def resolve_execution_mode(config: dict[str, Any] | None) -> str:
    cfg = config or {}
    return str(cfg.get("execution_mode", cfg.get("mode", "paper"))).strip().lower()


def _live_env_enabled() -> bool:
    return os.getenv("EXECUTION_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def check_qbit_live_gate(
    config: dict[str, Any] | None = None,
    *,
    broker: str | None = None,
) -> QbitLiveGateResult:
    """Return whether Qbit automation may run or route orders in live mode."""
    mode = resolve_execution_mode(config)
    broker_key = (broker or resolve_live_broker(config)).strip().lower()
    reasons: list[str] = []

    if mode != "live":
        return QbitLiveGateResult(
            ready=True,
            execution_mode=mode,
            broker=broker_key,
            mandate_ok=True,
            halt_ok=True,
            live_env_ok=True,
        )

    live_env_ok = _live_env_enabled()
    if not live_env_ok:
        reasons.append("execution_live_disabled")

    if not broker_key:
        reasons.append("live_broker_not_configured")

    mandate_ok = False
    if broker_key:
        mandate = load_mandate(broker_key)
        if mandate is None:
            reasons.append("no_committed_mandate")
        elif _is_expired(mandate):
            reasons.append("mandate_expired")
        else:
            mandate_ok = True

    halt_ok = True
    if halt_flag_set(None) or (broker_key and halt_flag_set(broker_key)):
        halt_ok = False
        reasons.append("halt_tripped")

    ready = bool(broker_key) and mandate_ok and halt_ok and live_env_ok
    return QbitLiveGateResult(
        ready=ready,
        execution_mode=mode,
        broker=broker_key,
        mandate_ok=mandate_ok,
        halt_ok=halt_ok,
        live_env_ok=live_env_ok,
        reasons=reasons,
    )


def load_qbit_automation_config() -> dict[str, Any]:
    """Load Qbit auto-trading YAML config (best-effort)."""
    import os

    import yaml

    qbit_root = Path(__file__).resolve().parents[2] / "qbit"
    path = Path(
        os.getenv("AUTO_TRADING_CONFIG_PATH", str(qbit_root / "configs" / "auto-trading.yaml"))
    )
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def assert_qbit_live_gate(config: dict[str, Any] | None) -> None:
    """Raise RuntimeError when live automation is configured but gate is not ready."""
    result = check_qbit_live_gate(config)
    if result.execution_mode == "live" and not result.ready:
        detail = ", ".join(result.reasons) or "live_gate_blocked"
        raise RuntimeError(f"live_gate_blocked:{detail}")

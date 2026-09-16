"""Read-only access to the embedded Qbit quant platform for the Agent."""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agent.tools import BaseTool


def _json(status: str, **payload: Any) -> str:
    return json.dumps({"status": status, **payload}, ensure_ascii=False, default=str)


class QbitQuantTool(BaseTool):
    """Inspect Qbit state without exposing live order or operations controls."""

    name = "qbit_quant"
    description = (
        "Read-only Qbit quant platform access. Actions: health, ops_status, "
        "ledger_snapshot, monitoring_metrics, strategy_repository. "
        "Paper trading state only; never use this tool to place live orders."
    )
    is_readonly = True
    repeatable = True
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "health",
                    "ops_status",
                    "ledger_snapshot",
                    "monitoring_metrics",
                    "strategy_repository",
                ],
                "description": "Read-only Qbit data to inspect",
            }
        },
        "required": ["action"],
    }

    def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "health")
        try:
            from src.qbit.platform import create_qbit_app

            create_qbit_app()
            modules = sys.modules
            if action == "health":
                orchestrator = modules["qbit_trading_orchestrator"]
                return _json(
                    "ok",
                    action=action,
                    service="qbit-quant-platform",
                    mode="embedded",
                    scheduler_running=bool(orchestrator._scheduler_running),
                )
            if action == "ops_status":
                risk = modules["qbit_risk_engine"]
                return _json("ok", action=action, **risk.ops_status())
            if action == "ledger_snapshot":
                ledger = modules["qbit_portfolio_ledger"]
                snapshot = ledger.build_snapshot()
                return _json("ok", action=action, snapshot=snapshot.model_dump())
            if action == "monitoring_metrics":
                monitoring = modules["qbit_monitoring_engine"]
                metrics = monitoring.get_metrics()
                return _json("ok", action=action, metrics=metrics.model_dump())
            if action == "strategy_repository":
                registry = modules["qbit_strategy_registry"]
                items = registry.list_strategy_repository()
                return _json(
                    "ok",
                    action=action,
                    strategies=[item.model_dump() for item in items],
                )
            return _json("error", error=f"unknown action: {action}")
        except Exception as exc:  # noqa: BLE001 - tool returns structured errors
            return _json("error", action=action, error=str(exc))

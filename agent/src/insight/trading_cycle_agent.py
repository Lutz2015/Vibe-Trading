"""Trading-cycle Agent — post-cycle decision log via scoped mini ReAct."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.insight.module_copilot import CopilotResult, CopilotToolStep, run_module_copilot

logger = logging.getLogger(__name__)

MAX_HISTORY = 20


@dataclass
class CycleAgentLog:
    as_of: str
    rebalance_id: str | None = None
    skipped: bool = False
    skip_reason: str = ""
    ok: bool = False
    text: str = ""
    error: str | None = None
    tool_steps: List[CopilotToolStep] = field(default_factory=list)
    iterations: int = 0
    model: str | None = None

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of,
            "rebalance_id": self.rebalance_id,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "ok": self.ok,
            "text": self.text,
            "error": self.error,
            "tool_steps": [
                {"name": s.name, "ok": s.ok, "summary": s.summary} for s in self.tool_steps
            ],
            "iterations": self.iterations,
            "model": self.model,
        }


def agent_log_enabled(config: dict[str, Any]) -> bool:
    """Paper mode enables agent logs by default; live requires explicit opt-in."""
    block = config.get("agent_log") or {}
    if "enabled" in block:
        return bool(block["enabled"])
    mode = str(config.get("execution_mode", config.get("mode", "paper"))).lower()
    return mode == "paper"


def build_cycle_brief(payload: dict[str, Any]) -> str:
    """Render a structured brief from a run_cycle result + portfolio context."""
    lines: List[str] = ["【自动交易循环】"]
    lines.append(f"- as_of: {payload.get('as_of')}")
    lines.append(f"- skipped: {payload.get('skipped')}")
    if payload.get("skip_reason"):
        lines.append(f"- skip_reason: {payload.get('skip_reason')}")
    lines.append(f"- rebalance_executed: {payload.get('rebalance_executed')}")
    lines.append(f"- orders_submitted: {payload.get('orders_submitted')}")
    lines.append(f"- orders_filled: {payload.get('orders_filled')}")
    if payload.get("message"):
        lines.append(f"- message: {payload.get('message')}")

    portfolio = payload.get("portfolio") or {}
    if portfolio:
        lines.append("【组合快照】")
        lines.append(f"- portfolio_value: {portfolio.get('portfolio_value')}")
        lines.append(f"- cash: {portfolio.get('cash')}")
        for pos in (portfolio.get("positions") or [])[:12]:
            if not isinstance(pos, dict):
                continue
            lines.append(
                f"- {pos.get('symbol')} qty={pos.get('quantity')} "
                f"mv={pos.get('market_value')} strategy={pos.get('strategy_id')}"
            )

    strategies = payload.get("strategies") or []
    if strategies:
        lines.append("【策略选股】")
        for row in strategies:
            if not isinstance(row, dict):
                continue
            picked = row.get("picked") or []
            lines.append(
                f"- {row.get('strategy_id')}: {', '.join(str(s) for s in picked)}"
            )
            if row.get("rule"):
                lines.append(f"  rule: {row.get('rule')}")

    orders = payload.get("orders") or []
    if orders:
        lines.append("【本轮订单】")
        for row in orders[:15]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('side')} {row.get('symbol')} x{row.get('quantity')} "
                f"@{row.get('price')} status={row.get('status')}"
                + (f" ({row.get('skip_reason')})" if row.get("skip_reason") else "")
            )

    ops = payload.get("ops") or {}
    if ops:
        lines.append("【风控闸】")
        lines.append(f"- kill_switch: {ops.get('kill_switch')}")
        lines.append(f"- trading_enabled: {ops.get('trading_enabled')}")

    return "\n".join(lines)


def _skipped_template(payload: dict[str, Any], locale: str) -> str:
    reason = str(payload.get("skip_reason") or "unknown")
    zh = locale.lower().startswith("zh")
    if zh:
        return f"本轮自动交易未执行（原因：{reason}）。建议检查调度窗口、策略启用状态与交易时段。"
    return f"Cycle skipped ({reason}). Check schedule window, enabled strategies, and trading hours."


def generate_cycle_decision_log(
    payload: dict[str, Any],
    *,
    locale: str = "zh-CN",
    max_iterations: int = 4,
) -> CycleAgentLog:
    """Produce an Agent decision log for one orchestrator cycle."""
    as_of = str(payload.get("as_of") or "")
    skipped = bool(payload.get("skipped"))
    skip_reason = str(payload.get("skip_reason") or "")
    rebalance_id = payload.get("rebalance_id")

    if skipped:
        return CycleAgentLog(
            as_of=as_of,
            rebalance_id=str(rebalance_id) if rebalance_id else None,
            skipped=True,
            skip_reason=skip_reason,
            ok=True,
            text=_skipped_template(payload, locale),
        )

    brief = build_cycle_brief(payload)
    zh = locale.lower().startswith("zh")
    prompt = (
        "请作为自动交易值班 Agent，解读本轮 paper 再平衡循环：\n"
        "1) 策略选股是否符合规则；2) 订单与持仓变化是否合理；"
        "3) 风险与异常（skipped/failed 订单）；4) 下一轮应观察什么。\n"
        "只基于下列结构化事实，不要编造数字。\n\n"
        f"{brief}"
        if zh
        else (
            "As the auto-trading duty agent, interpret this paper rebalance cycle:\n"
            "1) Do picks match the rule; 2) Are orders/positions sensible; "
            "3) Risks/anomalies; 4) What to watch next. Use ONLY the facts below.\n\n"
            f"{brief}"
        )
    )

    try:
        result: CopilotResult = run_module_copilot(
            "quant",
            prompt,
            locale=locale,
            max_iterations=max(1, min(max_iterations, 6)),
        )
        return CycleAgentLog(
            as_of=as_of,
            rebalance_id=str(rebalance_id) if rebalance_id else None,
            skipped=False,
            ok=result.ok,
            text=result.text,
            error=result.error,
            tool_steps=result.tool_steps,
            iterations=result.iterations,
            model=result.model,
        )
    except Exception as exc:  # noqa: BLE001 — cycle must not fail
        logger.warning("trading cycle agent failed: %s", exc)
        return CycleAgentLog(
            as_of=as_of,
            rebalance_id=str(rebalance_id) if rebalance_id else None,
            skipped=False,
            ok=False,
            text="",
            error=str(exc)[:300],
        )


def persist_agent_log(state: dict[str, Any], entry: CycleAgentLog) -> dict[str, Any]:
    """Append decision log to automation state (ring buffer)."""
    record = entry.to_state_dict()
    state["last_agent_log"] = record
    history = list(state.get("agent_log_history") or [])
    history.append(record)
    state["agent_log_history"] = history[-MAX_HISTORY:]
    return state

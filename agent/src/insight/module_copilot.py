"""Module Copilot — lightweight ReAct loop with per-module tool whitelists."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.agent.context import ContextBuilder
from src.config.limits import truncate_tool_result
from src.providers.chat import ChatLLM, LLMResponse
from src.tools import build_filtered_registry

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
TIMEOUT_SECONDS = 90
_KEEP_RECENT_TOOLS = 3

# Read-only tools scoped to each module surface.
MODULE_TOOLSETS: dict[str, list[str]] = {
    "market": [
        "get_market_data",
        "get_northbound_flow",
        "get_fund_flow",
        "screen_market",
        "get_sector_info",
    ],
    "news": ["get_stock_news", "web_search", "read_url"],
    "sentiment": ["sentiment", "prediction_market", "get_stock_news"],
    "logic_chain": [
        "get_market_data",
        "get_stock_news",
        "web_search",
        "screen_market",
    ],
    "portfolio": [
        "portfolio_summary",
        "portfolio_risk_xray",
        "get_market_data",
    ],
    "quant": [
        "qbit_quant",
        "list_strategies",
        "query_strategies",
        "backtest",
    ],
    "strategy_gen": ["list_strategies", "query_strategies", "backtest"],
    "backtest": ["backtest", "factor_analysis", "list_strategies"],
    "intelligence": [
        "get_stock_news",
        "sentiment",
        "get_market_data",
        "get_northbound_flow",
        "web_search",
        "read_url",
    ],
}

_SYSTEM_ZH = (
    "你是 Personal-Trading 模块内嵌投研 Copilot。只能使用提供的工具获取数据，"
    "不得编造数字。输出简体中文：先 3 条要点，再 1 句风险提示。"
    "语气专业克制，避免喊单；数据不足时明确说明未检索到。"
)

_SYSTEM_EN = (
    "You are the Personal-Trading module copilot. Use ONLY the provided tools "
    "for data — never invent numbers. Reply in English: 3 bullets + one risk note. "
    "Professional and cautious; say clearly when data was not retrieved."
)


@dataclass
class CopilotToolStep:
    name: str
    ok: bool = True
    summary: str = ""


@dataclass
class CopilotResult:
    ok: bool
    kind: str
    text: str = ""
    model: Optional[str] = None
    error: Optional[str] = None
    tool_steps: List[CopilotToolStep] = field(default_factory=list)
    iterations: int = 0


def _zh(locale: str) -> bool:
    loc = (locale or "zh-CN").lower()
    return loc.startswith("zh") or loc in ("", "auto")


def _tool_summary(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "empty result"
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            status = payload.get("status")
            if status:
                err = payload.get("error") or payload.get("message")
                if err:
                    return f"{status}: {str(err)[:80]}"
                return str(status)
    except json.JSONDecodeError:
        pass
    return text[:120] + ("…" if len(text) > 120 else "")


def _tool_ok(raw: str) -> bool:
    try:
        payload = json.loads(raw or "")
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).lower()
            if status in {"error", "failed", "failure"}:
                return False
            if payload.get("registry_incomplete"):
                return False
    except json.JSONDecodeError:
        pass
    return True


def _build_system_prompt(kind: str, registry, locale: str) -> str:
    base = _SYSTEM_ZH if _zh(locale) else _SYSTEM_EN
    lines = [base, "", f"Module: {kind}", "", "Available tools:"]
    for definition in registry.get_definitions():
        fn = definition.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        desc = str(fn.get("description") or name).strip()
        lines.append(f"- {name}: {desc[:160]}")
    return "\n".join(lines)


def run_module_copilot(
    kind: str,
    user_message: str,
    *,
    locale: str = "zh-CN",
    max_iterations: int = MAX_ITERATIONS,
) -> CopilotResult:
    """Run a scoped mini ReAct loop for one module page."""
    normalized = (kind or "").strip()
    tool_names = MODULE_TOOLSETS.get(normalized)
    if not tool_names:
        return CopilotResult(ok=False, kind=normalized, error=f"unsupported copilot kind: {kind}")

    registry = build_filtered_registry(tool_names, include_shell_tools=False)
    if not registry.get_definitions():
        return CopilotResult(
            ok=False,
            kind=normalized,
            error="no copilot tools available (check optional dependencies)",
        )

    llm, init_error = _try_chat_llm()
    if init_error or llm is None:
        return CopilotResult(ok=False, kind=normalized, error=init_error or "LLM 未就绪")

    try:
        return _run_loop(
            llm=llm,
            registry=registry,
            kind=normalized,
            user_message=user_message,
            locale=locale,
            max_iterations=max(1, min(max_iterations, MAX_ITERATIONS)),
        )
    finally:
        try:
            llm.close()
        except Exception:  # noqa: BLE001
            pass


def _try_chat_llm():
    from src.providers.chat import try_chat_llm

    return try_chat_llm()


def _run_loop(
    *,
    llm: ChatLLM,
    registry,
    kind: str,
    user_message: str,
    locale: str,
    max_iterations: int,
) -> CopilotResult:
    system = _build_system_prompt(kind, registry, locale)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    tool_steps: List[CopilotToolStep] = []
    last_text = ""
    t0 = time.monotonic()
    wrap_up_at = max(1, int(max_iterations * 0.75))

    for iteration in range(max_iterations):
        if time.monotonic() - t0 > TIMEOUT_SECONDS:
            return CopilotResult(
                ok=bool(last_text),
                kind=kind,
                text=last_text or "分析超时，请重试或缩小问题范围。",
                model=getattr(llm, "model_name", None),
                error=None if last_text else "timeout",
                tool_steps=tool_steps,
                iterations=iteration,
            )

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if len(tool_msgs) > _KEEP_RECENT_TOOLS:
            for msg in tool_msgs[:-_KEEP_RECENT_TOOLS]:
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 100:
                    msg["content"] = "[cleared]"

        if iteration == wrap_up_at:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[SYSTEM] Stop calling tools and output your final analysis "
                        "as plain text now."
                    ),
                }
            )

        is_last = iteration == max_iterations - 1
        tool_defs = None if is_last else registry.get_definitions()
        remaining = max(10, int(TIMEOUT_SECONDS - (time.monotonic() - t0)))

        try:
            response: LLMResponse = llm.stream_chat(
                messages,
                tools=tool_defs,
                timeout=remaining,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("module copilot LLM failed kind=%s iter=%s: %s", kind, iteration, exc)
            message = str(exc)[:300]
            if "api_key" in message.lower():
                message = "LLM 未就绪：请在「设置」配置提供商与 API 密钥后重试。"
            return CopilotResult(
                ok=bool(last_text),
                kind=kind,
                text=last_text,
                model=getattr(llm, "model_name", None),
                error=message,
                tool_steps=tool_steps,
                iterations=iteration + 1,
            )

        if response.content and len(response.content.strip()) > 10:
            last_text = response.content.strip()

        if not response.has_tool_calls:
            text = (response.content or last_text or "").strip()
            return CopilotResult(
                ok=bool(text),
                kind=kind,
                text=text,
                model=getattr(llm, "model_name", None),
                error=None if text else "empty completion",
                tool_steps=tool_steps,
                iterations=iteration + 1,
            )

        messages.append(
            ContextBuilder.format_assistant_tool_calls(
                response.tool_calls,
                content=response.content,
                reasoning_content=response.reasoning_content,
            )
        )

        for tc in response.tool_calls:
            args = dict(tc.arguments or {})
            try:
                raw = registry.execute(tc.name, args)
            except Exception as exc:  # noqa: BLE001
                raw = json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
            tool_steps.append(
                CopilotToolStep(
                    name=tc.name,
                    ok=_tool_ok(raw),
                    summary=_tool_summary(raw),
                )
            )
            messages.append(
                ContextBuilder.format_tool_result(
                    tc.id,
                    tc.name,
                    truncate_tool_result(raw),
                )
            )

    return CopilotResult(
        ok=bool(last_text),
        kind=kind,
        text=last_text or "已达最大推理轮次，请重试或换一个问题。",
        model=getattr(llm, "model_name", None),
        error=None if last_text else "max_iterations",
        tool_steps=tool_steps,
        iterations=max_iterations,
    )

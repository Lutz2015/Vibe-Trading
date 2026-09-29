"""Tests for Module Copilot mini ReAct runner."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from src.agent.tools import BaseTool, ToolRegistry
from src.insight.module_copilot import (
    MODULE_TOOLSETS,
    _tool_ok,
    _tool_summary,
    run_module_copilot,
)
from src.providers.chat import LLMResponse, ToolCallRequest


class _EchoTool(BaseTool):
    name = "echo_tool"
    description = "Echo test tool"
    parameters = {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}

    def execute(self, **kwargs: object) -> str:
        return json.dumps({"status": "ok", "msg": kwargs.get("msg")})


def test_module_toolsets_cover_insight_kinds() -> None:
    expected = {
        "market",
        "news",
        "sentiment",
        "logic_chain",
        "portfolio",
        "quant",
        "strategy_gen",
        "backtest",
        "intelligence",
    }
    assert expected == set(MODULE_TOOLSETS.keys())


def test_tool_summary_and_ok_helpers() -> None:
    assert _tool_ok(json.dumps({"status": "ok"}))
    assert not _tool_ok(json.dumps({"status": "error", "error": "boom"}))
    assert _tool_summary(json.dumps({"status": "ok"})) == "ok"


def test_run_module_copilot_executes_tools_then_returns_text(monkeypatch) -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    calls: list[str] = []

    def fake_stream_chat(messages, tools=None, timeout=90, **kwargs):
        calls.append("stream")
        if tools:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo_tool", arguments={"msg": "hi"}),
                ],
            )
        return LLMResponse(content="Final market view.", tool_calls=[])

    fake_llm = SimpleNamespace(
        stream_chat=fake_stream_chat,
        model_name="test-model",
        close=lambda: None,
    )

    with patch("src.insight.module_copilot.build_filtered_registry", return_value=registry):
        with patch("src.insight.module_copilot._try_chat_llm", return_value=(fake_llm, None)):
            result = run_module_copilot("market", "Analyze indices", locale="en", max_iterations=2)

    assert result.ok
    assert result.text == "Final market view."
    assert len(result.tool_steps) == 1
    assert result.tool_steps[0].name == "echo_tool"
    assert result.tool_steps[0].ok
    assert len(calls) == 2


def test_run_module_copilot_rejects_unknown_kind() -> None:
    result = run_module_copilot("unknown_kind", "hello")
    assert not result.ok
    assert "unsupported" in (result.error or "")

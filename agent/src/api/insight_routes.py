"""One-shot Agent insights for market / news / sentiment / logic-chain pages.

Each endpoint packages live page context into a short analyst prompt and runs
a single ``ChatLLM.chat`` completion (no tools, no session). Fail-closed: any
LLM or config error returns a structured error the UI can show; the page
itself keeps working with raw data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InsightRequest(BaseModel):
    kind: str = Field(
        ...,
        description="market | news | sentiment | logic_chain | portfolio | quant | strategy_gen | backtest | intelligence",
    )
    payload: Dict[str, Any] = Field(default_factory=dict)
    locale: str = "zh-CN"
    max_chars: int = Field(default=1200, ge=200, le=4000)


class InsightResponse(BaseModel):
    ok: bool
    kind: str
    text: str = ""
    model: Optional[str] = None
    error: Optional[str] = None


class CopilotToolStep(BaseModel):
    name: str
    ok: bool = True
    summary: str = ""


class CopilotRequest(BaseModel):
    kind: str = Field(
        ...,
        description="market | news | sentiment | logic_chain | portfolio | quant | strategy_gen | backtest | intelligence",
    )
    payload: Dict[str, Any] = Field(default_factory=dict)
    locale: str = "zh-CN"
    max_iterations: int = Field(default=6, ge=1, le=8)


class CopilotResponse(BaseModel):
    ok: bool
    kind: str
    text: str = ""
    model: Optional[str] = None
    error: Optional[str] = None
    tool_steps: List[CopilotToolStep] = Field(default_factory=list)
    iterations: int = 0


def _clip(value: Any, limit: int = 180) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _market_brief(payload: Dict[str, Any]) -> str:
    indices = payload.get("indices") or []
    boards = payload.get("hot_boards") or []
    stocks = payload.get("hot_stocks") or []
    lines: List[str] = ["【大盘指数】"]
    for row in indices[:8]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('name')}: {row.get('price')} ({row.get('change_pct')}%)"
        )
    northbound = payload.get("northbound") or {}
    if northbound:
        lines.append("【北向资金】")
        lines.append(
            f"- 净流入合计 {northbound.get('total_net')} / 沪 {northbound.get('sh_net')} / 深 {northbound.get('sz_net')}"
        )
    limit_stats = payload.get("limit_stats") or {}
    if limit_stats:
        lines.append("【涨跌停概况】")
        lines.append(
            f"- 涨停(估) {limit_stats.get('limit_up_approx')} / 跌停(估) {limit_stats.get('limit_down_approx')}"
        )
    lines.append("【热点板块涨幅榜】")
    for board in boards[:8]:
        if not isinstance(board, dict):
            continue
        leaders = board.get("leaders") or []
        leader_txt = "、".join(
            f"{x.get('name')} {x.get('change_pct')}%"
            for x in leaders[:4]
            if isinstance(x, dict)
        )
        lines.append(
            f"- {board.get('board_name')} {board.get('change_pct')}%"
            + (f" | 领涨: {leader_txt}" if leader_txt else "")
        )
    lines.append("【涨幅居前个股】")
    for row in stocks[:12]:
        if not isinstance(row, dict):
            continue
        lines.append(f"- {row.get('name')} {row.get('price')} ({row.get('change_pct')}%)")
    return "\n".join(lines)


def _strategy_gen_brief(payload: Dict[str, Any]) -> str:
    return str(payload.get("intent") or payload.get("request") or payload.get("prompt") or payload)


def _intelligence_brief(payload: Dict[str, Any]) -> str:
    lines: List[str] = ["【情报快照】"]
    market = payload.get("market") or {}
    if market:
        lines.append(f"- 大盘 as_of={market.get('as_of')} provider={market.get('primary_provider')}")
        for row in (market.get("indices") or [])[:6]:
            if isinstance(row, dict):
                lines.append(f"  · {row.get('name')} {row.get('change_pct')}%")
    news = payload.get("news") or {}
    articles = news.get("articles") or payload.get("articles") or []
    lines.append(f"- 新闻条数 {len(articles)} 偏多={news.get('positive_count')} 偏空={news.get('negative_count')}")
    for row in articles[:12]:
        if isinstance(row, dict):
            lines.append(f"  · [{row.get('source')}] {_clip(row.get('title'), 100)}")
    sentiment = payload.get("sentiment") or {}
    if sentiment:
        lines.append(f"- 情绪温度 {sentiment.get('composite')} mode={sentiment.get('mode')}")
    return "\n".join(lines)


def _backtest_brief(payload: Dict[str, Any]) -> str:
    metrics = payload.get("metrics") or payload
    keys = (
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_like",
        "win_rate_pct",
        "trade_count",
        "annualized_volatility_pct",
        "symbol",
        "strategy_id",
        "start",
        "end",
    )
    lines: List[str] = ["【回测指标】"]
    if isinstance(metrics, dict):
        for key in keys:
            if key in metrics:
                lines.append(f"- {key}: {metrics.get(key)}")
        extra = payload.get("note") or payload.get("params")
        if extra:
            lines.append(f"- 其它: {extra}")
    else:
        lines.append(str(metrics))
    return "\n".join(lines)


def _portfolio_brief(payload: Dict[str, Any]) -> str:
    holdings = payload.get("holdings") or payload.get("positions") or []
    cash = payload.get("cash")
    total = payload.get("portfolio_value") or payload.get("total_value")
    lines: List[str] = ["【组合概况】"]
    lines.append(f"- 净值/市值: {total} 现金: {cash}")
    lines.append("【持仓明细】")
    for row in holdings[:20]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('name') or row.get('symbol')} ({row.get('symbol')})"
            f" 数量={row.get('quantity')} 成本={row.get('avg_cost') or row.get('cost_price')}"
            f" 现价={row.get('last_price') or row.get('market_price')}"
            f" 盈亏={row.get('unrealized_pnl_usd') or row.get('pos_pnl')}"
        )
    return "\n".join(lines)


def _quant_brief(payload: Dict[str, Any]) -> str:
    strategies = payload.get("strategies") or []
    positions = payload.get("positions") or []
    lines: List[str] = ["【自动交易策略】"]
    for row in strategies[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('strategy_id')}: enabled={row.get('enabled')} rule={row.get('rule')}"
        )
    lines.append("【当前持仓】")
    for row in positions[:12]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('name') or row.get('symbol')} x{row.get('quantity')}"
            f" 成本={row.get('avg_cost')} 现价={row.get('last_price')}"
        )
    orders = payload.get("orders") or []
    if orders:
        lines.append("【最近指令】")
        for row in orders[:10]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('name') or row.get('symbol')} {row.get('side')}"
                f" {row.get('quantity')}@{row.get('price')} status={row.get('status')}"
            )
    return "\n".join(lines)


def _news_brief(payload: Dict[str, Any]) -> str:
    articles = payload.get("articles") or []
    lines: List[str] = ["【最新财经资讯标题】"]
    for row in articles[:20]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- [{row.get('source') or '?'}|{row.get('published') or ''}] "
            f"{_clip(row.get('title'), 120)}"
        )
    return "\n".join(lines)


def _sentiment_brief(payload: Dict[str, Any]) -> str:
    items = payload.get("items") or []
    composite = payload.get("composite")
    lines: List[str] = [f"综合情绪温度: {composite}"]
    for row in items:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('title')}: {row.get('probability')}% "
            f"(24h {row.get('delta24h')}pp, {row.get('source')})"
        )
    articles = payload.get("articles") or []
    if articles:
        lines.append("【相关新闻】")
        for row in articles[:8]:
            if isinstance(row, dict):
                lines.append(f"- {_clip(row.get('title'), 100)}")
    return "\n".join(lines)


def _logic_chain_brief(payload: Dict[str, Any]) -> str:
    mode = payload.get("mode") or "critique"
    topic = payload.get("topic") or ""
    lines: List[str] = [f"模式: {mode}", f"主题: {topic}"]
    nodes = payload.get("nodes") or []
    if nodes:
        lines.append("【现有节点】")
        for row in nodes:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- ({row.get('kind')}) {row.get('title')}: {_clip(row.get('body'), 80)}"
            )
    context = payload.get("context")
    if context:
        lines.append("【市场/新闻上下文】")
        lines.append(_clip(context, 1200))
    return "\n".join(lines)


def _live_market_news_context() -> str:
    """Pull a short snapshot from in-process caches (no slow EM news rebuild)."""
    try:
        from src.api.market_routes import _fetch_sina_roll, get_overview_snapshot

        overview = get_overview_snapshot()
        lines: List[str] = []
        for row in (overview.get("hot_boards") or [])[:6]:
            leaders = row.get("leaders") or []
            leader_txt = "、".join(
                f"{x.get('name')} {x.get('change_pct')}%"
                for x in leaders[:3]
                if isinstance(x, dict)
            )
            lines.append(
                f"板块 {row.get('board_name')} {row.get('change_pct')}%"
                + (f" 领涨:{leader_txt}" if leader_txt else "")
            )
        for row in (overview.get("hot_stocks") or [])[:8]:
            lines.append(f"个股 {row.get('name')} {row.get('change_pct')}%")
        try:
            for row in (_fetch_sina_roll(8) or [])[:8]:
                title = row.get("title") if isinstance(row, dict) else None
                if title:
                    lines.append(f"新闻 {title}")
        except Exception:  # noqa: BLE001 — headlines are optional context
            pass
        return "\n".join(lines)[:1500]
    except Exception as exc:  # noqa: BLE001
        logger.warning("live context failed: %s", exc)
        return ""


_SYSTEM_ZH = (
    "你是资深买方投研助理。只基于用户提供的实时结构化数据作答，不要编造未给出的数字。"
    "输出简体中文，条理清晰，控制在 200–350 字。"
    "格式：先 3 条要点，再 1 句风险提示。语气专业、克制，避免喊单。"
)

_SYSTEM_EN = (
    "You are a senior buy-side research assistant. Use ONLY the structured "
    "context provided. Do not invent numbers. Reply in English, 3 bullets + "
    "one risk note, ~150-220 words. Professional and cautious."
)

_USER_PROMPTS = {
    "market": {
        "zh-CN": (
            "请解读当前市场总览：指数强弱、热点板块是否具备持续性、领涨股扩散路径，"
            "以及对短线/波段操作的观察要点。\n\n{brief}"
        ),
        "en": (
            "Interpret this market overview: index strength, whether hot boards "
            "look sustainable, leader diffusion, and watchpoints for swing trading.\n\n{brief}"
        ),
    },
    "news": {
        "zh-CN": (
            "请扫描以下最新财经标题：1) 提炼 3 个最重要催化；2) 指出可能受益/受损板块；"
            "3) 标出需要进一步核实的信息。不要复述全部标题。\n\n{brief}"
        ),
        "en": (
            "Scan these headlines: 1) top 3 catalysts; 2) sectors likely helped/hurt; "
            "3) items needing verification. Do not recite all titles.\n\n{brief}"
        ),
    },
    "sentiment": {
        "zh-CN": (
            "请结合情绪温度与相关新闻，判断当前市场风险偏好处于什么阶段，"
            "给出仓位/节奏上的谨慎建议（不构成投资建议）。\n\n{brief}"
        ),
        "en": (
            "Combine the sentiment gauge with related news: judge risk appetite "
            "regime and give cautious position/tempo guidance.\n\n{brief}"
        ),
    },
    "logic_chain": {
        "zh-CN": (
            "请基于主题与上下文，输出一条可落地的投资逻辑链，严格用以下行格式（每行一个节点）：\n"
            "trigger|标题|一句说明\n"
            "logic|标题|一句说明\n"
            "sector|标题|一句说明\n"
            "ticker|标的名(代码)|一句说明\n"
            "只输出节点行，不要其它解释。主题与上下文如下：\n{brief}"
        ),
        "en": (
            "Produce one investable logic chain. Output ONLY lines:\n"
            "trigger|title|note\n"
            "logic|title|note\n"
            "sector|title|note\n"
            "ticker|name(code)|note\n"
            "Context:\n{brief}"
        ),
    },
    "portfolio": {
        "zh-CN": (
            "请诊断我的持仓组合（A股/港股为主）：\n"
            "1) 集中度与行业暴露风险；2) 浮亏/浮盈标的的可能原因与应对；"
            "3) 2-3 条可执行的调仓或观察建议（不构成投资建议）。\n\n{brief}"
        ),
        "en": (
            "Diagnose this portfolio (A-share/HK focused):\n"
            "1) concentration and sector exposure; 2) drivers for winners/losers and actions; "
            "3) 2-3 actionable rebalance or watch items (not investment advice).\n\n{brief}"
        ),
    },
    "quant": {
        "zh-CN": (
            "请从自动交易运营角度分析：\n"
            "1) 当前策略是否清晰、可解释；2) 持仓与策略是否匹配；"
            "3) 风险与下一步操作（暂停/切换策略/手动干预）建议。\n\n{brief}"
        ),
        "en": (
            "Analyze from an auto-trading ops perspective:\n"
            "1) is the strategy clear and explainable; 2) do holdings match the strategy; "
            "3) risks and next ops (pause / switch / manual override).\n\n{brief}"
        ),
    },
    "strategy_gen": {
        "zh-CN": (
            "根据用户的策略意图，输出一个可直接导入的 JSON 策略配置（只输出 JSON，不要其它文字）。\n"
            "字段：\n"
            "```json\n"
            "{{\n"
            '  "strategy_id": "snake_case_id",\n'
            '  "label": "中文策略名",\n'
            '  "enabled": false,\n'
            '  "expected_return_pct": 10.0,\n'
            '  "risk_volatility_pct": 10.0,\n'
            '  "max_weight": 0.4,\n'
            '  "params": {{\n'
            '    "mode": "momentum|mean_reversion|equal_weight|low_volatility|strong_hand|dip_buy",\n'
            '    "top_n": 3,\n'
            '    "universe_symbols": ["000001.SZ","600519.SH"]\n'
            "  }}\n"
            "}}\n"
            "```\n"
            "mode 必须是枚举之一。universe_symbols 用 A 股/港股代码。用户意图：\n{brief}"
        ),
        "en": (
            "Output ONLY a JSON strategy config matching this schema:\n"
            'strategy_id, label, enabled, expected_return_pct, risk_volatility_pct, max_weight, '
            'params.mode in {momentum,mean_reversion,equal_weight,low_volatility,strong_hand,dip_buy}, '
            "params.top_n, params.universe_symbols.\n"
            "Intent:\n{brief}"
        ),
    },
    "backtest": {
        "zh-CN": (
            "请解读回测结果（A股/港股语境）：\n"
            "1) 收益/回撤/夏普是否合理；2) 是否有过拟合或数据窥探嫌疑；"
            "3) 实盘前还需要验证什么；4) 是否建议上模拟盘/实盘。\n\n{brief}"
        ),
        "en": (
            "Interpret this backtest (A-share/HK context):\n"
            "1) are return/drawdown/Sharpe sensible; 2) overfitting or data-snooping risks; "
            "3) what to validate before live; 4) paper vs live recommendation.\n\n{brief}"
        ),
    },
    "intelligence": {
        "zh-CN": (
            "请作为情报分析师，综合市场、新闻与情绪快照：\n"
            "1) 提炼 3 个最重要催化；2) 受益/受损板块；3) 与 A 股/港股相关的观察点；"
            "4) 需进一步核实的信息。\n\n{brief}"
        ),
        "en": (
            "As an intelligence analyst, synthesize market/news/sentiment:\n"
            "1) top 3 catalysts; 2) sectors helped/hurt; 3) A-share/HK watchpoints; "
            "4) items to verify.\n\n{brief}"
        ),
    },
}


def _build_messages(kind: str, payload: Dict[str, Any], locale: str) -> List[Dict[str, str]]:
    builders = {
        "market": _market_brief,
        "news": _news_brief,
        "sentiment": _sentiment_brief,
        "logic_chain": _logic_chain_brief,
        "portfolio": _portfolio_brief,
        "quant": _quant_brief,
        "strategy_gen": _strategy_gen_brief,
        "backtest": _backtest_brief,
        "intelligence": _intelligence_brief,
    }
    builder = builders.get(kind)
    if builder is None:
        raise ValueError(f"unsupported insight kind: {kind}")
    # Logic-chain generation: inject live hot boards / stocks / headlines when
    # the client did not pass an explicit context string.
    if kind == "logic_chain" and not payload.get("context"):
        live = _live_market_news_context()
        if live:
            payload = {**payload, "context": live}
    brief = builder(payload)
    zh = locale.lower().startswith("zh") or locale.lower() in ("", "auto")
    system = _SYSTEM_ZH if zh else _SYSTEM_EN
    template = _USER_PROMPTS[kind]["zh-CN" if zh else "en"]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": template.format(brief=brief)},
    ]


def parse_logic_chain_lines(text: str) -> List[Dict[str, str]]:
    """Parse ``kind|title|body`` lines from an LLM logic-chain reply."""
    allowed = {"trigger", "logic", "sector", "ticker"}
    nodes: List[Dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-• ").strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        kind = parts[0].lower()
        if kind not in allowed:
            continue
        title = parts[1]
        body = parts[2] if len(parts) > 2 else ""
        if title:
            nodes.append({"kind": kind, "title": title, "body": body})
    return nodes


def register_insight_routes(
    app: FastAPI,
    require_local_or_auth=None,
) -> None:
    deps = [Depends(require_local_or_auth)] if require_local_or_auth else []

    @app.post("/insight/analyze", response_model=InsightResponse, dependencies=deps)
    async def analyze_insight(body: InsightRequest) -> InsightResponse:
        kind = (body.kind or "").strip()
        try:
            messages = _build_messages(kind, body.payload or {}, body.locale or "zh-CN")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        def _run() -> InsightResponse:
            # Align process env with the configured provider (Moonshot etc.) so
            # ChatOpenAI does not require a bare OPENAI_API_KEY when another
            # provider is selected. Desktop injects provider keys via env; this
            # also reloads ~/.person-trading/.env after a settings change.
            try:
                from src.providers.llm import _sync_provider_env
                from src.config.accessor import reset_env_config

                reset_env_config()
                _sync_provider_env()
            except Exception:  # noqa: BLE001 — still attempt ChatLLM
                logger.debug("provider env sync skipped", exc_info=True)

            from src.providers.chat import try_chat_llm

            llm, init_error = try_chat_llm()
            if init_error or llm is None:
                return InsightResponse(
                    ok=False,
                    kind=kind,
                    text="",
                    error=init_error or "LLM 未就绪",
                )
            try:
                response = llm.chat(messages, timeout=90)
                text = (getattr(response, "content", None) or str(response) or "").strip()
                return InsightResponse(
                    ok=bool(text),
                    kind=kind,
                    text=text,
                    model=getattr(llm, "model_name", None),
                    error=None if text else "empty completion",
                )
            except Exception as exc:  # noqa: BLE001 — surface to UI, keep page alive
                logger.warning("insight %s failed: %s", kind, exc)
                message = str(exc)[:300]
                if "api_key" in message.lower() or "OPENAI_API_KEY" in message:
                    message = (
                        "LLM 未就绪：请在「设置」配置提供商与 API 密钥后重试。"
                        f"（{message}）"
                    )
                return InsightResponse(
                    ok=False,
                    kind=kind,
                    text="",
                    model=getattr(llm, "model_name", None),
                    error=message,
                )
            finally:
                try:
                    llm.close()
                except Exception:  # noqa: BLE001
                    pass

        # ChatLLM is sync; run in a worker thread so the event loop stays free.
        import asyncio

        return await asyncio.to_thread(_run)

    @app.post("/insight/copilot", response_model=CopilotResponse, dependencies=deps)
    async def copilot_insight(body: CopilotRequest) -> CopilotResponse:
        """Module Copilot — mini ReAct with per-module tool whitelist."""
        kind = (body.kind or "").strip()
        try:
            messages = _build_messages(kind, body.payload or {}, body.locale or "zh-CN")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        user_message = messages[-1]["content"]

        def _run_copilot() -> CopilotResponse:
            try:
                from src.providers.llm import _sync_provider_env
                from src.config.accessor import reset_env_config

                reset_env_config()
                _sync_provider_env()
            except Exception:  # noqa: BLE001
                logger.debug("provider env sync skipped", exc_info=True)

            from src.insight.module_copilot import run_module_copilot

            result = run_module_copilot(
                kind,
                user_message,
                locale=body.locale or "zh-CN",
                max_iterations=body.max_iterations,
            )
            return CopilotResponse(
                ok=result.ok,
                kind=result.kind,
                text=result.text,
                model=result.model,
                error=result.error,
                tool_steps=[
                    CopilotToolStep(name=s.name, ok=s.ok, summary=s.summary)
                    for s in result.tool_steps
                ],
                iterations=result.iterations,
            )

        import asyncio

        return await asyncio.to_thread(_run_copilot)

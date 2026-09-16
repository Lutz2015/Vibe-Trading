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
    kind: str = Field(..., description="market | news | sentiment | logic_chain")
    payload: Dict[str, Any] = Field(default_factory=dict)
    locale: str = "zh-CN"
    max_chars: int = Field(default=1200, ge=200, le=4000)


class InsightResponse(BaseModel):
    ok: bool
    kind: str
    text: str = ""
    model: Optional[str] = None
    error: Optional[str] = None


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
}


def _build_messages(kind: str, payload: Dict[str, Any], locale: str) -> List[Dict[str, str]]:
    builders = {
        "market": _market_brief,
        "news": _news_brief,
        "sentiment": _sentiment_brief,
        "logic_chain": _logic_chain_brief,
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

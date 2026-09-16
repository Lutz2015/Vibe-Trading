"""User-facing strategy library (code strategies) for the desktop/web UI.

Stores one JSON file per strategy under ``~/.person-trading/strategy_library/``.
This is intentionally separate from the heavier ``src.strategy_store`` (which
tracks validated signal artifacts + governance). The library is for editable
code drafts the operator writes or asks the Agent to generate.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^\w一-鿿\- ]+", re.UNICODE)


def _library_dir() -> Path:
    root = Path.home() / ".person-trading" / "strategy_library"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_title(title: str) -> str:
    title = _SAFE_NAME.sub("_", (title or "").strip())[:80]
    return title or "untitled"


class StrategyItem(BaseModel):
    id: str
    name: str
    group: str = "默认"
    kind: str = "通用策略"  # 通用策略 | 选股 | 择时 | 对冲 …
    language: str = "python"
    code: str = ""
    notes: str = ""
    description: str = ""
    created_at: float
    updated_at: float


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    group: str = "默认"
    kind: str = "通用策略"
    language: str = "python"
    code: str = ""
    notes: str = ""
    description: str = ""


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    group: Optional[str] = None
    kind: Optional[str] = None
    language: Optional[str] = None
    code: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None


class StrategyListResponse(BaseModel):
    items: List[StrategyItem] = Field(default_factory=list)
    groups: List[str] = Field(default_factory=list)


class StrategyAiMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant)$")
    content: str = Field(..., min_length=1)


class StrategyAiRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    language: str = "python"
    current_code: str = ""
    mode: str = "generate"  # generate | improve | explain
    history: List[StrategyAiMessage] = Field(default_factory=list)


class StrategyAiResponse(BaseModel):
    ok: bool
    code: str = ""
    language: str = "python"
    model: Optional[str] = None
    error: Optional[str] = None


def _path_for(strategy_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", strategy_id or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid strategy id")
    return _library_dir() / f"{strategy_id}.json"


def _load(item_id: str) -> StrategyItem:
    path = _path_for(item_id)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StrategyItem(**data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"corrupt strategy: {exc}") from exc


def _save(item: StrategyItem) -> None:
    path = _path_for(item.id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(item.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _list_all() -> List[StrategyItem]:
    items: List[StrategyItem] = []
    for path in sorted(_library_dir().glob("*.json")):
        try:
            items.append(StrategyItem(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            logger.warning("skip unreadable strategy file %s", path)
    items.sort(key=lambda x: x.updated_at, reverse=True)
    return items


def _default_code(language: str) -> str:
    if language == "javascript":
        return (
            "function main() {\n"
            "  const account = exchange.GetAccount();\n"
            "  const ticker = exchange.GetTicker();\n"
            "  Log(account, ticker);\n"
            "}\n"
        )
    return (
        "\"\"\"双均线示例 — 在 Agent 中可继续改写。\"\"\"\n"
        "def on_bar(ctx):\n"
        "    close = ctx.close\n"
        "    if len(close) < 30:\n"
        "        return\n"
        "    fast = sum(close[-5:]) / 5\n"
        "    slow = sum(close[-20:]) / 20\n"
        "    if fast > slow and not ctx.position:\n"
        "        ctx.buy(1)\n"
        "    elif fast < slow and ctx.position:\n"
        "        ctx.sell(1)\n"
    )


_SYSTEM = (
    "你是量化策略工程师。根据用户描述生成可运行的策略代码。"
    "只输出代码本身，不要 markdown 围栏，不要多余解释。"
    "Python 使用简单的 on_bar(ctx) 风格；JavaScript 使用 FMZ 风格 function main()。"
    "加入必要的中文注释说明信号逻辑。"
)


def register_strategy_library_routes(
    app: FastAPI,
    require_local_or_auth=None,
) -> None:
    deps = [Depends(require_local_or_auth)] if require_local_or_auth else []

    @app.get("/strategies", response_model=StrategyListResponse, dependencies=deps)
    async def list_strategies() -> StrategyListResponse:
        items = _list_all()
        groups = sorted({s.group or "默认" for s in items} | {"默认"})
        return StrategyListResponse(items=items, groups=groups)

    @app.post("/strategies", response_model=StrategyItem, dependencies=deps)
    async def create_strategy(body: StrategyCreate) -> StrategyItem:
        now = time.time()
        item = StrategyItem(
            id=uuid.uuid4().hex[:16],
            name=_safe_title(body.name),
            group=(body.group or "默认").strip()[:40],
            kind=(body.kind or "通用策略").strip()[:40],
            language=(body.language or "python").strip().lower(),
            code=body.code or _default_code(body.language),
            notes=body.notes or "",
            description=body.description or "",
            created_at=now,
            updated_at=now,
        )
        _save(item)
        return item

    @app.get("/strategies/{strategy_id}", response_model=StrategyItem, dependencies=deps)
    async def get_strategy(strategy_id: str) -> StrategyItem:
        return _load(strategy_id)

    @app.put("/strategies/{strategy_id}", response_model=StrategyItem, dependencies=deps)
    async def update_strategy(strategy_id: str, body: StrategyUpdate) -> StrategyItem:
        item = _load(strategy_id)
        data = item.model_dump()
        for field in ("name", "group", "kind", "language", "code", "notes", "description"):
            value = getattr(body, field)
            if value is not None:
                data[field] = _safe_title(value) if field == "name" else value
        data["updated_at"] = time.time()
        updated = StrategyItem(**data)
        _save(updated)
        return updated

    @app.delete("/strategies/{strategy_id}", dependencies=deps)
    async def delete_strategy(strategy_id: str) -> Dict[str, bool]:
        path = _path_for(strategy_id)
        if not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy not found")
        path.unlink()
        return {"ok": True}

    @app.post("/strategies/{strategy_id}/duplicate", response_model=StrategyItem, dependencies=deps)
    async def duplicate_strategy(strategy_id: str) -> StrategyItem:
        src = _load(strategy_id)
        now = time.time()
        clone = src.model_copy(
            update={
                "id": uuid.uuid4().hex[:16],
                "name": _safe_title(f"{src.name}-副本")[:80],
                "created_at": now,
                "updated_at": now,
            }
        )
        _save(clone)
        return clone

    @app.post("/strategies/ai", response_model=StrategyAiResponse, dependencies=deps)
    async def strategy_ai(body: StrategyAiRequest) -> StrategyAiResponse:
        """One-shot LLM code generation / improve for the strategy editor."""
        lang = (body.language or "python").lower()
        lang_hint = (
            "语言：JavaScript（FMZ 风格 function main）"
            if lang.startswith("js") or lang == "javascript"
            else "语言：Python（on_bar(ctx) 风格）"
        )
        mode_hint = {
            "improve": "请优化/修复下面已有代码，保持结构，只输出完整代码：",
            "explain": "请用中文简要解释下面策略逻辑（200字内），不要输出代码：",
            "generate": "请根据需求生成完整策略代码：",
        }.get(body.mode, "请根据需求生成完整策略代码：")

        user_content = (
            f"{lang_hint}\n{mode_hint}\n"
            f"需求：{body.prompt.strip()}\n"
            + (f"现有代码：\n```\n{body.current_code[:4000]}\n```" if body.current_code else "")
        )
        messages: List[Dict[str, str]] = [{"role": "system", "content": _SYSTEM}]
        for entry in (body.history or [])[-10:]:
            messages.append({"role": entry.role, "content": entry.content[:2000]})
        messages.append({"role": "user", "content": user_content})

        def _run() -> StrategyAiResponse:
            try:
                from src.providers.llm import _sync_provider_env
                from src.config.accessor import reset_env_config

                reset_env_config()
                _sync_provider_env()
            except Exception:  # noqa: BLE001
                logger.debug("provider env sync skipped", exc_info=True)

            from src.providers.chat import try_chat_llm

            llm, init_error = try_chat_llm()
            if init_error or llm is None:
                return StrategyAiResponse(ok=False, error=init_error or "LLM 未就绪", language=lang)
            try:
                response = llm.chat(messages, timeout=90)
                text = (getattr(response, "content", None) or str(response) or "").strip()
                # Strip accidental markdown fences
                if text.startswith("```"):
                    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                    text = re.sub(r"\n?```\s*$", "", text).strip()
                if not text:
                    return StrategyAiResponse(ok=False, error="empty completion", language=lang)
                return StrategyAiResponse(
                    ok=True,
                    code=text,
                    language=lang,
                    model=getattr(llm, "model_name", None),
                )
            except Exception as exc:  # noqa: BLE001
                raw = str(exc)
                if "Missing credentials" in raw or "OPENAI_API_KEY" in raw:
                    error = "未配置 LLM 密钥，请先在设置中保存 API 密钥。"
                else:
                    error = raw[:300]
                logger.warning("strategy ai failed: %s", exc)
                return StrategyAiResponse(ok=False, error=error, language=lang)
            finally:
                try:
                    llm.close()
                except Exception:  # noqa: BLE001
                    pass

        import asyncio

        return await asyncio.to_thread(_run)

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="strategy-registry-engine", version="0.1.0")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyVersionCreate(BaseModel):
    strategy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    rebalance_rule: str = ""
    factors: list[str] = Field(default_factory=list)
    params_schema: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    status: str = "draft"  # draft | active | deprecated


class StrategyVersionRecord(StrategyVersionCreate):
    created_at: str


class ExperimentCreate(BaseModel):
    strategy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


class ExperimentRecord(ExperimentCreate):
    experiment_id: str
    created_at: str


class StrategyStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1)  # draft | deprecated


class ActiveVersionRecord(BaseModel):
    strategy_id: str
    active_version: str
    activated_at: str


class RolloutConfigUpdateRequest(BaseModel):
    mode: str = "full"  # shadow | canary | full | paused
    canary_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    auto_rollback_on_alert: bool = True
    notes: str = ""


class RolloutConfigRecord(RolloutConfigUpdateRequest):
    strategy_id: str
    version: str
    updated_at: str


class RollbackRequest(BaseModel):
    target_version: str | None = None
    reason: str = "manual_rollback"


class RollbackRecord(BaseModel):
    strategy_id: str
    from_version: str
    to_version: str
    rolled_back_at: str
    reason: str


class StrategyRepositoryItem(BaseModel):
    strategy_id: str
    version: str
    template_id: str
    asset_class: str
    params_schema: dict[str, str] = Field(default_factory=dict)
    status: str
    description: str = ""
    metadata_path: str
    strategy_path: str


_strategy_versions: dict[tuple[str, str], StrategyVersionRecord] = {}
_experiments: list[ExperimentRecord] = []
_active_versions: dict[str, ActiveVersionRecord] = {}
_rollout_configs: dict[str, RolloutConfigRecord] = {}
_activation_history: dict[str, list[str]] = {}
_allowed_statuses = {"draft", "active", "deprecated"}
_allowed_rollout_modes = {"shadow", "canary", "full", "paused"}
_qbit_root = Path(__file__).resolve().parents[2]
_strategies_root = Path(
    os.getenv("VIBE_TRADING_QBIT_STRATEGIES_ROOT", str(_qbit_root / "strategies"))
)


def _read_repository_items() -> list[StrategyRepositoryItem]:
    if not _strategies_root.exists():
        return []
    items: list[StrategyRepositoryItem] = []
    for metadata_file in _strategies_root.rglob("metadata.json"):
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        strategy_file = metadata_file.with_name("strategy.py")
        if not strategy_file.exists():
            continue
        try:
            item = StrategyRepositoryItem(
                strategy_id=str(payload.get("strategy_id", "")).strip(),
                version=str(payload.get("version", "")).strip(),
                template_id=str(payload.get("template_id", "")).strip(),
                asset_class=str(payload.get("asset_class", "")).strip(),
                params_schema=dict(payload.get("params_schema", {}) or {}),
                status=str(payload.get("status", "draft")).strip() or "draft",
                description=str(payload.get("description", "")).strip(),
                metadata_path=str(metadata_file),
                strategy_path=str(strategy_file),
            )
        except Exception:
            continue
        if not item.strategy_id or not item.version:
            continue
        items.append(item)
    return sorted(items, key=lambda x: (x.strategy_id, x.version))


def _normalize_rollout_ratio(mode: str, canary_ratio: float) -> float:
    if mode == "canary":
        if not (0.0 < canary_ratio < 1.0):
            raise HTTPException(status_code=400, detail="canary_ratio_must_be_between_0_and_1")
        return canary_ratio
    if mode == "full":
        return 1.0
    if mode in {"paused", "shadow"}:
        return 0.0
    raise HTTPException(status_code=400, detail=f"invalid_rollout_mode: {mode}")


def _set_active_version(strategy_id: str, version: str) -> ActiveVersionRecord:
    key = (strategy_id, version)
    record = _strategy_versions.get(key)
    if record is None:
        raise HTTPException(status_code=404, detail="strategy_version_not_found")
    if record.status == "deprecated":
        raise HTTPException(status_code=409, detail="cannot_activate_deprecated_version")

    for (sid, ver), item in _strategy_versions.items():
        if sid == strategy_id and ver != version and item.status == "active":
            item.status = "draft"

    record.status = "active"
    active_record = ActiveVersionRecord(
        strategy_id=strategy_id,
        active_version=version,
        activated_at=now_iso(),
    )
    _active_versions[strategy_id] = active_record

    history = _activation_history.setdefault(strategy_id, [])
    if not history or history[-1] != version:
        history.append(version)

    existing_rollout = _rollout_configs.get(strategy_id)
    if existing_rollout is None or existing_rollout.version != version:
        _rollout_configs[strategy_id] = RolloutConfigRecord(
            strategy_id=strategy_id,
            version=version,
            mode="full",
            canary_ratio=1.0,
            auto_rollback_on_alert=True,
            notes="",
            updated_at=now_iso(),
        )

    return active_record


def _resolve_rollback_target(strategy_id: str, current_version: str) -> str:
    history = _activation_history.get(strategy_id, [])
    for version in reversed(history):
        if version == current_version:
            continue
        record = _strategy_versions.get((strategy_id, version))
        if record is None or record.status == "deprecated":
            continue
        return version
    raise HTTPException(status_code=409, detail="no_rollback_target_available")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "strategy-registry-engine"}


@app.get("/strategy/repository", response_model=list[StrategyRepositoryItem])
def list_strategy_repository() -> list[StrategyRepositoryItem]:
    return _read_repository_items()


@app.get("/strategy/repository/{strategy_id}", response_model=list[StrategyRepositoryItem])
def list_strategy_repository_versions(strategy_id: str) -> list[StrategyRepositoryItem]:
    items = [item for item in _read_repository_items() if item.strategy_id == strategy_id]
    if not items:
        raise HTTPException(status_code=404, detail="strategy_repository_not_found")
    return items


@app.get(
    "/strategy/repository/{strategy_id}/{version}",
    response_model=StrategyRepositoryItem,
)
def get_strategy_repository_item(strategy_id: str, version: str) -> StrategyRepositoryItem:
    for item in _read_repository_items():
        if item.strategy_id == strategy_id and item.version == version:
            return item
    raise HTTPException(status_code=404, detail="strategy_repository_item_not_found")


@app.post(
    "/strategy/repository/{strategy_id}/{version}/register",
    response_model=StrategyVersionRecord,
)
def register_strategy_from_repository(strategy_id: str, version: str) -> StrategyVersionRecord:
    item = get_strategy_repository_item(strategy_id, version)
    if item.status not in _allowed_statuses:
        raise HTTPException(status_code=400, detail=f"invalid_status: {item.status}")
    key = (item.strategy_id, item.version)
    if key in _strategy_versions:
        return _strategy_versions[key]
    record = StrategyVersionRecord(
        strategy_id=item.strategy_id,
        version=item.version,
        data_version="repository_local",
        rebalance_rule="",
        factors=[],
        params_schema=item.params_schema,
        description=item.description or f"registered from repository template={item.template_id}",
        status=item.status,
        created_at=now_iso(),
    )
    _strategy_versions[key] = record
    if item.status == "active":
        _set_active_version(item.strategy_id, item.version)
    return record


@app.post("/strategy/versions", response_model=StrategyVersionRecord)
def register_strategy_version(payload: StrategyVersionCreate) -> StrategyVersionRecord:
    if payload.status not in _allowed_statuses:
        raise HTTPException(status_code=400, detail=f"invalid_status: {payload.status}")
    key = (payload.strategy_id, payload.version)
    if key in _strategy_versions:
        raise HTTPException(
            status_code=409,
            detail=f"strategy_version_exists: {payload.strategy_id}@{payload.version}",
        )
    record = StrategyVersionRecord(**payload.model_dump(), created_at=now_iso())
    _strategy_versions[key] = record
    return record


@app.get("/strategy/versions", response_model=list[StrategyVersionRecord])
def list_strategy_versions(strategy_id: str | None = Query(default=None)) -> list[StrategyVersionRecord]:
    values = list(_strategy_versions.values())
    if strategy_id is None:
        return values
    return [item for item in values if item.strategy_id == strategy_id]


@app.get("/strategy/versions/{strategy_id}/{version}", response_model=StrategyVersionRecord)
def get_strategy_version(strategy_id: str, version: str) -> StrategyVersionRecord:
    key = (strategy_id, version)
    record = _strategy_versions.get(key)
    if record is None:
        raise HTTPException(status_code=404, detail="strategy_version_not_found")
    return record


@app.post("/strategy/versions/{strategy_id}/{version}/status", response_model=StrategyVersionRecord)
def update_strategy_version_status(
    strategy_id: str,
    version: str,
    payload: StrategyStatusUpdateRequest,
) -> StrategyVersionRecord:
    if payload.status not in {"draft", "deprecated"}:
        raise HTTPException(status_code=400, detail="status_update_only_supports: draft|deprecated")
    key = (strategy_id, version)
    record = _strategy_versions.get(key)
    if record is None:
        raise HTTPException(status_code=404, detail="strategy_version_not_found")
    if record.status == "active" and payload.status == "deprecated":
        active = _active_versions.get(strategy_id)
        if active is not None and active.active_version == version:
            raise HTTPException(
                status_code=409,
                detail="cannot_deprecate_active_version_without_switch",
            )
    record.status = payload.status
    return record


@app.post(
    "/strategy/versions/{strategy_id}/{version}/activate",
    response_model=ActiveVersionRecord,
)
def activate_strategy_version(strategy_id: str, version: str) -> ActiveVersionRecord:
    return _set_active_version(strategy_id, version)


@app.post(
    "/strategy/versions/{strategy_id}/rollback",
    response_model=RollbackRecord,
)
def rollback_strategy_version(strategy_id: str, payload: RollbackRequest) -> RollbackRecord:
    active = _active_versions.get(strategy_id)
    if active is None:
        raise HTTPException(status_code=404, detail="active_version_not_found")

    from_version = active.active_version
    if payload.target_version is not None and payload.target_version.strip() != "":
        target_version = payload.target_version.strip()
        target_record = _strategy_versions.get((strategy_id, target_version))
        if target_record is None:
            raise HTTPException(status_code=404, detail="rollback_target_not_found")
        if target_record.status == "deprecated":
            raise HTTPException(status_code=409, detail="cannot_rollback_to_deprecated_version")
    else:
        target_version = _resolve_rollback_target(strategy_id, from_version)

    _set_active_version(strategy_id, target_version)
    return RollbackRecord(
        strategy_id=strategy_id,
        from_version=from_version,
        to_version=target_version,
        rolled_back_at=now_iso(),
        reason=payload.reason,
    )


@app.get("/strategy/active-versions", response_model=list[ActiveVersionRecord])
def list_active_versions() -> list[ActiveVersionRecord]:
    return list(_active_versions.values())


@app.get("/strategy/active-versions/{strategy_id}", response_model=ActiveVersionRecord)
def get_active_version(strategy_id: str) -> ActiveVersionRecord:
    active = _active_versions.get(strategy_id)
    if active is None:
        raise HTTPException(status_code=404, detail="active_version_not_found")
    return active


@app.post("/strategy/rollout/{strategy_id}", response_model=RolloutConfigRecord)
def upsert_rollout_config(strategy_id: str, payload: RolloutConfigUpdateRequest) -> RolloutConfigRecord:
    if payload.mode not in _allowed_rollout_modes:
        raise HTTPException(status_code=400, detail=f"invalid_rollout_mode: {payload.mode}")
    active = _active_versions.get(strategy_id)
    if active is None:
        raise HTTPException(status_code=404, detail="active_version_not_found")

    ratio = _normalize_rollout_ratio(payload.mode, payload.canary_ratio)
    config = RolloutConfigRecord(
        strategy_id=strategy_id,
        version=active.active_version,
        mode=payload.mode,
        canary_ratio=ratio,
        auto_rollback_on_alert=payload.auto_rollback_on_alert,
        notes=payload.notes,
        updated_at=now_iso(),
    )
    _rollout_configs[strategy_id] = config
    return config


@app.get("/strategy/rollout/{strategy_id}", response_model=RolloutConfigRecord)
def get_rollout_config(strategy_id: str) -> RolloutConfigRecord:
    config = _rollout_configs.get(strategy_id)
    if config is None:
        raise HTTPException(status_code=404, detail="rollout_config_not_found")
    return config


@app.post("/strategy/experiments", response_model=ExperimentRecord)
def record_experiment(payload: ExperimentCreate) -> ExperimentRecord:
    key = (payload.strategy_id, payload.version)
    if key not in _strategy_versions:
        raise HTTPException(
            status_code=400,
            detail=f"strategy_version_not_registered: {payload.strategy_id}@{payload.version}",
        )
    record = ExperimentRecord(
        **payload.model_dump(),
        experiment_id=f"exp_{uuid4().hex[:12]}",
        created_at=now_iso(),
    )
    _experiments.append(record)
    return record


@app.get("/strategy/experiments", response_model=list[ExperimentRecord])
def list_experiments(
    strategy_id: str | None = Query(default=None),
    version: str | None = Query(default=None),
) -> list[ExperimentRecord]:
    items = _experiments
    if strategy_id is not None:
        items = [item for item in items if item.strategy_id == strategy_id]
    if version is not None:
        items = [item for item in items if item.version == version]
    return items


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8008, reload=False)

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

from fastapi import HTTPException


def _load_module(module_name: str, relative_path: str) -> Any:
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _reset_state(registry: Any) -> None:
    registry._strategy_versions.clear()
    registry._experiments.clear()
    registry._active_versions.clear()
    registry._rollout_configs.clear()
    registry._activation_history.clear()


def test_register_and_get_strategy_version() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_1",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    created = registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
            rebalance_rule="15d",
            factors=["market_cap", "roe"],
            params_schema={"top_n": "int", "rebalance_days": "int"},
            description="baseline",
        )
    )
    fetched = registry.get_strategy_version("multifactor", "v1")

    assert created.strategy_id == "multifactor"
    assert fetched.version == "v1"
    assert fetched.data_version == "jq_2026q1"


def test_duplicate_strategy_version_rejected() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_2",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    payload = registry.StrategyVersionCreate(
        strategy_id="multifactor",
        version="v1",
        data_version="jq_2026q1",
    )
    registry.register_strategy_version(payload)
    try:
        registry.register_strategy_version(payload)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Expected duplicate strategy version to fail.")


def test_record_experiment_requires_registered_version() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_3",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    try:
        registry.record_experiment(
            registry.ExperimentCreate(
                strategy_id="multifactor",
                version="v1",
                data_version="jq_2026q1",
                params={"top_n": 20},
                metrics={"total_return_pct": 12.5},
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected unregistered strategy version to fail.")


def test_record_and_filter_experiments() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_4",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
        )
    )

    recorded = registry.record_experiment(
        registry.ExperimentCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
            params={"top_n": 20},
            metrics={"total_return_pct": 12.5, "max_drawdown_pct": 5.1},
        )
    )

    filtered = registry.list_experiments(strategy_id="multifactor", version="v1")
    assert len(filtered) == 1
    assert filtered[0].experiment_id == recorded.experiment_id


def test_activate_switches_single_active_version() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_5",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
        )
    )
    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v2",
            data_version="jq_2026q2",
        )
    )

    registry.activate_strategy_version("multifactor", "v1")
    first = registry.get_strategy_version("multifactor", "v1")
    assert first.status == "active"

    active = registry.activate_strategy_version("multifactor", "v2")
    assert active.active_version == "v2"
    v1 = registry.get_strategy_version("multifactor", "v1")
    v2 = registry.get_strategy_version("multifactor", "v2")
    assert v1.status == "draft"
    assert v2.status == "active"


def test_cannot_deprecate_active_version_without_switch() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_6",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
        )
    )
    registry.activate_strategy_version("multifactor", "v1")

    try:
        registry.update_strategy_version_status(
            "multifactor",
            "v1",
            registry.StrategyStatusUpdateRequest(status="deprecated"),
        )
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Expected deprecating active version to fail.")


def test_rollout_config_and_rollback_flow() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_7",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
        )
    )
    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v2",
            data_version="jq_2026q2",
        )
    )

    registry.activate_strategy_version("multifactor", "v1")
    registry.activate_strategy_version("multifactor", "v2")

    rollout = registry.upsert_rollout_config(
        "multifactor",
        registry.RolloutConfigUpdateRequest(
            mode="canary",
            canary_ratio=0.2,
            auto_rollback_on_alert=True,
            notes="first canary",
        ),
    )
    assert rollout.version == "v2"
    assert rollout.mode == "canary"
    assert rollout.canary_ratio == 0.2

    rollback = registry.rollback_strategy_version(
        "multifactor",
        registry.RollbackRequest(reason="latency_spike"),
    )
    assert rollback.from_version == "v2"
    assert rollback.to_version == "v1"

    active = registry.get_active_version("multifactor")
    assert active.active_version == "v1"


def test_invalid_canary_ratio_rejected() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_8",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    registry.register_strategy_version(
        registry.StrategyVersionCreate(
            strategy_id="multifactor",
            version="v1",
            data_version="jq_2026q1",
        )
    )
    registry.activate_strategy_version("multifactor", "v1")

    try:
        registry.upsert_rollout_config(
            "multifactor",
            registry.RolloutConfigUpdateRequest(mode="canary", canary_ratio=1.0),
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected invalid canary ratio to fail.")


def test_strategy_repository_discovery_and_register() -> None:
    registry = _load_module(
        "strategy_registry_engine_test_9",
        "qbit/services/strategy-registry-engine/main.py",
    )
    _reset_state(registry)

    items = registry.list_strategy_repository()
    strategy_ids = {item.strategy_id for item in items}
    assert "test1_multifactor" in strategy_ids
    assert "test2_futures_spread" in strategy_ids

    item = registry.get_strategy_repository_item("test1_multifactor", "v1")
    assert item.template_id == "test1_multifactor"

    record = registry.register_strategy_from_repository("test1_multifactor", "v1")
    assert record.strategy_id == "test1_multifactor"
    assert record.version == "v1"

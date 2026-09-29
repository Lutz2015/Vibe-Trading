"""Tests for person-trading-strategy.yaml import."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.strategy_yaml.importer import (
    StrategyImportError,
    apply_strategy_yaml_to_config,
    import_strategy_yaml_file,
    parse_strategy_yaml,
)

SAMPLE = Path(__file__).resolve().parents[1] / "strategies" / "examples" / "momentum_topn.yaml"


def test_parse_example_yaml() -> None:
    doc = parse_strategy_yaml(SAMPLE.read_text(encoding="utf-8"))
    assert doc.meta.id == "momentum_topn"
    assert doc.signals.mode == "momentum"
    assert len(doc.universe.symbols) >= 4


def test_apply_upserts_strategy(tmp_path: Path) -> None:
    config = {
        "enabled": True,
        "mode": "paper",
        "strategies": [
            {
                "strategy_id": "momentum_topn",
                "label": "old",
                "enabled": True,
                "params": {"mode": "momentum", "top_n": 2, "universe_symbols": ["000001.SZ"]},
            }
        ],
    }
    content = SAMPLE.read_text(encoding="utf-8")
    updated, doc, created = apply_strategy_yaml_to_config(
        config,
        content,
        activate=True,
        merge_globals=True,
    )
    assert created is False
    assert doc.meta.id == "momentum_topn"
    strategies = updated["strategies"]
    assert len(strategies) == 1
    assert strategies[0]["label"] == "日内动量 Top N"
    assert strategies[0]["enabled"] is True
    assert updated["poll_interval_sec"] == 60
    assert updated["portfolio"]["initial_cash"] == 1_000_000.0


def test_import_rejects_missing_python_module_source() -> None:
    bad = yaml.safe_dump(
        {
            "meta": {"id": "py_only", "name": "Python only"},
            "universe": {"symbols": ["000001.SZ"]},
            "signals": {"type": "python_module", "path": "missing_module.py"},
        }
    )
    with pytest.raises(StrategyImportError, match="not found"):
        apply_strategy_yaml_to_config({"strategies": []}, bad)


def test_import_strategy_yaml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "auto-trading.yaml"
    config_path.write_text(
        yaml.safe_dump({"enabled": True, "mode": "paper", "strategies": []}),
        encoding="utf-8",
    )
    summary = import_strategy_yaml_file(
        config_path,
        SAMPLE.read_text(encoding="utf-8"),
        activate=True,
    )
    assert summary["strategy_id"] == "momentum_topn"
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["strategies"][0]["enabled"] is True

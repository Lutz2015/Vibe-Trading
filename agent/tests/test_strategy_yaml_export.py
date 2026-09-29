"""Tests for strategy YAML export and python_module loader."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.strategy_yaml.exporter import export_strategy_yaml
from src.strategy_yaml.importer import apply_strategy_yaml_to_config
from src.strategy_yaml.python_module import build_targets_from_module, write_module

SAMPLE = Path(__file__).resolve().parents[1] / "strategies" / "examples" / "momentum_topn.yaml"
MODULE_SAMPLE = Path(__file__).resolve().parents[1] / "strategies" / "modules" / "momentum_live.py"
PY_YAML = Path(__file__).resolve().parents[1] / "strategies" / "examples" / "momentum_python_module.yaml"


def test_export_roundtrip_builtin() -> None:
    updated, doc, _ = apply_strategy_yaml_to_config(
        {"enabled": True, "mode": "paper", "poll_interval_sec": 60, "strategies": []},
        SAMPLE.read_text(encoding="utf-8"),
    )
    text = export_strategy_yaml(updated, "momentum_topn")
    assert "meta:" in text
    assert "momentum_topn" in text
    assert doc.meta.id == "momentum_topn"


def test_python_module_build_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    write_module("test_mom", MODULE_SAMPLE.read_text(encoding="utf-8"))
    quotes = {
        "000001.SZ": SimpleNamespace(price=10.5, prev_close=10.0),
        "600519.SH": SimpleNamespace(price=1500.0, prev_close=1600.0),
    }
    targets = build_targets_from_module(
        "test_mom",
        quotes,
        list(quotes),
        {"top_n": 1},
    )
    assert len(targets) == 1
    assert targets[0]["symbol"] == "000001.SZ"
    assert targets[0]["weight"] == pytest.approx(1.0)


def test_import_python_module_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = {"enabled": True, "mode": "paper", "strategies": []}
    updated, doc, created = apply_strategy_yaml_to_config(
        config,
        PY_YAML.read_text(encoding="utf-8"),
        activate=True,
    )
    assert created is True
    assert doc.signals.type == "python_module"
    entry = updated["strategies"][0]
    assert entry["signal_type"] == "python_module"
    assert Path(entry["module_path"]).exists()

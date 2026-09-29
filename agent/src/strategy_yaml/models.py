"""Pydantic models for person-trading-strategy.yaml v0.1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

STRATEGY_ID_PATTERN = r"^[a-zA-Z0-9_-]{2,64}$"
BUILTIN_MODES = (
    "momentum",
    "mean_reversion",
    "equal_weight",
    "low_volatility",
    "strong_hand",
    "dip_buy",
)


class StrategyMeta(BaseModel):
    id: str = Field(..., min_length=2, max_length=64, pattern=STRATEGY_ID_PATTERN)
    name: str = Field(..., min_length=1, max_length=120)
    version: str = "0.1.0"
    market: str = "a_share"
    description: str = ""


class StrategyUniverse(BaseModel):
    symbols: list[str] = Field(..., min_length=1)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        cleaned = [str(item).strip().upper() for item in values if str(item).strip()]
        if not cleaned:
            raise ValueError("universe.symbols must not be empty")
        return cleaned


class StrategySignals(BaseModel):
    type: Literal["builtin", "python_module", "yaml_rules"] = "builtin"
    mode: str = "momentum"
    path: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return str(value or "momentum").strip().lower()


class StrategyExecution(BaseModel):
    mode: str = "paper"
    rebalance: str = "trading_days"
    rebalance_every_trading_days: int = Field(default=15, ge=1, le=252)
    time_local: str = "14:50"
    poll_interval_sec: int = Field(default=60, ge=10, le=3600)
    trading_hours: dict[str, str] = Field(default_factory=lambda: {"start": "09:30", "end": "15:00"})


class StrategyPortfolio(BaseModel):
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    total_exposure: float = Field(default=1.0, gt=0, le=2.0)
    max_single_weight: float = Field(default=0.5, gt=0, le=1.0)
    min_trade_lot: int = Field(default=100, ge=1)
    fee_bps: float = Field(default=3.0, ge=0)


class StrategyDocument(BaseModel):
    meta: StrategyMeta
    universe: StrategyUniverse
    signals: StrategySignals = Field(default_factory=StrategySignals)
    execution: StrategyExecution = Field(default_factory=StrategyExecution)
    portfolio: StrategyPortfolio = Field(default_factory=StrategyPortfolio)
    risk: dict[str, Any] = Field(default_factory=dict)
    agent: dict[str, Any] = Field(default_factory=dict)

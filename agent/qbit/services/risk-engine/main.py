from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

_QBIT_ROOT = Path(__file__).resolve().parents[2]
RISK_LIMITS_PATH = Path(
    os.getenv(
        "RISK_LIMITS_PATH",
        str(_QBIT_ROOT / "configs" / "sim-risk-limits.yaml"),
    )
)

app = FastAPI(title="risk-engine", version="0.1.0")

_trading_enabled_override: bool | None = None
_kill_switch_override: bool | None = None


def load_risk_limits() -> dict[str, Any]:
    with RISK_LIMITS_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


class OrderRiskCheckRequest(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(gt=0)
    notional: float = Field(gt=0)
    portfolio_value: float = Field(gt=0)


class RiskCheckResponse(BaseModel):
    accepted: bool
    reason: str


class OpsToggleRequest(BaseModel):
    enabled: bool


def effective_trading_state() -> tuple[bool, bool]:
    limits = load_risk_limits()
    trading_enabled = (
        _trading_enabled_override
        if _trading_enabled_override is not None
        else limits["global"]["trading_enabled"]
    )
    kill_switch = (
        _kill_switch_override
        if _kill_switch_override is not None
        else limits["global"]["kill_switch"]
    )
    if kill_switch:
        trading_enabled = False
    return trading_enabled, kill_switch


@app.get("/health")
def health() -> dict[str, str | bool]:
    trading_enabled, kill_switch = effective_trading_state()
    return {
        "status": "ok",
        "service": "risk-engine",
        "tradingEnabled": trading_enabled,
        "killSwitch": kill_switch,
    }


@app.get("/ops/status")
def ops_status() -> dict[str, bool]:
    trading_enabled, kill_switch = effective_trading_state()
    return {"tradingEnabled": trading_enabled, "killSwitch": kill_switch}


@app.post("/ops/trading-toggle")
def set_trading_toggle(payload: OpsToggleRequest) -> dict[str, bool]:
    global _trading_enabled_override
    _trading_enabled_override = payload.enabled
    trading_enabled, kill_switch = effective_trading_state()
    return {"tradingEnabled": trading_enabled, "killSwitch": kill_switch}


@app.post("/ops/kill-switch")
def set_kill_switch(payload: OpsToggleRequest) -> dict[str, bool]:
    global _kill_switch_override, _trading_enabled_override
    _kill_switch_override = payload.enabled
    if payload.enabled:
        _trading_enabled_override = False
    trading_enabled, kill_switch = effective_trading_state()
    return {"killSwitch": kill_switch, "tradingEnabled": trading_enabled}


@app.post("/risk/check-order", response_model=RiskCheckResponse)
def check_order(payload: OrderRiskCheckRequest) -> RiskCheckResponse:
    trading_enabled, kill_switch = effective_trading_state()
    if not trading_enabled or kill_switch:
        return RiskCheckResponse(accepted=False, reason="trading_disabled_or_killed")

    limits = load_risk_limits()
    max_weight = limits["position"]["max_single_symbol_weight"]
    if payload.notional / payload.portfolio_value > max_weight:
        return RiskCheckResponse(accepted=False, reason="single_symbol_weight_exceeded")

    return RiskCheckResponse(accepted=True, reason="ok")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8002, reload=False)

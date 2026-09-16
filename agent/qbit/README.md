# Qbit Quant Platform (embedded)

This directory contains the **Qbit** A-share quant trading scaffold integrated into Personal-Trading.

## What is included

| Module | Path | Capability |
|--------|------|------------|
| Strategy engine | `services/strategy-engine/` | Signals, portfolio allocate, rebalance |
| Risk engine | `services/risk-engine/` | Order checks, kill switch |
| Execution engine | `services/execution-engine/` | OMS state machine, paper/live routing |
| Market data | `services/market-data/` | akshare / sina / eastmoney / joinquant |
| Backtest engine | `services/backtest_engine/` | SMA, multi-factor, JoinQuant templates |
| Reconciliation | `services/reconciliation-engine/` | Ledger consistency |
| Monitoring | `services/monitoring-engine/` | Alerts, ingest |
| Strategy registry | `services/strategy-registry-engine/` | Versioning, canary, rollback |
| Portfolio ledger | `services/portfolio-ledger/` | Paper account JSON ledger |
| Trading orchestrator | `services/trading-orchestrator/` | Scheduled auto rebalance |

## HTTP API

Mounted at **`/qbit/*`** on the main FastAPI server (see `agent/src/qbit/platform.py`).

Examples:

- `GET /qbit/health`
- `GET /qbit/ledger/snapshot`
- `POST /qbit/automation/run-cycle`
- `POST /qbit/backtest/run`
- `GET /qbit/strategy/repository`

Full contract: `agent/qbit/docs/API_CONTRACT.md`.

## Tests

```bash
pytest agent/tests/qbit -q
```

51 unit tests migrated from upstream Qbit.

## Configuration

- YAML: `agent/qbit/configs/` (default sim risk limits + auto-trading template)
- Runtime data: `~/.person-trading/qbit/` (ledgers, automation state)
- Strategies: `agent/qbit/strategies/`

## Environment

| Variable | Default |
|----------|---------|
| `MARKET_DATA_PROVIDER` | `akshare` |
| `AUTO_TRADING_ENABLED` | off (set `1` to start scheduler on boot) |
| `RISK_LIMITS_PATH` | `configs/sim-risk-limits.yaml` |
| `JQ_USERNAME` / `JQ_PASSWORD` | optional JoinQuant |

## UI

Web: **量化交易台** → `/quant-desk` in the React sidebar.

## Safety

- Default **sim-risk-limits** keeps `trading_enabled: false` until ops toggle.
- Live execution requires explicit `EXECUTION_LIVE_ENABLED=1`.
- Do not enable auto-trading in production without review.

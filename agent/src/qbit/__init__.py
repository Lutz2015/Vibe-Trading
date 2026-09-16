"""Qbit quant platform integration — paper trading, risk, execution, automation."""

from src.qbit.platform import create_qbit_app, mount_qbit_routes

__all__ = ["create_qbit_app", "mount_qbit_routes"]

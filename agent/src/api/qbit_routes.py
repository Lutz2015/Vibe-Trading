"""Mount the embedded Qbit quant platform (paper trading stack)."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


def register_qbit_routes(
    app: FastAPI,
    require_local_or_auth=None,
) -> None:
    """Attach Qbit at ``/qbit/*`` — strategy, risk, execution, ledger, automation."""
    try:
        from src.qbit.platform import mount_qbit_routes

        if require_local_or_auth is not None:
            bearer = HTTPBearer(auto_error=False)

            @app.middleware("http")
            async def protect_qbit_routes(request: Request, call_next):
                if request.url.path == "/qbit" or request.url.path.startswith("/qbit/"):
                    credentials: HTTPAuthorizationCredentials | None = await bearer(request)
                    await require_local_or_auth(request=request, cred=credentials)
                return await call_next(request)

        mount_qbit_routes(app, prefix="/qbit")
    except Exception as exc:  # noqa: BLE001 — core app must still boot
        logger.warning("Qbit quant platform failed to mount: %s", exc)

        if require_local_or_auth:

            @app.get("/qbit/health", dependencies=[Depends(require_local_or_auth)])
            async def qbit_unavailable() -> dict[str, str]:
                return {"status": "error", "message": str(exc)[:200]}

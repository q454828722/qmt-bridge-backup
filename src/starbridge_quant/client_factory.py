"""Client factory helpers for StarBridge Quant research and strategy code."""

from __future__ import annotations

import os
from dataclasses import dataclass

from starbridge_quant import QMTClient


@dataclass(frozen=True)
class StarbridgeClientConfig:
    host: str
    port: int
    api_key: str = ""


def _env_get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _default_host() -> str:
    host = _env_get("STARBRIDGE_CLIENT_HOST", "QMT_BRIDGE_CLIENT_HOST")
    if host:
        return host
    host = _env_get("STARBRIDGE_HOST", "QMT_BRIDGE_HOST")
    if host and host != "0.0.0.0":
        return host
    return "127.0.0.1"


def get_starbridge_config(*, with_trading: bool = False) -> StarbridgeClientConfig:
    """Return client config from preferred StarBridge env vars and legacy fallbacks."""
    host = _default_host()
    port = int(_env_get("STARBRIDGE_PORT", "QMT_BRIDGE_PORT", default="18888"))
    api_key = _env_get("STARBRIDGE_API_KEY", "QMT_BRIDGE_API_KEY")
    if with_trading and not api_key:
        raise RuntimeError(
            "STARBRIDGE_API_KEY (or legacy QMT_BRIDGE_API_KEY) is required when with_trading=True"
        )
    return StarbridgeClientConfig(host=host, port=port, api_key=api_key if with_trading else "")


def make_starbridge_client(
    *,
    with_trading: bool = False,
    host: str | None = None,
    port: int | None = None,
    api_key: str | None = None,
) -> QMTClient:
    """Create a client using environment defaults, with optional overrides."""
    config = get_starbridge_config(with_trading=with_trading)
    resolved_host = host or config.host
    resolved_port = port if port is not None else config.port
    resolved_key = api_key if api_key is not None else config.api_key
    if with_trading and not resolved_key:
        raise RuntimeError(
            "api_key or STARBRIDGE_API_KEY (or legacy QMT_BRIDGE_API_KEY) is required when with_trading=True"
        )
    return QMTClient(host=resolved_host, port=resolved_port, api_key=resolved_key)


QMTClientConfig = StarbridgeClientConfig
get_qmt_config = get_starbridge_config
make_qmt_client = make_starbridge_client

__all__ = [
    "QMTClientConfig",
    "StarbridgeClientConfig",
    "get_qmt_config",
    "get_starbridge_config",
    "make_qmt_client",
    "make_starbridge_client",
]

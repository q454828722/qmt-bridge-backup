"""Shared QMT Bridge client factory for research and strategy projects.

Configuration is read from environment variables only:

- QMT_BRIDGE_CLIENT_HOST: client-facing host, default 127.0.0.1
- QMT_BRIDGE_PORT: client-facing port, default 18888
- QMT_BRIDGE_API_KEY: required when with_trading=True
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from qmt_bridge import QMTClient


@dataclass(frozen=True)
class QMTClientConfig:
    host: str
    port: int
    api_key: str = ""


def _default_host() -> str:
    host = os.environ.get("QMT_BRIDGE_CLIENT_HOST", "").strip()
    if host:
        return host
    host = os.environ.get("QMT_BRIDGE_HOST", "").strip()
    if host and host != "0.0.0.0":
        return host
    return "127.0.0.1"


def get_qmt_config(*, with_trading: bool = False) -> QMTClientConfig:
    """Return client config from environment variables."""
    host = _default_host()
    port = int(os.environ.get("QMT_BRIDGE_PORT", "18888"))
    api_key = os.environ.get("QMT_BRIDGE_API_KEY", "").strip()
    if with_trading and not api_key:
        raise RuntimeError("QMT_BRIDGE_API_KEY is required when with_trading=True")
    return QMTClientConfig(host=host, port=port, api_key=api_key if with_trading else "")


def make_qmt_client(
    *,
    with_trading: bool = False,
    host: str | None = None,
    port: int | None = None,
    api_key: str | None = None,
) -> QMTClient:
    """Create a QMTClient using environment defaults, with optional overrides."""
    config = get_qmt_config(with_trading=with_trading)
    resolved_host = host or config.host
    resolved_port = port if port is not None else config.port
    resolved_key = api_key if api_key is not None else config.api_key
    if with_trading and not resolved_key:
        raise RuntimeError("api_key or QMT_BRIDGE_API_KEY is required when with_trading=True")
    return QMTClient(host=resolved_host, port=resolved_port, api_key=resolved_key)


__all__ = ["QMTClientConfig", "get_qmt_config", "make_qmt_client"]

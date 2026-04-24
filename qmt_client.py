"""Backward-compatible wrapper for the renamed StarBridge Quant client helper."""

from starbridge_quant.client_factory import (
    QMTClientConfig,
    StarbridgeClientConfig,
    get_qmt_config,
    get_starbridge_config,
    make_qmt_client,
    make_starbridge_client,
)

__all__ = [
    "QMTClientConfig",
    "StarbridgeClientConfig",
    "get_qmt_config",
    "get_starbridge_config",
    "make_qmt_client",
    "make_starbridge_client",
]

"""系统元数据路由模块 /api/meta/*。

提供市场列表、K线周期列表、证券列表、服务版本、连接状态等系统级元数据端点。
底层调用 xtquant.xtdata 的元数据接口，包括：
- xtdata.get_markets()                — 获取所有市场列表
- xtdata.get_period_list()            — 获取支持的 K 线周期列表
- xtdata.get_stock_list_in_sector()   — 按类别获取证券列表
- xtdata.get_market_last_trade_date() — 获取市场最近交易日
- xtdata.get_client()                 — 获取客户端连接对象
- xtdata.get_quote_server_status()    — 获取行情服务器状态
"""

from fastapi import APIRouter, Query
from xtquant import xtdata

from ..helpers import _numpy_to_python

router = APIRouter(prefix="/api/meta", tags=["meta"])

FALLBACK_MARKETS = ["SH", "SZ", "BJ", "CFE", "DCE", "CZCE", "SHFE", "INE", "GFEX", "HK"]
FALLBACK_PERIODS = ["tick", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mon"]


def _xtdata_callable(name: str):
    """返回 xtdata 上可调用的函数；若当前版本不支持则返回 None。"""
    func = getattr(xtdata, name, None)
    if callable(func):
        return func
    return None


def _connection_status_payload() -> dict:
    """兼容不同 xtdata/IPythonApiClient 版本的连接状态查询。"""
    client = xtdata.get_client()

    for name in ("is_connected", "get_connect_status"):
        method = getattr(client, name, None)
        if callable(method):
            return {"connected": bool(method()), "source": f"client.{name}"}

    return {
        "connected": False,
        "source": "unavailable",
        "error": "xtdata client does not expose a supported connection status API",
    }


@router.get("/markets")
def get_markets():
    """获取所有支持的市场列表。

    Returns:
        markets: 市场信息列表（如 SH、SZ、CFE 等）。
    优先调用 ``xtdata.get_markets()``；若当前 xtdata 版本未暴露该 API，
    则回退到项目内维护的常见市场列表，避免直接 500。
    """
    func = _xtdata_callable("get_markets")
    if func is None:
        return {
            "markets": FALLBACK_MARKETS,
            "source": "fallback",
            "warning": "xtdata.get_markets is unavailable in the current xtdata build",
        }

    raw = func()
    return {"markets": _numpy_to_python(raw), "source": "xtdata.get_markets"}


@router.get("/period_list")
def get_period_list():
    """获取所有支持的 K 线周期列表。

    Returns:
        periods: 周期列表（如 tick、1m、5m、15m、30m、60m、1d 等）。
    优先调用 ``xtdata.get_period_list()``；若当前 xtdata 版本未暴露该 API，
    则回退到项目内维护的常见周期列表，避免直接 500。
    """
    func = _xtdata_callable("get_period_list")
    if func is None:
        return {
            "periods": FALLBACK_PERIODS,
            "source": "fallback",
            "warning": "xtdata.get_period_list is unavailable in the current xtdata build",
        }

    raw = func()
    return {"periods": _numpy_to_python(raw), "source": "xtdata.get_period_list"}


@router.get("/stock_list")
def get_stock_list(
    category: str = Query(
        ...,
        description="证券类别，如 沪深A股 / 上证A股 / 深证A股 / 北证A股 / 沪深ETF / 沪深指数",
    ),
):
    """按类别获取证券代码列表。

    Args:
        category: 证券类别名称（即板块名称），如 "沪深A股"、"沪深ETF"。

    Returns:
        category: 查询的类别名称。
        count: 证券数量。
        stocks: 证券代码列表。

    底层调用: xtdata.get_stock_list_in_sector(category)
    """
    stock_list = xtdata.get_stock_list_in_sector(category)
    return {"category": category, "count": len(stock_list), "stocks": stock_list}


@router.get("/last_trade_date")
def get_last_trade_date(
    market: str = Query(..., description="市场代码，如 SH / SZ"),
):
    """获取指定市场的最近交易日。

    Args:
        market: 市场代码。

    Returns:
        market: 市场代码。
        last_trade_date: 最近交易日。

    底层调用: xtdata.get_market_last_trade_date(market)
    """
    date = xtdata.get_market_last_trade_date(market)
    return {"market": market, "last_trade_date": date}


# ---------------------------------------------------------------------------
# 系统状态监控端点
# ---------------------------------------------------------------------------


@router.get("/version")
def get_server_version():
    """获取 StarBridge Quant 服务端版本号。

    Returns:
        version: 服务端版本字符串。
    """
    from ..._version import __version__

    return {"version": __version__}


@router.get("/xtdata_version")
def get_xtdata_version():
    """获取 xtquant/xtdata 库版本号。

    Returns:
        xtdata_version: xtquant 库的版本字符串，获取失败时返回 "unknown"。
    """
    try:
        import xtquant
        version = getattr(xtquant, "__version__", "unknown")
    except Exception:
        version = "unknown"
    return {"xtdata_version": version}


@router.get("/connection_status")
def get_connection_status():
    """检查 xtdata 与行情服务器的连接状态。

    Returns:
        connected: 布尔值，表示是否已连接。
        error: 连接异常时的错误信息（可选）。

    底层调用: xtdata.get_client().get_connect_status()
    """
    return _connection_status_payload()


@router.get("/health")
def health_check():
    """健康检查端点。

    用于负载均衡器或监控系统探测服务是否正常运行。

    Returns:
        status: "ok" 表示服务正常。
    """
    return {"status": "ok"}


@router.get("/quote_server_status")
def get_quote_server_status():
    """获取行情服务器的详细连接状态。

    Returns:
        data: 行情服务器状态详细信息。
        error: 查询异常时的错误信息（可选）。

    底层调用: xtdata.get_quote_server_status()
    """
    func = _xtdata_callable("get_quote_server_status")
    if func is not None:
        status = func()
        return {"data": _numpy_to_python(status), "source": "xtdata.get_quote_server_status"}

    payload = _connection_status_payload()
    return {
        "data": {
            "connected": payload["connected"],
            "status": "connected" if payload["connected"] else "disconnected",
        },
        "source": payload.get("source", "fallback"),
        "warning": "xtdata.get_quote_server_status is unavailable; falling back to client connection state",
    }

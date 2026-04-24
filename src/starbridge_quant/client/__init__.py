"""StarBridge Quant 客户端 — 跨平台 HTTP/WebSocket 客户端（无需安装 xtquant）。

通过 Mixin 组合模式将行情、交易、数据下载等功能拆分到独立模块，
最终由 ``QMTClient`` 类统一继承，对外提供完整的 API 访问能力。

架构说明:
    BaseClient 提供底层 HTTP 传输（_get/_post/_delete），所有 Mixin
    通过 self._get() 等方法与服务端通信，无需依赖 xtquant 库。
"""

from starbridge_quant.client.base import BaseClient
from starbridge_quant.client.market import MarketMixin
from starbridge_quant.client.tick import TickMixin
from starbridge_quant.client.sector import SectorMixin
from starbridge_quant.client.calendar import CalendarMixin
from starbridge_quant.client.financial import FinancialMixin
from starbridge_quant.client.instrument import InstrumentMixin
from starbridge_quant.client.option import OptionMixin
from starbridge_quant.client.etf import ETFMixin
from starbridge_quant.client.cb import CBMixin
from starbridge_quant.client.futures import FuturesMixin
from starbridge_quant.client.meta import MetaMixin
from starbridge_quant.client.download import DownloadMixin
from starbridge_quant.client.formula import FormulaMixin
from starbridge_quant.client.hk import HKMixin
from starbridge_quant.client.tabular import TabularMixin
from starbridge_quant.client.utility import UtilityMixin
from starbridge_quant.client.trading import TradingMixin
from starbridge_quant.client.credit import CreditMixin
from starbridge_quant.client.fund import FundMixin
from starbridge_quant.client.smt import SMTMixin
from starbridge_quant.client.bank import BankMixin
from starbridge_quant.client.websocket import WebSocketMixin


class QMTClient(
    MarketMixin,
    TickMixin,
    SectorMixin,
    CalendarMixin,
    FinancialMixin,
    InstrumentMixin,
    OptionMixin,
    ETFMixin,
    CBMixin,
    FuturesMixin,
    MetaMixin,
    DownloadMixin,
    FormulaMixin,
    HKMixin,
    TabularMixin,
    UtilityMixin,
    TradingMixin,
    CreditMixin,
    FundMixin,
    SMTMixin,
    BankMixin,
    WebSocketMixin,
    BaseClient,
):
    """StarBridge Quant 全功能客户端。

    通过 HTTP 请求与 StarBridge Quant 服务端通信，封装了行情查询、数据下载、
    交易委托、两融业务、银证转账等全部功能。

    行情数据类方法无需认证即可使用；交易类方法需要提供 API Key。

    示例::

        from starbridge_quant import QMTClient

        # 仅查询行情（无需 API Key）
        client = QMTClient("192.168.1.100")
        df = client.get_history("000001.SZ", period="1d", count=60)

        # 交易操作（需要 API Key）
        client = QMTClient("192.168.1.100", api_key="your-key")
        result = client.place_order("000001.SZ", order_type=23, order_volume=100)
    """


__all__ = ["QMTClient"]

# 本地策略开发 API 使用手册

这份手册面向当前这台 Windows 机器上的本地部署：

- QMT Bridge 地址：`http://127.0.0.1:18888`
- 局域网地址：`http://<your-lan-ip>:18888`
- Swagger 文档：`http://127.0.0.1:18888/docs`
- miniQMT 用户目录：`<miniqmt-userdir>`
- 交易账号：`<your-trading-account>`

交易、融资融券、资金划转、银证转账、约定式交易接口需要 `X-API-Key`。API Key 在 `D:\qmt-bridge\.env` 里，不要写死到策略代码、Git 仓库或笔记里。

## 1. 先做连通性检查

PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:18888/api/meta/health
Invoke-RestMethod http://127.0.0.1:18888/api/meta/connection_status
```

Python：

```python
from qmt_bridge import QMTClient

client = QMTClient(host="127.0.0.1", port=18888)

print(client.health_check())
print(client.get_connection_status())
```

交易模块检查：

```python
from pathlib import Path
from qmt_bridge import QMTClient


def read_env(path: str, key: str) -> str:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            if name == key:
                return value.strip().strip('"').strip("'")
    return ""


api_key = read_env(r"D:\qmt-bridge\.env", "QMT_BRIDGE_API_KEY")
client = QMTClient(host="127.0.0.1", port=18888, api_key=api_key)

print(client.get_account_status())
```

## 2. 建议的策略项目连接模板

在你的策略项目里放一个类似 `qmt_client.py` 的小模块，后续策略统一从这里拿客户端：

```python
from pathlib import Path
from qmt_bridge import QMTClient


QMT_HOST = "127.0.0.1"
QMT_PORT = 18888
QMT_ENV = Path(r"D:\qmt-bridge\.env")


def _read_env_value(key: str, default: str = "") -> str:
    if not QMT_ENV.is_file():
        return default
    for raw in QMT_ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return default


def make_qmt_client(with_trading: bool = False) -> QMTClient:
    api_key = _read_env_value("QMT_BRIDGE_API_KEY") if with_trading else ""
    return QMTClient(host=QMT_HOST, port=QMT_PORT, api_key=api_key)
```

使用：

```python
from qmt_client import make_qmt_client

data_client = make_qmt_client()
trade_client = make_qmt_client(with_trading=True)
```

## 3. 实时行情快照

适合盘中轮询、风控检查、简易信号判断：

```python
from qmt_client import make_qmt_client

client = make_qmt_client()

stocks = ["000001.SZ", "600519.SH", "000001.SH"]
snapshot = client.get_market_snapshot(stocks)

for code, tick in snapshot.items():
    print(code, tick.get("lastPrice"), tick.get("volume"), tick.get("amount"))
```

直接 HTTP：

```powershell
curl.exe "http://127.0.0.1:18888/api/market/full_tick?stocks=000001.SZ,600519.SH"
```

## 4. 历史 K 线

常用增强版接口 `get_history_ex`，支持多股票、复权和填充：

```python
from qmt_client import make_qmt_client

client = make_qmt_client()

bars = client.get_history_ex(
    ["000001.SZ", "600519.SH"],
    period="1d",
    count=120,
    dividend_type="front",
    fill_data=True,
)

df = bars["000001.SZ"]
print(df.tail())
```

分钟线：

```python
bars = client.get_history_ex(
    ["000001.SZ"],
    period="1m",
    start_time="20260421093000",
    end_time="20260421150000",
    dividend_type="none",
)
```

只读本地缓存，不触发补下载：

```python
local_bars = client.get_local_data(
    ["000001.SZ"],
    period="1d",
    start_time="20250101",
    end_time="",
)
```

## 5. 数据预下载

批量下载历史数据：

```python
from qmt_client import make_qmt_client

client = make_qmt_client()

client.download_batch(
    stocks=["000001.SZ", "600519.SH"],
    period="1d",
    start_time="20250101",
    end_time="",
)
```

常用基础数据下载：

```python
client.download_sector_data()
client.download_holiday_data()
client.download_index_weight()
client.download_etf_info()
client.download_cb_data()
```

财务数据下载和查询：

```python
client.download_financial_data2(
    stocks=["000001.SZ"],
    tables=["Balance", "Income", "CashFlow"],
)

financial = client.get_financial_data(
    stocks=["000001.SZ"],
    tables=["Balance", "Income"],
)
```

## 6. 股票池、交易日和基础信息

板块成分：

```python
stocks = client.get_sector_stocks("沪深A股")
print(len(stocks), stocks[:10])
```

交易日历：

```python
dates = client.get_trading_dates("SH", start_time="20260101", end_time="20260421")
print(dates[-5:])

print(client.is_trading_date("SH", "20260421"))
print(client.get_prev_trading_date("SH", "20260421"))
```

股票名称和市场归属：

```python
print(client.get_stock_name("000001.SZ"))
print(client.get_batch_stock_name(["000001.SZ", "600519.SH"]))
print(client.code_to_market("600519.SH"))
```

## 7. 账户查询

以下接口需要 `with_trading=True`：

```python
from qmt_client import make_qmt_client

client = make_qmt_client(with_trading=True)

print(client.get_account_status())
asset = client.query_asset()
positions = client.query_positions()
orders = client.query_orders(cancelable_only=False)
trades = client.query_trades()

print(asset)
print(positions)
print(orders)
print(trades)
```

直接 HTTP：

```powershell
curl.exe -H "X-API-Key: <your-api-key>" http://127.0.0.1:18888/api/trading/asset
curl.exe -H "X-API-Key: <your-api-key>" http://127.0.0.1:18888/api/trading/positions
curl.exe -H "X-API-Key: <your-api-key>" http://127.0.0.1:18888/api/trading/orders
```

## 8. 下单模板

先用模拟账号、小数量、明确价格测试。常见股票 `order_type`：`23` 买入，`24` 卖出；`price_type`、委托类型以券商 miniQMT / xtquant 常量为准。README 示例中 `price_type=5` 表示最新价。

建议所有策略下单都经过一个带 `dry_run` 的包装：

```python
from dataclasses import dataclass
from qmt_client import make_qmt_client


@dataclass
class OrderIntent:
    stock_code: str
    side: str
    volume: int
    price_type: int = 5
    price: float = 0.0
    remark: str = "strategy"


def submit_order(intent: OrderIntent, dry_run: bool = True):
    if intent.side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if intent.volume <= 0 or intent.volume % 100 != 0:
        raise ValueError("A-share order volume should normally be a positive multiple of 100")

    order_type = 23 if intent.side == "buy" else 24

    payload = {
        "stock_code": intent.stock_code,
        "order_type": order_type,
        "order_volume": intent.volume,
        "price_type": intent.price_type,
        "price": intent.price,
        "strategy_name": "local_strategy",
        "order_remark": intent.remark,
    }

    if dry_run:
        print("DRY RUN:", payload)
        return {"dry_run": True, "payload": payload}

    client = make_qmt_client(with_trading=True)
    return client.place_order(**payload)


submit_order(OrderIntent("000001.SZ", "buy", 100), dry_run=True)
```

确认要真实下单时：

```python
result = submit_order(
    OrderIntent("000001.SZ", "buy", 100, price_type=5, remark="manual_test"),
    dry_run=False,
)
print(result)
```

撤单：

```python
client = make_qmt_client(with_trading=True)
client.cancel_order(order_id=123456)
```

## 9. 盘中实时订阅

WebSocket 适合低频 UI 推送、实时监控和分钟线增量更新：

```python
import asyncio
from qmt_client import make_qmt_client


client = make_qmt_client()


def on_data(data):
    print(data)


asyncio.run(
    client.subscribe_realtime(
        stocks=["000001.SZ", "600519.SH"],
        period="tick",
        callback=on_data,
    )
)
```

实时分钟 K 线：

```python
asyncio.run(
    client.subscribe_realtime(
        stocks=["000001.SZ"],
        period="1m",
        callback=on_data,
    )
)
```

全市场行情：

```python
asyncio.run(
    client.subscribe_whole_quote(
        codes=["SH", "SZ"],
        callback=on_data,
    )
)
```

交易回报：

```python
client = make_qmt_client(with_trading=True)

asyncio.run(
    client.subscribe_trade_events(
        callback=on_data,
    )
)
```

## 10. REST 调用速查

常用只读接口：

| 场景 | 方法 | 路径 |
|---|---|---|
| 健康检查 | GET | `/api/meta/health` |
| 行情连接 | GET | `/api/meta/connection_status` |
| 股票列表 | GET | `/api/meta/stock_list?category=沪深A股` |
| 主要指数 | GET | `/api/market/indices` |
| 实时快照 | GET | `/api/market/full_tick?stocks=000001.SZ,600519.SH` |
| 增强 K 线 | GET | `/api/market/market_data_ex` |
| 本地缓存 K 线 | GET | `/api/market/local_data` |
| 板块列表 | GET | `/api/sector/list` |
| 板块成分 | GET | `/api/sector/stocks?sector=沪深A股` |
| 交易日历 | GET | `/api/calendar/trading_dates` |

常用交易查询接口：

| 场景 | 方法 | 路径 |
|---|---|---|
| 账户连接 | GET | `/api/trading/account_status` |
| 资产 | GET | `/api/trading/asset` |
| 持仓 | GET | `/api/trading/positions` |
| 委托 | GET | `/api/trading/orders` |
| 成交 | GET | `/api/trading/trades` |
| 单只持仓 | GET | `/api/trading/position/{stock_code}` |

常用交易操作接口：

| 场景 | 方法 | 路径 |
|---|---|---|
| 下单 | POST | `/api/trading/order` |
| 撤单 | POST | `/api/trading/cancel` |
| 批量下单 | POST | `/api/trading/batch_order` |
| 批量撤单 | POST | `/api/trading/batch_cancel` |

完整接口清单建议查看运行中的 Swagger：

```text
http://127.0.0.1:18888/docs
```

## 11. 常见问题

### 服务连不上

先检查本机：

```powershell
Get-NetTCPConnection -LocalPort 18888
Get-Content D:\qmt-bridge\logs\server.err.log -Tail 80
```

再检查 miniQMT 是否仍在登录状态，且使用独立交易模式。

### 客户端默认端口不对

`QMTClient` 默认端口是 `8000`，本机部署用的是 `18888`，必须显式传入：

```python
QMTClient(host="127.0.0.1", port=18888)
```

### 交易接口返回 401 或 503

- `401`：没有传 `X-API-Key` 或 API Key 不匹配。
- `503 API key not configured`：服务端未配置 API Key。
- `503 Trading module is not enabled`：服务端未启用交易模块，或交易模块初始化失败。

### 多请求并发慢

服务端会串行化 xtdata 调用来避免 xtdata C 扩展并发崩溃。策略里优先批量请求，例如一次传多个 `stocks`，不要为每只股票单独开线程请求。

### 策略部署建议

- 行情、历史数据、股票池查询可以不带 API Key。
- 交易查询和下单统一走单独的 `trade_client`。
- 所有真实下单函数默认 `dry_run=True`，只有人工确认或策略生产模式才关闭。
- 先查 `account_status`、`asset`、`positions`，再进入交易逻辑。
- 下单后记录 `order_id`、请求参数、返回值和策略信号，便于复盘。

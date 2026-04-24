# 全 A 数据补齐计划

生成时间：2026-04-21

目标：为“金元顺安元启风格复刻”准备本地可用的全 A 股票研究缓存，当前只优先覆盖财务数据、近三年日线和基础元数据；分钟级 K 线暂缓。补齐过程按“小批量测试先行、单进程串行、低速可恢复”的原则执行，避免对 QMT 行情服务器造成持续高压。

## 当前基线

- 全 A 股票池：5202 只，来自 `data/yuanqi_replica/basic/a_share_universe.csv`。
- 基础元数据已落地：股票详情、沪深 300/中证 500/中证 1000 权重、可转债列表。
- 财务缓存覆盖率：2 / 5202，只覆盖此前烟测的 `000001.SZ` 和 `600519.SH`。
- `D:` 盘剩余空间约 226 GiB。分钟线暂不补齐，先把容量留给财务和日线研究缓存。

## 数据清单

第一优先级：

- 股票池与合约详情：代码、名称、上市状态、流通股本、总股本、昨收。
- 指数权重：沪深 300、中证 500、中证 1000，用于风格约束和基准暴露。
- 财务表：`Balance`、`Income`、`CashFlow`，用于质量、成长、现金流、杠杆因子。
- K 线：`1d` 近三年优先，后续视需要再扩展更长历史。
- ST 与停复牌相关数据：用于剔除不可交易样本和风险过滤。

第二优先级：

- 分钟级 K 线：暂缓补齐；后续若确实需要短周期交易特征，再单独制定容量和限速计划。
- 可转债数据：暂不进入股票多因子池，但保留给以后做风险偏好和转债映射。
- ETF 信息：当前券商版 `xtquant` 缺少下载函数，暂不作为阻塞项。

## 限流原则

- 一次只运行一个下载进程。
- 避免在交易时段拉取大批量历史数据，推荐 15:30 以后或夜间执行。
- 财务数据按小批量下载，初始 `batch-size=10`、批次间隔 `2.0` 秒；稳定后可调到 `20` 和 `1.0` 秒。
- K 线逐只下载，初始 `--kline-delay 0.30` 到 `0.50` 秒；如果失败率高于 3%，加大间隔并重跑。
- 不下载分钟级 K 线；本阶段只做财务和 `1d`。
- 先用 `--limit 20` 对同一条下载链路做小批量测试，通过后再放大全 A。
- 每阶段完成后运行 `scripts/check_cache_progress.py`，确认覆盖率、失败数和最新日期。

## 阶段 A：20 只小批量测试

先用全 A 板块的前 20 只标的测试财务和近三年日线。测试命令与全 A 命令使用同一个脚本，只多一个 `--limit 20`。

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --limit 20 --periods 1d --tables Balance,Income,CashFlow --since 2024 --batch-size 10 --timeout 300 --delay 2.0 --kline-delay 0.20 --kline-timeout 15 --max-retries 1
```

测试通过标准：

- K 线 `1d` 成功 20 只，失败数为 0 或接近 0。
- 财务数据成功 20 只，失败数为 0 或接近 0。
- 日线最新日期接近最近交易日。
- 若失败率高于 3%，先把 `--delay` 提到 `3.0`、`--kline-delay` 提到 `0.50`，重跑同一命令。

## 阶段 0：基础刷新

这些数据量较小，服务启动时也会自动刷新一部分，可在大下载前手动跑一轮：

```powershell
curl.exe -X POST http://127.0.0.1:18888/api/download/sector_data
curl.exe -X POST http://127.0.0.1:18888/api/download/index_weight
curl.exe -X POST http://127.0.0.1:18888/api/download/cb_data
curl.exe -X POST http://127.0.0.1:18888/api/download/history_contracts
```

## 阶段 1：财务全 A 补齐

先补财务，因为它是后续质量、成长、估值因子的硬前提，且数据量小于分钟线。

保守命令：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --skip-kline --tables Balance,Income,CashFlow --batch-size 10 --timeout 300 --delay 2.0 --max-retries 2
```

稳定后提速：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --skip-kline --tables Balance,Income,CashFlow --batch-size 20 --timeout 300 --delay 1.0 --max-retries 2
```

验收口径：

- `financial.status_counts.ok >= 95% * 5202`。
- `Balance / Income / CashFlow` 三张表各自 `fresh_complete` 接近全 A 有效股票数量。
- 退市、未披露、极新上市公司允许少量缺失。

## 阶段 2：日线历史补齐

日线用于长期回测、波动率、动量、换手、停牌识别和市值因子对齐。本阶段只补近三年，即从 2024 年开始至今。

最近三年：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --periods 1d --skip-financial --since 2024 --kline-delay 0.20 --kline-timeout 15 --max-retries 1
```

后续若确实需要更长历史，再扩展到 2020：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --periods 1d --skip-financial --since 2020 --kline-delay 0.20 --kline-timeout 15 --max-retries 1
```

更长历史可在以上稳定后再执行：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --periods 1d --skip-financial --since 2015 --kline-delay 0.30 --kline-timeout 15 --max-retries 1
```

验收口径：

- `1d` 覆盖率接近 100%。
- `newest_latest_date` 接近最近交易日。
- 年度覆盖率从近年到远年逐步提高。

## 阶段 3：分钟线暂缓

1 分钟线和 5 分钟线数据量过大，本阶段暂停补齐。只有当财务和近三年日线稳定完成，并确认后续因子确实需要分钟级特征时，再单独启动。

## 阶段 4：进度检查

标准检查：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\check_cache_progress.py --periods 1d --tables Balance,Income,CashFlow
```

带年度覆盖率检查：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\check_cache_progress.py --periods 1d --tables Balance,Income,CashFlow --year-from 2024
```

输出文件：

- `data/yuanqi_replica/basic/cache_progress_summary.json`
- `data/yuanqi_replica/basic/cache_progress_kline.csv`
- `data/yuanqi_replica/basic/cache_progress_financial.csv`

## 阶段 5：日常维护

收盘后增量：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --periods 1d --skip-financial --kline-delay 0.20 --kline-timeout 15 --max-retries 1
```

每周或财报季后补财务：

```powershell
D:\qmt-bridge\.venv\Scripts\python.exe D:\qmt-bridge\scripts\download_all.py --sectors 沪深A股 --skip-kline --tables Balance,Income,CashFlow --batch-size 20 --timeout 300 --delay 1.0 --max-retries 2
```

## 异常处理

- 失败率高于 3%：停止提速，重跑同一阶段；若仍失败，将 `--kline-delay` 翻倍。
- 财务批次超时：把 `--batch-size` 从 20 降到 10，`--timeout` 提到 600。
- QMT 连接断开：先确认 miniQMT 仍登录，再重启 qmt-bridge 服务。
- 磁盘低于 80 GiB：继续暂停分钟线，只保留财务和日线增量。

---
name: 量化数据专员
slug: quant-data-specialist
description: 管理 StarBridge Quant 量化数据缓存更新、缓存覆盖率检查、数据清洗、质量核对和必要的公开数据交叉验证。
default_workspace: /mnt/d/starbridge-quant
---

# 量化数据专员

当用户说“调用量化数据专员”“让数据专员检查缓存”“清洗量化数据”“更新 QMT 缓存”时，按本文件执行。

## 职责

1. 管理 QMT 本地缓存更新，包括日线、分钟线、财务三表和元数据。
2. 检查缓存覆盖率、缺失项、旧数据、异常最新日期、重复证券、退市或新股导致的非阻塞缺口。
3. 生成研究可用的清洗清单，例如可用于价格因子的股票池、可用于财务因子的股票池、需要剔除或人工复核的股票。
4. 必要时使用公开数据源交叉核对关键样本，核对项包括证券代码、名称、上市日期、交易日、行情日期和财报公告日期。
5. 输出简洁的质量报告，明确哪些问题会阻塞回测，哪些只影响部分因子。

## 环境规则

- 工作目录固定为 `/mnt/d/starbridge-quant`。
- QMT 服务运行在 Windows，WSL 只作为研究和调用环境。
- API 默认地址是 `http://127.0.0.1:18888`，先运行 `scripts/check-starbridge-quant-health.sh`。
- 直接访问 `xtquant.xtdata` 的脚本必须使用 Windows Python：`D:\starbridge-quant\.venv\Scripts\python.exe`。
- 不在 WSL 原生 Python 中导入 `xtquant`。
- 不打印 `.env` 中的 API key、账号或其他敏感信息。
- 不执行交易、撤单、转账、资金操作。
- 大范围下载前先检查是否已有 `download_all.py` 进程，避免并发写同一套 xtdata 缓存。

## 默认检查

```bash
scripts/check-starbridge-quant-health.sh
```

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match "python" -and $_.CommandLine -like "*download_all.py*" } |
  Select-Object ProcessId,Name,CommandLine
```

```cmd
D:\starbridge-quant\.venv\Scripts\python.exe D:\starbridge-quant\scripts\check_cache_progress.py --periods 1d --year-from 2019 --tables Balance,Income,CashFlow
```

## 下载策略

- 默认先补 `1d` 日线和财务数据。
- 不默认补 `1m` 或 `5m` 的多年全量数据；分钟线必须按年份、市场、容量和磁盘预算分批规划。
- 默认使用年度分段模式，跳过已有缓存：

```cmd
D:\starbridge-quant\.venv\Scripts\python.exe D:\starbridge-quant\scripts\download_all.py --sectors 沪深A股 --periods 1d --since 2019 --tables Balance,Income,CashFlow --batch-size 20 --timeout 300 --delay 1.0 --kline-delay 0.30 --kline-timeout 15 --max-retries 2
```

## 刷新审计流程

对会改变研究口径的刷新任务，默认附带一轮“刷新前快照 -> 刷新后快照 -> diff 审计”。

使用 WSL 原生研究环境执行快照和 diff：

```bash
$HOME/.venvs/starbridge-quant/bin/python /mnt/d/starbridge-quant/scripts/write_research_snapshot.py --symbols-file /mnt/d/starbridge-quant/data/yuanqi_replica/basic/quant_backtest_prefilter.csv --symbol-column stock_code --snapshot-name pre_refresh_audit --start-date 20190101 --end-date 20260423
```

刷新完成后再次生成快照：

```bash
$HOME/.venvs/starbridge-quant/bin/python /mnt/d/starbridge-quant/scripts/write_research_snapshot.py --symbols-file /mnt/d/starbridge-quant/data/yuanqi_replica/basic/quant_backtest_prefilter.csv --symbol-column stock_code --snapshot-name post_refresh_audit --start-date 20190101 --end-date 20260423
```

然后生成 diff 审计产物：

```bash
$HOME/.venvs/starbridge-quant/bin/python /mnt/d/starbridge-quant/scripts/write_snapshot_diff_report.py --left-snapshot <pre_snapshot_dir> --right-snapshot <post_snapshot_dir> --diff-name refresh_audit --instrument-fields name,list_date,delist_date,exchange
```

执行口径：

- 定向刷新：优先对本次刷新涉及的股票清单做前后快照和 diff。
- 分批刷新：每个批次至少保留一份批次级 diff 审计。
- 全量刷新：默认基于 `quant_backtest_prefilter.csv` 或最新研究股票池生成审计快照。
- `instrument` diff 默认只比较稳定字段 `name/list_date/delist_date/exchange`，避免 `ExtendInfo` 这类噪声字段淹没结果。

审计产物默认保存在：

- `research/output/snapshots/`
- `research/output/snapshot_diffs/`

每次刷新结束后，报告里要附上：

- 前后快照目录
- diff artifact 目录
- `manifest.json`
- `top_changes.md`

## 清洗输出

优先维护这些项目内文件：

- `data/yuanqi_replica/basic/cache_progress_summary.json`
- `data/yuanqi_replica/basic/cache_progress_kline.csv`
- `data/yuanqi_replica/basic/cache_progress_kline_issues.csv`
- `data/yuanqi_replica/basic/cache_progress_financial.csv`
- `data/yuanqi_replica/basic/cache_progress_financial_issues.csv`
- `data/yuanqi_replica/basic/quant_data_clean_universe.csv`
- `data/yuanqi_replica/basic/quant_data_quality_report.md`

`quant_data_clean_universe.csv` 至少包含：

- `stock_code`
- `name`
- `open_date`
- `has_1d_cache`
- `latest_1d_date`
- `financial_status`
- `include_price_factors`
- `include_financial_factors`
- `data_issue_type`
- `factor_policy`

## 判断口径

- `include_price_factors=1`：有日线缓存，且最新日线日期与缓存摘要一致。
- `include_financial_factors=1`：财务三表均不缺失，记录数达到最小要求，且不属于需要人工复核的缺口。
- 新上市股票缺财务或财务记录少，通常不阻塞价格因子，只在财务因子中剔除。
- `stale_financial` 不直接判定为下载失败；先确认 QMT 数据源公告日期口径，再决定是否只排除最近一期财务因子。
- 对回测有阻塞意义的问题要单独列出，例如日线缺失、代码不在基础股票池、上市日期异常、财务三表全部缺失且非新股。

## 公开数据交叉验证

只有在本地缓存出现冲突或用户明确要求时才查公开数据。使用外部数据时要记录来源、查询时间和被核对字段，不把公开源临时结果直接覆盖本地缓存。

## 工作汇报

每次完成后报告：

- 缓存更新时间和检查时间。
- 日线覆盖率、年度覆盖率、缺失数。
- 财务 `ok/stale/missing/incomplete` 数量。
- 清洗后价格因子可用股票数、财务因子可用股票数。
- 需要人工复核的问题样本和输出文件路径。
- 如执行了刷新审计，补充 `changed_dataset_count`、`row_change_rows`、`field_change_rows` 和 `top_changes.md` 路径。

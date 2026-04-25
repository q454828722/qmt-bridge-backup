# Research Lib

这里放研究侧公共工具，目标是给 notebook / 因子 / 策略复用。

适合放进来的内容：

- 数据读取适配层
- 时间与交易日工具
- 横截面清洗工具
- 回测结果汇总工具

不建议放进来的内容：

- Windows 侧进程控制
- QMT 服务启动脚本
- 缓存下载调度脚本

研究侧工具尽量保持“只读数据、少副作用”。

当前已经提供统一访问骨架：

- `research_database_client.py`
  - `ResearchDatabaseClient`：SQLite 研究库只读客户端，供三位因子专员并行读取。
  - `load_price_panel()`：读取 `factor_price_cache_daily`，不存在时回退到 `v_factor_ready_daily_effective`。
  - `load_financial_panel()`：读取财务三表，可按 `fresh/latest` 财务股票池过滤。
  - `load_style_panel()`：读取风格状态面板，并可合并 GICS4 行业映射。
  - `check_open_conflicts()`：读取未解决多源冲突，因子研究前必须检查。
- `research_client.py`
  - `ResearchClient`：统一入口
  - `QMTResearchSource`：主源读取
  - `TushareResearchSource`：公开基础信息与旁路校验
  - `AkshareResearchSource`：公开行情校验
  - `GmResearchSource`：通过 Windows Python 子进程调用掘金 SDK，作为只读备用源
  - `write_snapshot()` / `load_snapshot()`：研究快照固化与回读
  - `diff_snapshots()`：比较两次快照之间哪些股票 / 日期 / 字段发生变化
  - `write_diff_report()`：把 diff 结果固化成可审计产物

推荐用法：

```python
from research.lib import ResearchClient, ResearchDatabaseClient

client = ResearchClient()

bars = client.get_daily_bars(
    ["000001.SZ", "600519.SH"],
    start_date="20240101",
    end_date="20240430",
)

instrument_report = client.reconcile_instrument_basics(["000001.SZ", "600519.SH"])
daily_report = client.reconcile_daily_bars(
    ["000001.SZ"],
    start_date="20260401",
    end_date="20260423",
)

snapshot = client.write_snapshot(
    ["000001.SZ", "600519.SH"],
    snapshot_name="value_factor_base",
    start_date="20240101",
    end_date="20240430",
)

loaded = client.load_snapshot(snapshot.snapshot_dir)
bars = loaded.daily_bars.data

with ResearchDatabaseClient("data/research/starbridge_quant_research.sqlite") as db:
    status = db.database_status()
    conflicts = db.check_open_conflicts(symbols=["000001.SZ", "600519.SH"])
    price_panel = db.load_price_panel(
        symbols=["000001.SZ", "600519.SH"],
        start_date="20250101",
        columns=["symbol", "trade_date", "close", "return_20d", "ma_20", "return_1d_vol_20"],
    )
    financial_panel = db.load_financial_panel(
        symbols="000001.SZ",
        statement="income",
        universe="latest",
    )
    style_panel = db.load_style_panel(symbols="000001.SZ", start_date="20250101")

diff = client.diff_snapshots(
    "research/output/snapshots/20260424_102343_financial_smoke_test",
    "research/output/snapshots/20260424_102343_financial_smoke_test",
)

summary = diff.dataset_summary
row_changes = diff.row_changes
field_changes = diff.field_changes

stable_instrument_diff = client.diff_snapshots(
    left_snapshot,
    right_snapshot,
    instrument_fields=["name", "list_date", "delist_date", "exchange"],
)

artifact = client.write_diff_report(
    left_snapshot,
    right_snapshot,
    diff_name="daily_audit",
    instrument_fields=["name", "list_date", "delist_date", "exchange"],
)

artifact_dir = artifact.diff_dir
artifact_manifest = artifact.manifest_path
```

默认策略：

- `daily_bar`：`QMT` 主源，`AkShare`、`GM` 旁路校验/兜底
- `instrument`：`QMT` 主源，`Tushare`、`GM` 旁路校验/兜底
- `financial`：`QMT` 主源，`GM` 只在显式 `allow_fallback=True` 或 `source=SourceName.GM` 时做备用验证

备注：

- `Tushare` 更适合低频基础信息核验，不适合在 notebook 中高频反复查询
- `TushareResearchSource` 已带内存缓存和本地 CSV 缓存，首次成功拉取后会尽量复用缓存
- `GM` 默认读取 `C:\Users\lianghua\.goldminer3\storage.json` 的本机 tokenMemo，或使用 `GM_TOKEN`；任何脚本都不打印 token
- `GM` 默认 Windows Python 为 `C:\Users\lianghua\python-sdk\python3.11.11\python.exe`，可通过 `STARBRIDGE_GM_WINDOWS_PYTHON` 覆盖
- SQLite 研究库默认位于 `data/research/starbridge_quant_research.sqlite`
- 三因子并行研究默认读取只读连接，优先使用 `factor_price_cache_daily`
- `factor_price_cache_daily` 可通过 `just build-factor-cache` 从 `v_factor_ready_daily_effective` 重建
- 研究快照默认写到 `research/output/snapshots/<timestamp>_<name>/`
- `daily_bar` / `instrument` / `financial/*.parquet` 会和 `manifest.json` 一起落盘
- `diff_snapshots()` 会输出三张表：`dataset_summary`、`row_changes`、`field_changes`
- `diff_snapshots()` 支持 `daily_fields`、`instrument_fields`、`financial_fields`，可以把对比限制在更稳定的字段上
- `write_diff_report()` 默认写到 `research/output/snapshot_diffs/<timestamp>_<name>/`
- diff artifact 会包含 `dataset_summary`、`row_changes`、`field_changes` 和 `manifest.json`

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

- `research_client.py`
  - `ResearchClient`：统一入口
  - `QMTResearchSource`：主源读取
  - `TushareResearchSource`：公开基础信息与旁路校验
  - `AkshareResearchSource`：公开行情校验
  - `write_snapshot()` / `load_snapshot()`：研究快照固化与回读
  - `diff_snapshots()`：比较两次快照之间哪些股票 / 日期 / 字段发生变化
  - `write_diff_report()`：把 diff 结果固化成可审计产物

推荐用法：

```python
from research.lib import ResearchClient

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

- `daily_bar`：`QMT` 主源，`AkShare` 旁路校验/兜底
- `instrument`：`QMT` 主源，`Tushare` 旁路校验/兜底
- `financial`：当前只认 `QMT` 主源，不做静默公开源替换

备注：

- `Tushare` 更适合低频基础信息核验，不适合在 notebook 中高频反复查询
- `TushareResearchSource` 已带内存缓存和本地 CSV 缓存，首次成功拉取后会尽量复用缓存
- 研究快照默认写到 `research/output/snapshots/<timestamp>_<name>/`
- `daily_bar` / `instrument` / `financial/*.parquet` 会和 `manifest.json` 一起落盘
- `diff_snapshots()` 会输出三张表：`dataset_summary`、`row_changes`、`field_changes`
- `diff_snapshots()` 支持 `daily_fields`、`instrument_fields`、`financial_fields`，可以把对比限制在更稳定的字段上
- `write_diff_report()` 默认写到 `research/output/snapshot_diffs/<timestamp>_<name>/`
- diff artifact 会包含 `dataset_summary`、`row_changes`、`field_changes` 和 `manifest.json`

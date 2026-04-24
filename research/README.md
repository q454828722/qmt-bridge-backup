# Research Workspace

这个目录只服务于 WSL 原生环境下的量化研究与策略开发，不承载 Windows 侧的 QMT 运行职责。

推荐分工：

- Windows：QMT 客户端、`qmt-server`、缓存下载与缓存清洗
- WSL：因子研究、策略原型、回测、分析 notebook

目录约定：

- `notebooks/`：研究 notebook，按主题或策略拆分
- `factors/`：因子定义、信号生成、截面处理代码
- `strategies/`：策略原型、组合构建、回测入口
- `lib/`：研究侧公共工具，不放服务端逻辑

建议的研究数据入口：

1. `QMT Bridge API`
   - 适合读取本地缓存、历史行情、财务数据、板块、交易日历
2. `Tushare`
   - 适合补充公开基础信息、做抽样核对、补研究期内公开口径数据
3. `AkShare`
   - 适合作为公开数据辅助源，不直接当成交口径主源

数据一致性建议：

- 行情与财务的主口径优先使用 QMT 本地缓存
- Tushare / AkShare 只做补充字段、公开校验、缺失兜底
- 因子回测前先把数据落成一份研究时点快照，避免 notebook 每次运行动态混入不同来源
- 每张研究表保留 `source`、`asof_date`、`fetch_time`、`version` 字段，方便复盘

推荐在 WSL 中使用：

```bash
source /home/lianghua/.venvs/qmt-bridge-quant/bin/activate
jupyter lab
```

Jupyter kernel 请选择 `QMT Bridge Quant (WSL Native)`。

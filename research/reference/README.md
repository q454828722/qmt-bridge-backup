# Research Reference Files

这个目录用于保存可跟踪、可复用、适合随仓库备份的研究参考文件。

典型内容：

- 行业映射
- 股票池映射
- 研究基准配置
- 研究口径说明

与 `research/output/` 的区别：

- `reference/`：稳定参考文件，适合进入 Git
- `output/`：运行产物、临时快照、回测输出，默认不进 Git

当前已支持的参考文件：

- `qmt_gics4_industry_map.csv`
- `qmt_gics4_industry_map_summary.json`

这两份文件可通过下面的脚本刷新：

```bash
python scripts/cache_qmt_gics4_industry_map.py
```

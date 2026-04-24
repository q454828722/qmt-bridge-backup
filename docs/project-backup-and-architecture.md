# Project Backup And Architecture

本文档说明这份仓库在公开备份时实际包含什么，以及 Windows / WSL 两侧各自负责什么。

## 架构分工

### Windows 侧

负责运行和维护：

- miniQMT 客户端
- `starbridge-server`
- QMT 本地缓存下载
- QMT 原始缓存检查和清洗脚本
- Windows 启动脚本、计划任务、转发配套脚本

### WSL 侧

负责研究和开发：

- 因子研究
- 策略原型
- 回测与分析
- Jupyter / notebook 工作流
- 研究快照、diff 审计、研究侧工具库

## 仓库中会备份的内容

这份仓库适合备份和同步的内容包括：

- 服务端代码
- 客户端代码
- Windows 启动 / 检查 / 运维脚本
- WSL 研究工具、研究模板、研究文档
- 子代理定义
- 可跟踪的研究参考文件，例如行业映射缓存

## 不会进入 Git 的内容

以下内容默认不进入仓库：

- `.env`
- API Key、账号、密码
- 虚拟环境目录，例如 `.venv/`、`$HOME/.venvs/...`
- `data/` 下的大体量原始缓存
- `logs/`
- `research/output/` 下的运行产物

这样做的原因：

- 避免泄露敏感信息
- 避免把券商侧或本地运行态文件直接公开
- 避免 Git 仓库被大体量缓存拖慢

## 当前推荐工作模式

1. Windows 负责 QMT 服务与缓存管理
2. WSL 负责因子、策略、回测研究
3. 研究默认优先使用 QMT 主源
4. 公开源只做旁路校验和补充
5. 正式研究尽量先写快照，再做因子计算和差异审计

## 行业中性推荐口径

当前项目已经支持：

- 从 QMT `GICS4` 板块生成本地行业映射缓存
- 研究模板优先使用本地缓存做行业中性

参考文件：

- `research/reference/qmt_gics4_industry_map.csv`
- `research/reference/qmt_gics4_industry_map_summary.json`

刷新命令：

```bash
python scripts/cache_qmt_gics4_industry_map.py
```

## 推荐阅读顺序

1. `README.md`
2. `docs/windows-wsl-operations.md`
3. `docs/project-backup-and-architecture.md`
4. `research/README.md`
5. `docs/local-strategy-api-guide.md`

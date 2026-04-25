# StarBridge Quant — 项目快捷命令
# 使用: just <命令>  |  just --list 查看所有命令

# Windows 下使用 PowerShell 作为默认 shell
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# 默认命令：列出所有可用命令
default:
    @just --list

# ─────────────────────────── 安装 ───────────────────────────

# 安装项目（仅客户端，零依赖）
install:
    pip install -e .

# 安装服务端全部依赖
install-server:
    pip install -e ".[full]"

# 安装文档依赖
install-docs:
    pip install -e ".[docs]"

# 安装仪表盘依赖
install-dashboard:
    pip install -e ".[dashboard]"

# 安装全部依赖（服务端 + 文档 + 仪表盘）
install-all:
    pip install -e ".[full,docs,dashboard,test]"

# ─────────────────────────── 服务 ───────────────────────────

# 启动 API 服务（前台，Ctrl+C 停止）
serve *ARGS:
    starbridge-server --port 18888 {{ARGS}}

# 启动 API 服务（指定端口）
serve-port port="18888":
    starbridge-server --port {{port}}

# 启动 API 服务（调试模式）
serve-debug:
    starbridge-server --port 18888 --log-level debug

# 启动定时下载调度器（独立进程，与 serve 分开运行）
scheduler *ARGS:
    starbridge-scheduler {{ARGS}}

# 启动定时下载调度器（调试模式）
scheduler-debug:
    starbridge-scheduler --log-level debug

# 停止 API 服务（查找并终止占用 18888 端口的进程）
serve-stop:
    @echo "正在查找 starbridge-server 进程..."
    Get-NetTCPConnection -LocalPort 18888 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; if ($?) { echo "✅ starbridge-server 已停止" } else { echo "⚠️ 未找到运行中的 starbridge-server" }

# ─────────────────────────── 数据下载 ─────────────────────────

# 下载 A 股历史行情 + 财务数据（逐股精准增量，首次自动全量）
download-all *ARGS:
    python scripts/download_all.py {{ARGS}}

# 仅下载 1m K 线数据（跳过财务数据）
download-1m *ARGS:
    python scripts/download_all.py --periods 1m --skip-financial {{ARGS}}

# 下载最近两年的 1m K 线数据（快速启动算法开发）
download-1m-recent *ARGS:
    python scripts/download_all.py --periods 1m --skip-financial --since 2025 {{ARGS}}

# 仅下载 5m K 线数据（跳过财务数据）
download-5m *ARGS:
    python scripts/download_all.py --periods 5m --skip-financial {{ARGS}}

# 下载最近两年的 5m K 线数据（快速启动算法开发）
download-5m-recent *ARGS:
    python scripts/download_all.py --periods 5m --skip-financial --since 2025 {{ARGS}}

# ─────────────────────────── 仪表盘 ─────────────────────────

# 启动可视化仪表盘（http://localhost:8501）
dashboard:
    streamlit run dashboard/app.py

# ─────────────────────────── 研究代理 ───────────────────────

# 输出 A 股量化代理矩阵（支持 --format json/mermaid）
agent-matrix *ARGS:
    python scripts/describe_quant_agent_matrix.py {{ARGS}}

# 使用 GM Windows SDK 对清洗失败清单做只读备用源验证
gm-fallback-validation *ARGS:
    python scripts/run_gm_fallback_validation.py {{ARGS}}

# 应用多源验证后的 QMT 派生缓存修复，并生成 QMT 原生财务刷新清单
apply-qmt-cache-repairs *ARGS:
    python scripts/apply_verified_qmt_cache_repairs.py {{ARGS}}

# 从最新清洗数据生成批处理安全的全量研究快照
clean-full-snapshot *ARGS:
    python scripts/write_clean_full_snapshot.py {{ARGS}}

# 将清洗快照构建成研究侧 SQLite 数据库
build-research-db *ARGS:
    python scripts/build_research_database.py {{ARGS}}

# 增量维护研究侧 SQLite 数据库（证据、冲突、修复、覆盖层）
maintain-research-db *ARGS:
    python scripts/maintain_research_database.py {{ARGS}}

# 构建三因子并行研究使用的日线派生因子缓存
build-factor-cache *ARGS:
    python scripts/build_factor_cache.py {{ARGS}}

# ─────────────────────────── 文档 ───────────────────────────

# 本地预览 MkDocs 文档站点（http://127.0.0.1:8001）
docs:
    mkdocs serve -a 127.0.0.1:8001

# 构建 MkDocs 静态站点到 site/
docs-build:
    mkdocs build -d site/

# pdoc 本地预览客户端 API（http://localhost:8002）
docs-pdoc:
    pdoc src/starbridge_quant/client/ -p 8002

# 一键构建 MkDocs + pdoc
docs-all:
    @echo "==> 构建 MkDocs 文档..."
    mkdocs build -d site/
    @echo "==> 构建 pdoc API 参考..."
    pdoc -o site/pdoc src/starbridge_quant/client/
    @echo "==> 完成！"
    @echo "    MkDocs: site/index.html"
    @echo "    pdoc:   site/pdoc/index.html"

# 清理文档构建产物
docs-clean:
    rm -rf site/

# ─────────────────────────── 测试 ───────────────────────────

# 运行测试
test *ARGS:
    python scripts/run_pytest.py tests/ {{ARGS}}

# 运行测试（verbose）
test-v:
    python scripts/run_pytest.py tests/ -v

# ─────────────────────────── 代码质量 ───────────────────────

# 类型检查（需要 mypy）
typecheck:
    python -m mypy src/starbridge_quant/

# 格式化代码（需要 ruff）
fmt:
    python -m ruff format src/ tests/

# 代码检查（需要 ruff）
lint:
    python -m ruff check src/ tests/

# 格式化 + 检查
check: fmt lint

# ─────────────────────────── 构建 ───────────────────────────

# 构建 wheel 和 sdist
build:
    python -m build

# 发布到 TestPyPI（首次验证用）
publish-test: build
    python -m twine upload --repository testpypi dist/*

# 发布到 PyPI
publish: build
    python -m twine upload dist/*

# 清理构建产物
clean:
    rm -rf dist/ build/ site/ *.egg-info src/*.egg-info
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ─────────────────────────── 信息 ───────────────────────────

# 显示项目版本
version:
    @python -c "from starbridge_quant._version import __version__; print(__version__)"

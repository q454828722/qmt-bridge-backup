"""命令行入口模块 ── ``starbridge-server`` / ``starbridge-scheduler`` 命令。

本模块提供 StarBridge Quant 的命令行启动入口：
- ``starbridge-server``: 启动 HTTP API 服务（不含定时下载调度器）
- ``starbridge-scheduler``: 启动定时数据下载调度器（独立进程）

两个命令分离运行，避免 xtdata C 扩展在同进程内被多线程并发调用导致
BSON 断言崩溃。

典型用法::

    starbridge-server --host 0.0.0.0 --port 8000 --trading --api-key my-secret-key
    starbridge-scheduler                        # 另开终端运行
"""

import argparse
import sys
from pathlib import Path

from .config import Settings, _load_env_file, env_get, reset_settings


def _cli_prog(default: str) -> str:
    """根据实际入口脚本名生成 argparse prog，兼顾旧 CLI 别名。"""
    stem = Path(sys.argv[0]).stem.strip()
    if stem and not stem.lower().startswith("python"):
        return stem
    return default


def main():
    """解析命令行参数，构建配置对象并启动 Uvicorn 服务器。

    启动流程：
    1. 从 .env 文件加载环境变量（已存在的环境变量不会被覆盖）
    2. 解析命令行参数（命令行参数优先级高于环境变量）
    3. 用参数构建 Settings 配置对象并设置为全局单例
    4. 创建 FastAPI 应用并通过 Uvicorn 启动 HTTP 服务

    命令行参数说明：
        --host:           监听地址，默认 0.0.0.0（所有网卡）
        --port:           监听端口，默认 8000
        --log-level:      日志级别（critical/error/warning/info/debug）
        --workers:        工作进程数，Windows 下建议保持 1
        --trading:        启用交易模块（需要 miniQMT 客户端运行）
        --api-key:        API 密钥，用于保护交易等敏感接口
        --mini-qmt-path:  miniQMT 安装目录路径（启用交易时必须指定）
        --account-id:     交易资金账号
    """
    # 优先从 .env 文件加载环境变量，使得后续参数默认值可以读取到 .env 中的配置
    _load_env_file()

    parser = argparse.ArgumentParser(
        prog=_cli_prog("starbridge-server"),
        description="StarBridge Quant API Server",
    )
    parser.add_argument(
        "--host",
        default=env_get("STARBRIDGE_HOST", "QMT_BRIDGE_HOST", default="0.0.0.0"),
        help="Listen host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env_get("STARBRIDGE_PORT", "QMT_BRIDGE_PORT", default="8000")),
        help="Listen port (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default=env_get("STARBRIDGE_LOG_LEVEL", "QMT_BRIDGE_LOG_LEVEL", default="info"),
        choices=["critical", "error", "warning", "info", "debug"],
        help="Uvicorn log level (default: info)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(env_get("STARBRIDGE_WORKERS", "QMT_BRIDGE_WORKERS", default="1")),
        help="Number of workers (default: 1, keep 1 on Windows)",
    )
    parser.add_argument(
        "--trading",
        action="store_true",
        default=env_get("STARBRIDGE_TRADING_ENABLED", "QMT_BRIDGE_TRADING_ENABLED").lower()
        in ("1", "true", "yes"),
        help="Enable trading module",
    )
    parser.add_argument(
        "--api-key",
        default=env_get("STARBRIDGE_API_KEY", "QMT_BRIDGE_API_KEY"),
        help="API key for authenticated endpoints",
    )
    parser.add_argument(
        "--mini-qmt-path",
        default=env_get("STARBRIDGE_MINI_QMT_PATH", "QMT_BRIDGE_MINI_QMT_PATH"),
        help="Path to miniQMT installation (for trading)",
    )
    parser.add_argument(
        "--account-id",
        default=env_get("STARBRIDGE_TRADING_ACCOUNT_ID", "QMT_BRIDGE_TRADING_ACCOUNT_ID"),
        help="Trading account ID",
    )

    args = parser.parse_args()

    # 用命令行参数构建 Settings 对象（覆盖环境变量中的默认值）
    settings = Settings(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=args.workers,
        api_key=args.api_key,
        trading_enabled=args.trading,
        mini_qmt_path=args.mini_qmt_path,
        trading_account_id=args.account_id,
    )
    # 将配置对象设置为全局单例，供后续模块通过 get_settings() 获取
    reset_settings(settings)

    # 为应用自身的 logger 配置 handler，使 starbridge_quant.* 的日志能输出到控制台。
    # Uvicorn 只配置 uvicorn.* 系列 logger，不会影响应用自定义的 logger。
    import logging

    app_logger = logging.getLogger("starbridge_quant")
    app_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
        app_logger.addHandler(handler)

    import uvicorn

    from .app import create_app

    # 创建 FastAPI 应用实例
    app = create_app(settings)
    # 启动 Uvicorn ASGI 服务器
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        workers=settings.workers,
        timeout_graceful_shutdown=3,
    )


def scheduler_main():
    """启动定时数据下载调度器（独立进程）。

    从 .env / 环境变量读取调度配置（scheduler_kline_*, scheduler_financial_*），
    在独立进程中运行，不与 API 服务共享线程池，
    从根本上避免 xtdata C 扩展的并发调用问题。

    用法::

        starbridge-scheduler                    # 使用 .env 默认配置
        starbridge-scheduler --log-level debug  # 调试模式
    """
    _load_env_file()

    parser = argparse.ArgumentParser(
        prog=_cli_prog("starbridge-scheduler"),
        description="StarBridge Quant 定时数据下载调度器（独立进程）",
    )
    parser.add_argument(
        "--log-level",
        default=env_get("STARBRIDGE_LOG_LEVEL", "QMT_BRIDGE_LOG_LEVEL", default="info"),
        choices=["critical", "error", "warning", "info", "debug"],
        help="日志级别 (default: info)",
    )
    args = parser.parse_args()

    import asyncio
    import logging

    app_logger = logging.getLogger("starbridge_quant")
    app_logger.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
        app_logger.addHandler(handler)

    settings = Settings.from_env()

    from .downloader import DownloadSchedulerState
    from .scheduler import scheduler_loop

    state = DownloadSchedulerState()

    app_logger.info(
        "调度器独立进程启动 (K线=%s 周期=%s, 财务=%s)",
        settings.scheduler_kline_enabled,
        settings.scheduler_kline_periods,
        settings.scheduler_financial_enabled,
    )

    try:
        asyncio.run(scheduler_loop(state, settings))
    except KeyboardInterrupt:
        app_logger.info("调度器已停止")


if __name__ == "__main__":
    main()

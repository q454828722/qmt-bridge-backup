"""迁移后的轻量烟测，重点覆盖兼容入口和仓库元数据一致性。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import qmt_client
import starbridge_client
from starbridge_quant import QMTClient, __version__
from starbridge_quant.client_factory import get_starbridge_config, make_starbridge_client


ROOT = Path(__file__).resolve().parents[1]


def test_public_imports_and_legacy_wrappers_still_work() -> None:
    """公开导入路径和兼容 wrapper 应保持可用。"""
    client = QMTClient(host="127.0.0.1")

    assert __version__
    assert client.base_url == "http://127.0.0.1:8000"
    assert client.ws_url == "ws://127.0.0.1:8000"
    assert qmt_client.make_qmt_client is starbridge_client.make_qmt_client
    assert qmt_client.get_qmt_config is starbridge_client.get_qmt_config
    assert importlib.util.find_spec("qmt_bridge") is None


def test_client_factory_prefers_starbridge_env_and_keeps_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WSL 研究侧默认端口应沿用 18888，并优先读取新变量名。"""
    for key in (
        "STARBRIDGE_CLIENT_HOST",
        "QMT_BRIDGE_CLIENT_HOST",
        "STARBRIDGE_HOST",
        "QMT_BRIDGE_HOST",
        "STARBRIDGE_PORT",
        "QMT_BRIDGE_PORT",
        "STARBRIDGE_API_KEY",
        "QMT_BRIDGE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    default_cfg = get_starbridge_config()
    assert default_cfg.host == "127.0.0.1"
    assert default_cfg.port == 18888
    assert default_cfg.api_key == ""

    monkeypatch.setenv("QMT_BRIDGE_CLIENT_HOST", "192.168.0.9")
    monkeypatch.setenv("QMT_BRIDGE_PORT", "19999")
    legacy_cfg = get_starbridge_config()
    assert legacy_cfg.host == "192.168.0.9"
    assert legacy_cfg.port == 19999

    monkeypatch.setenv("STARBRIDGE_CLIENT_HOST", "127.0.0.2")
    monkeypatch.setenv("STARBRIDGE_PORT", "28888")
    monkeypatch.setenv("STARBRIDGE_API_KEY", "new-key")
    monkeypatch.setenv("QMT_BRIDGE_API_KEY", "old-key")

    preferred_cfg = get_starbridge_config(with_trading=True)
    assert preferred_cfg.host == "127.0.0.2"
    assert preferred_cfg.port == 28888
    assert preferred_cfg.api_key == "new-key"

    client = make_starbridge_client(with_trading=True)
    assert client.base_url == "http://127.0.0.2:28888"
    assert client.api_key == "new-key"


def test_repo_metadata_uses_current_origin_and_new_entrypoints() -> None:
    """打包元数据和公开文档不应再指向旧备份仓库。"""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "starbridge-quant"' in pyproject
    assert 'Homepage = "https://github.com/q454828722/starbridge-quant"' in pyproject
    assert 'Repository = "https://github.com/q454828722/starbridge-quant"' in pyproject
    assert 'starbridge-server = "starbridge_quant.server.cli:main"' in pyproject
    assert 'qmt-server = "starbridge_quant.server.cli:main"' in pyproject
    assert 'packages = ["src/starbridge_quant"]' in pyproject
    assert '"starbridge_client.py" = "starbridge_client.py"' in pyproject
    assert '"qmt_client.py" = "qmt_client.py"' in pyproject
    assert "qmt-bridge-backup" not in pyproject

    for relative_path in ("README.md", "docs/index.md", "docs/getting-started.md", "mkdocs.yml"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "qmt-bridge-backup" not in content, relative_path


def test_repo_local_defaults_stay_aligned_to_18888() -> None:
    """仓库自带的运维入口应继续围绕 18888 工作流。"""
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "STARBRIDGE_PORT=18888" in env_example
    assert "starbridge-server --port 18888" in justfile
    assert "http://<Windows局域网IP>:18888/docs" in readme

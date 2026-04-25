#!/usr/bin/env python
"""以项目兼容方式运行 pytest。"""

from __future__ import annotations

import sys


def _remove_qmt_runtime_site_packages() -> None:
    """避免 Windows pytest 误导入 QMT 自带的旧 pyreadline。"""

    if sys.platform != "win32":
        return

    cleaned: list[str] = []
    for path in sys.path:
        normalized = path.replace("\\", "/").lower()
        if "bin.x64/lib/site-packages" in normalized:
            continue
        cleaned.append(path)
    sys.path[:] = cleaned


def _default_args(argv: list[str]) -> list[str]:
    """补充在 WSL 挂载盘上更稳的默认参数。"""

    has_capture_option = any(
        arg == "-s" or arg.startswith("--capture") for arg in argv
    )
    if has_capture_option:
        return argv
    return ["-s", *argv]


def main(argv: list[str] | None = None) -> int:
    _remove_qmt_runtime_site_packages()

    try:
        import pytest
    except ModuleNotFoundError:
        print(
            "pytest 未安装。请先运行：pip install -e \".[test]\"",
            file=sys.stderr,
        )
        return 2

    args = _default_args(list(argv if argv is not None else sys.argv[1:]))
    return int(pytest.main(args))


if __name__ == "__main__":
    raise SystemExit(main())

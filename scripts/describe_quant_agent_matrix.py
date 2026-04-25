#!/usr/bin/env python
"""输出 StarBridge Quant 量化代理矩阵。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.agents import build_default_quant_agent_matrix  # noqa: E402


def _markdown() -> str:
    matrix = build_default_quant_agent_matrix()
    errors = matrix.validate()

    lines = [
        "# StarBridge Quant 量化代理矩阵",
        "",
        f"- 代理数量：{len(matrix.agents)} / {matrix.max_agents}",
        f"- 标的范围：{matrix.universe}",
        f"- 交易频率：{matrix.cadence}",
        f"- 股指期货对冲：{'禁用' if matrix.no_index_futures_hedge else '启用'}",
        f"- 结构校验：{'通过' if not errors else '失败'}",
        "",
        "## 代理",
        "",
        "| ID | 名称 | 层级 | 输出 |",
        "|---|---|---|---|",
    ]

    for agent in matrix.agents:
        lines.append(
            f"| `{agent.agent_id}` | {agent.name} | {agent.layer} | {', '.join(agent.outputs)} |"
        )

    lines.extend(
        [
            "",
            "## 通信链路",
            "",
            "| 来源 | 目标 | 主题 | 载荷 |",
            "|---|---|---|---|",
        ]
    )
    for link in matrix.links:
        required = "" if link.required else "（可选）"
        lines.append(
            f"| `{link.source}` | `{link.target}` | `{link.topic}` | {link.payload}{required} |"
        )

    if errors:
        lines.extend(["", "## 校验错误", ""])
        lines.extend(f"- {error}" for error in errors)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Describe the StarBridge Quant agent matrix.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "mermaid"),
        default="markdown",
        help="输出格式。",
    )
    args = parser.parse_args(argv)

    matrix = build_default_quant_agent_matrix()
    if args.format == "json":
        print(json.dumps(matrix.to_dict(), ensure_ascii=False, indent=2))
    elif args.format == "mermaid":
        print(matrix.to_mermaid())
    else:
        print(_markdown())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Codex 子代理定义与量化矩阵的一致性检查。"""

from __future__ import annotations

from pathlib import Path

from research.agents import build_default_quant_agent_matrix


ROOT = Path(__file__).resolve().parents[1]
SUBAGENT_DIR = ROOT / ".codex" / "subagents"

EXPECTED_SUBAGENTS = {
    "supervisor": "quant-matrix-supervisor",
    "data_steward": "quant-data-specialist",
    "alpha_analyst": "quant-factor-specialist",
    "alpha_fundamental_analyst": "quant-factor-fundamental-specialist",
    "alpha_style_analyst": "quant-factor-style-specialist",
    "strategy_debate_judge": "quant-strategy-debate-judge",
    "portfolio_risk": "quant-portfolio-risk-specialist",
    "compliance_gatekeeper": "quant-compliance-gatekeeper",
    "execution_attribution": "quant-execution-attribution-specialist",
}


def _read_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def test_codex_subagents_cover_quant_agent_matrix() -> None:
    """9 个矩阵节点都应有可调用的 Codex subagent 定义。"""

    matrix = build_default_quant_agent_matrix()
    assert set(matrix.agent_ids) == set(EXPECTED_SUBAGENTS)

    files = sorted(SUBAGENT_DIR.glob("*.md"))
    slugs = {_read_frontmatter(path)["slug"] for path in files}

    assert len(files) == 9
    assert slugs == set(EXPECTED_SUBAGENTS.values())


def test_codex_subagents_keep_matrix_agent_ids_and_topics() -> None:
    """subagent 文档应显式标明矩阵 ID 和对应通信主题。"""

    matrix = build_default_quant_agent_matrix()

    for agent_id, slug in EXPECTED_SUBAGENTS.items():
        path = SUBAGENT_DIR / f"{slug}.md"
        content = path.read_text(encoding="utf-8")
        agent = matrix.get_agent(agent_id)

        assert f"`matrix_agent_id`: `{agent_id}`" in content
        assert agent.name[:2] in content or agent.layer in content

        for topic in agent.consumes_topics:
            assert f"`{topic}`" in content, (slug, topic)
        for topic in agent.publishes_topics:
            assert f"`{topic}`" in content, (slug, topic)


def test_existing_data_and_factor_specialists_are_reused() -> None:
    """已有量化数据/因子专员应复用到新矩阵，而不是被替换成新名字。"""

    data_meta = _read_frontmatter(SUBAGENT_DIR / "quant-data-specialist.md")
    factor_meta = _read_frontmatter(SUBAGENT_DIR / "quant-factor-specialist.md")

    assert data_meta["name"] == "量化数据专员"
    assert data_meta["slug"] == "quant-data-specialist"
    assert factor_meta["name"] == "量化因子专员"
    assert factor_meta["slug"] == "quant-factor-specialist"

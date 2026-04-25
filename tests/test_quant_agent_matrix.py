"""量化代理矩阵的结构与通信烟测。"""

from __future__ import annotations

import pytest

from research.agents import AgentBlackboard, build_default_quant_agent_matrix


def test_default_quant_agent_matrix_is_capped_and_valid() -> None:
    """默认代理矩阵必须保持 9 个以内，并固定为 A 股低频/中频架构。"""

    matrix = build_default_quant_agent_matrix()

    assert len(matrix.agents) <= 9
    assert matrix.universe == "A_SHARE_ONLY"
    assert matrix.no_index_futures_hedge is True
    assert matrix.validate() == ()
    assert "低频" in matrix.cadence


def test_factor_specialists_run_as_three_parallel_lanes() -> None:
    """Alpha 层应拆为三位因子专员，并先汇总到总调度审核。"""

    matrix = build_default_quant_agent_matrix()
    factor_ids = {"alpha_analyst", "alpha_fundamental_analyst", "alpha_style_analyst"}

    assert factor_ids.issubset(set(matrix.agent_ids))
    for agent_id in factor_ids:
        assert matrix.allows_route("data_steward", agent_id, "data.snapshot_manifest")
        assert matrix.allows_route(agent_id, "supervisor", "alpha.research_summary")
        assert not matrix.allows_route(agent_id, "strategy_debate_judge", "alpha.signal_vector")

    assert matrix.allows_route("supervisor", "strategy_debate_judge", "alpha.reviewed_signal_vector")
    assert matrix.allows_route("supervisor", "strategy_debate_judge", "alpha.reviewed_evidence_pack")


def test_all_non_supervisor_agents_are_wired() -> None:
    """除协调器外，每个代理都应至少有输入和输出链路。"""

    matrix = build_default_quant_agent_matrix()

    for agent in matrix.agents:
        if agent.agent_id == "supervisor":
            continue
        assert matrix.links_for(target=agent.agent_id), agent.agent_id
        assert matrix.links_for(source=agent.agent_id), agent.agent_id


def test_declared_links_match_publish_and_consume_topics() -> None:
    """通信链路的主题必须同时出现在来源发布和目标消费清单中。"""

    matrix = build_default_quant_agent_matrix()

    for link in matrix.links:
        source = matrix.get_agent(link.source)
        target = matrix.get_agent(link.target)

        assert link.topic in source.publishes_topics
        assert link.topic in target.consumes_topics


def test_every_declared_topic_has_a_route() -> None:
    """代理声明的发布/消费主题都应有至少一条黑板链路承接。"""

    matrix = build_default_quant_agent_matrix()

    for agent in matrix.agents:
        for topic in agent.publishes_topics:
            assert any(
                link.source == agent.agent_id and link.topic == topic for link in matrix.links
            ), (agent.agent_id, topic)
        for topic in agent.consumes_topics:
            assert any(
                link.target == agent.agent_id and link.topic == topic for link in matrix.links
            ), (agent.agent_id, topic)


def test_compliance_and_risk_rules_disable_index_futures_hedge() -> None:
    """降级后的本地矩阵不能把机构级股指期货对冲带入执行链路。"""

    matrix = build_default_quant_agent_matrix()
    portfolio = matrix.get_agent("portfolio_risk")
    compliance = matrix.get_agent("compliance_gatekeeper")
    joined_rules = "\n".join((*portfolio.hard_rules, *compliance.hard_rules))

    assert "不使用股指期货对冲" in joined_rules
    assert "股指期货、期权、融资融券默认不进入本矩阵执行路径" in joined_rules


def test_blackboard_accepts_declared_routes_and_rejects_shortcuts() -> None:
    """黑板只允许矩阵中声明过的代理链路。"""

    blackboard = AgentBlackboard()
    ok = blackboard.publish(
        source="data_steward",
        target="alpha_analyst",
        topic="data.snapshot_manifest",
        payload={"snapshot_id": "20260424_smoke", "asof_date": "20260424"},
        correlation_id="run-1",
    )

    assert ok.topic == "data.snapshot_manifest"
    assert blackboard.latest(target="alpha_analyst").message_id == ok.message_id

    with pytest.raises(ValueError, match="route not allowed"):
        blackboard.publish(
            source="alpha_analyst",
            target="execution_attribution",
            topic="alpha.signal_vector",
            payload={"stock_code": "000001.SZ"},
        )


def test_blackboard_summary_and_priority_order() -> None:
    """黑板应能按目标代理和优先级读取消息。"""

    blackboard = AgentBlackboard()
    blackboard.publish(
        source="compliance_gatekeeper",
        target="execution_attribution",
        topic="compliance.clearance",
        payload={"batch_id": "normal"},
        priority=1,
    )
    urgent = blackboard.publish(
        source="compliance_gatekeeper",
        target="execution_attribution",
        topic="compliance.block",
        payload={"reason": "risk_limit"},
        priority=9,
    )

    pending = blackboard.pending_for("execution_attribution")
    summary = blackboard.summary()

    assert pending[0].message_id == urgent.message_id
    assert summary["message_count"] == 2
    assert summary["by_target"]["execution_attribution"] == 2

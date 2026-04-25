"""StarBridge Quant 研究侧量化代理矩阵。

这个模块只定义轻量、可审计的代理职责和通信拓扑，不绑定任何 LLM
框架。后续接入 LangGraph、AutoGen 或本地规则引擎时，可以复用这里
的角色边界、主题名称和校验规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentDefinition:
    """单个代理在矩阵中的职责边界。"""

    agent_id: str
    name: str
    layer: str
    mission: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    hard_rules: tuple[str, ...]
    consumes_topics: tuple[str, ...] = field(default_factory=tuple)
    publishes_topics: tuple[str, ...] = field(default_factory=tuple)
    qmt_touchpoints: tuple[str, ...] = field(default_factory=tuple)
    downgrade_notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 友好结构。"""

        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "layer": self.layer,
            "mission": self.mission,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "hard_rules": list(self.hard_rules),
            "consumes_topics": list(self.consumes_topics),
            "publishes_topics": list(self.publishes_topics),
            "qmt_touchpoints": list(self.qmt_touchpoints),
            "downgrade_notes": list(self.downgrade_notes),
        }


@dataclass(frozen=True)
class AgentLink:
    """两个代理之间允许通过黑板传递的一类消息。"""

    source: str
    target: str
    topic: str
    payload: str
    required: bool = True
    guardrails: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 友好结构。"""

        return {
            "source": self.source,
            "target": self.target,
            "topic": self.topic,
            "payload": self.payload,
            "required": self.required,
            "guardrails": list(self.guardrails),
        }


@dataclass(frozen=True)
class AgentMatrix:
    """量化代理矩阵和通信拓扑。"""

    agents: tuple[AgentDefinition, ...]
    links: tuple[AgentLink, ...]
    max_agents: int = 9
    universe: str = "A_SHARE_ONLY"
    no_index_futures_hedge: bool = True
    cadence: str = "低频/中频优先，盘中只做风控、执行和异常响应"

    @property
    def agent_ids(self) -> tuple[str, ...]:
        """返回矩阵中全部代理 ID。"""

        return tuple(agent.agent_id for agent in self.agents)

    @property
    def topic_names(self) -> tuple[str, ...]:
        """返回全部已登记通信主题。"""

        topics = {
            topic
            for agent in self.agents
            for topic in (*agent.consumes_topics, *agent.publishes_topics)
        }
        return tuple(sorted(topics))

    def get_agent(self, agent_id: str) -> AgentDefinition:
        """按 ID 获取代理定义。"""

        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(f"unknown agent_id: {agent_id}")

    def links_for(
        self,
        *,
        source: str | None = None,
        target: str | None = None,
    ) -> tuple[AgentLink, ...]:
        """按来源或目标筛选通信链路。"""

        return tuple(
            link
            for link in self.links
            if (source is None or link.source == source)
            and (target is None or link.target == target)
        )

    def allows_route(self, source: str, target: str, topic: str) -> bool:
        """判断黑板消息是否符合矩阵中声明的通信链路。"""

        return any(
            link.source == source and link.target == target and link.topic == topic
            for link in self.links
        )

    def validate(self) -> tuple[str, ...]:
        """校验矩阵结构，返回错误列表；空列表表示通过。"""

        errors: list[str] = []
        ids = self.agent_ids

        if len(ids) > self.max_agents:
            errors.append(f"agent count {len(ids)} exceeds max_agents {self.max_agents}")
        if len(set(ids)) != len(ids):
            errors.append("agent_id must be unique")
        if "supervisor" not in ids:
            errors.append("matrix must include supervisor")
        if self.universe != "A_SHARE_ONLY":
            errors.append("matrix must stay A_SHARE_ONLY")
        if not self.no_index_futures_hedge:
            errors.append("index futures hedge must stay disabled")

        for agent in self.agents:
            if not agent.mission.strip():
                errors.append(f"{agent.agent_id} mission is empty")
            if not agent.hard_rules:
                errors.append(f"{agent.agent_id} hard_rules is empty")

        id_set = set(ids)
        inbound: dict[str, int] = {agent_id: 0 for agent_id in ids}
        outbound: dict[str, int] = {agent_id: 0 for agent_id in ids}

        for link in self.links:
            if link.source not in id_set:
                errors.append(f"unknown source agent: {link.source}")
                continue
            if link.target not in id_set:
                errors.append(f"unknown target agent: {link.target}")
                continue

            outbound[link.source] += 1
            inbound[link.target] += 1

            source_agent = self.get_agent(link.source)
            target_agent = self.get_agent(link.target)
            if link.topic not in source_agent.publishes_topics:
                errors.append(f"{link.source} does not publish topic {link.topic}")
            if link.topic not in target_agent.consumes_topics:
                errors.append(f"{link.target} does not consume topic {link.topic}")

        for agent_id in ids:
            if agent_id == "supervisor":
                continue
            if inbound[agent_id] == 0:
                errors.append(f"{agent_id} has no inbound communication link")
            if outbound[agent_id] == 0:
                errors.append(f"{agent_id} has no outbound communication link")

        return tuple(errors)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化结构。"""

        return {
            "max_agents": self.max_agents,
            "universe": self.universe,
            "no_index_futures_hedge": self.no_index_futures_hedge,
            "cadence": self.cadence,
            "agents": [agent.to_dict() for agent in self.agents],
            "links": [link.to_dict() for link in self.links],
        }

    def to_mermaid(self) -> str:
        """导出简洁 Mermaid 流程图。"""

        lines = ["flowchart LR"]
        for agent in self.agents:
            lines.append(f'    {agent.agent_id}["{agent.name}"]')
        for link in self.links:
            if link.topic in {"system.task", "system.context", "system.halt"}:
                continue
            if link.target == "supervisor" and not link.topic.startswith("alpha."):
                continue
            if link.source == "supervisor" and not link.topic.startswith("alpha.reviewed"):
                continue
            label = link.topic.replace(".", "/")
            lines.append(f'    {link.source} -->|"{label}"| {link.target}')
        for agent in self.agents:
            if agent.agent_id != "supervisor":
                lines.append(f'    supervisor -. "任务/状态路由" .-> {agent.agent_id}')
        lines.append("    classDef guard fill:#fff5f5,stroke:#c92a2a,color:#7f1d1d")
        lines.append("    class compliance_gatekeeper,portfolio_risk guard")
        return "\n".join(lines)


def build_default_quant_agent_matrix() -> AgentMatrix:
    """构建适配 QMT+A 股环境的默认 9 节点代理矩阵。"""

    agents = (
        AgentDefinition(
            agent_id="supervisor",
            name="总调度专员",
            layer="路由与任务编排",
            mission="维护任务状态、黑板主题和人工介入点，负责把并行因子研究、风控、执行串成可审计流程。",
            inputs=("人工任务", "运行日历", "黑板告警", "代理反馈"),
            outputs=("任务上下文", "运行状态", "暂停/继续指令", "审核后的 Alpha 汇总"),
            hard_rules=(
                "不得越过风控与合规代理直接触发执行。",
                "任何实盘路径必须保留人工可读的审计记录。",
                "三个因子专员并行研究，研究结果必须先汇总到总调度专员审核。",
                "代理总数上限为 9，新增职责优先合并到既有代理。",
            ),
            consumes_topics=(
                "data.quality_alert",
                "alpha.signal_vector",
                "alpha.evidence_pack",
                "alpha.research_summary",
                "strategy.decision",
                "risk.alert",
                "risk.block",
                "compliance.block",
                "execution.fill_report",
                "feedback.agent_score",
            ),
            publishes_topics=(
                "system.task",
                "system.context",
                "system.halt",
                "alpha.reviewed_signal_vector",
                "alpha.reviewed_evidence_pack",
            ),
            downgrade_notes=("替代机构级全局 Orchestrator，只做本地研究侧轻量路由。",),
        ),
        AgentDefinition(
            agent_id="data_steward",
            name="数据基建代理",
            layer="数据与特征底座",
            mission="围绕 QMT 本地缓存构建 A 股研究快照、数据质量检查和基础特征框架。",
            inputs=("QMT 行情缓存", "财务数据", "板块/行业映射", "交易日历", "mx-data/GM 备用校验源"),
            outputs=("研究快照清单", "特征表", "数据质量告警"),
            hard_rules=(
                "行情和财务主口径优先使用 QMT，本地快照必须带 asof_date、fetch_time 和 source。",
                "禁止使用未来数据；缺失值只允许前向填充或显式剔除，不做跨未来插值。",
                "只面向 A 股股票池，ETF 可作为参考资产但不作为股指期货对冲替代。",
                "mx-data 和 GM 只能在缓存清洗发现异常时做小样本校验，不能直接覆盖 QMT 原始缓存；备用源之间必须交叉验证。",
            ),
            consumes_topics=("system.task", "system.context", "feedback.data_quality"),
            publishes_topics=("data.snapshot_manifest", "data.feature_frame", "data.quality_alert"),
            qmt_touchpoints=(
                "ResearchClient.get_daily_bars",
                "ResearchClient.write_snapshot",
                "QMTClient.get_financial_data",
                "QMTClient.get_sector_stocks",
            ),
            downgrade_notes=("合并原文的市场数据、另类数据和特征工程三类代理。",),
        ),
        AgentDefinition(
            agent_id="alpha_analyst",
            name="量价趋势因子专员",
            layer="信号生产",
            mission="专注 A 股量价趋势、动量反转、波动率、成交额和流动性因子，输出可交易右侧信号。",
            inputs=("研究快照", "行业映射", "复权 K 线", "成交额/换手", "流动性过滤结果"),
            outputs=("量价信号向量", "量价证据包", "量价研究摘要"),
            hard_rules=(
                "信号分数固定在 [-1, 1]，置信度必须与样本覆盖和数据质量绑定。",
                "禁止单一技术指标触发高置信度结论。",
                "盘中不更新模型参数；盘后再做因子评估和权重校准。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "data.snapshot_manifest",
                "data.feature_frame",
                "data.quality_alert",
                "feedback.agent_score",
            ),
            publishes_topics=("alpha.signal_vector", "alpha.evidence_pack", "alpha.research_summary"),
            qmt_touchpoints=(
                "research/factors",
                "research/reference/qmt_gics4_industry_map.csv",
                "QMTClient.get_history_ex",
            ),
            downgrade_notes=("原 Alpha 研究代理保留为量价趋势方向，复用既有量化因子专员。",),
        ),
        AgentDefinition(
            agent_id="alpha_fundamental_analyst",
            name="基本面因子专员",
            layer="信号生产",
            mission="专注 A 股估值、质量、盈利、成长、现金流和财务公告滞后口径，输出中低频基本面信号。",
            inputs=("研究快照", "财务三表", "公告日期", "行业映射", "财务可用股票池"),
            outputs=("基本面信号向量", "基本面证据包", "基本面研究摘要"),
            hard_rules=(
                "财务因子必须使用 announce_date 或明确公告滞后规则，禁止 report_date 前视。",
                "财务缺失、陈旧或单源冲突样本必须降权或剔除。",
                "不得把公开备用源单独作为最终财务真值。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "data.snapshot_manifest",
                "data.feature_frame",
                "data.quality_alert",
                "feedback.agent_score",
            ),
            publishes_topics=("alpha.signal_vector", "alpha.evidence_pack", "alpha.research_summary"),
            qmt_touchpoints=(
                "financial_balance",
                "financial_income",
                "financial_cashflow",
                "v_financial_fresh_universe",
            ),
            downgrade_notes=("把机构级基本面分析师降级为只研究 A 股财务与估值因子的专员。",),
        ),
        AgentDefinition(
            agent_id="alpha_style_analyst",
            name="风格状态因子专员",
            layer="信号生产",
            mission="专注 A 股市场状态、行业轮动、大小盘风格、风险偏好和拥挤度因子，输出风格适配信号。",
            inputs=("研究快照", "行业映射", "成交结构", "市场宽度", "数据质量告警"),
            outputs=("风格状态信号向量", "风格证据包", "风格研究摘要"),
            hard_rules=(
                "不得用股指期货或跨市场对冲假设解释 A 股股票组合收益。",
                "风格判断必须和价格、流动性、行业广度或回撤状态绑定。",
                "市场状态因子只能调节信号权重或股票池，不能单独触发高置信度买卖。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "data.snapshot_manifest",
                "data.feature_frame",
                "data.quality_alert",
                "feedback.agent_score",
            ),
            publishes_topics=("alpha.signal_vector", "alpha.evidence_pack", "alpha.research_summary"),
            qmt_touchpoints=(
                "research/reference/qmt_gics4_industry_map.csv",
                "v_factor_ready_daily_effective",
                "QMTClient.get_market_snapshot",
            ),
            downgrade_notes=("把机构级宏观/情绪/行业分析降级为 A 股风格状态因子专员。",),
        ),
        AgentDefinition(
            agent_id="strategy_debate_judge",
            name="策略辩论裁决代理",
            layer="策略研判",
            mission="对候选信号执行多头论证、空头质询和中立裁决，输出可解释交易建议。",
            inputs=("信号向量", "证据包", "数据质量告警", "市场状态上下文"),
            outputs=("Buy/Hold/Sell 决策", "审计轨迹", "置信度"),
            hard_rules=(
                "多头、空头、裁决三个角色作为内部状态机运行，不再拆成三个外部代理。",
                "每条论点必须引用数据代理或 Alpha 代理的证据。",
                "综合置信度低于 60 时只能输出 Hold 或观察。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "data.quality_alert",
                "alpha.reviewed_signal_vector",
                "alpha.reviewed_evidence_pack",
            ),
            publishes_topics=("strategy.decision", "strategy.audit_trail"),
            downgrade_notes=("保留原文辩论精华，但用单代理内部状态机控制代理数量。",),
        ),
        AgentDefinition(
            agent_id="portfolio_risk",
            name="组合与风控代理",
            layer="组合构建与风险",
            mission="把裁决建议转成目标持仓，并执行 T+1、集中度、流动性和回撤约束。",
            inputs=("策略决策", "当前持仓", "账户资产", "成交回报", "合规策略"),
            outputs=("目标持仓簿", "风险拦截", "风险告警"),
            hard_rules=(
                "维护 T+1 锁定池，当日买入仓位不得假设可立即卖出。",
                "控制单票、行业、现金占用和流动性集中度。",
                "不使用股指期货对冲；风险降低只能通过仓位、现金、股票池和交易节奏完成。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "strategy.decision",
                "strategy.audit_trail",
                "data.snapshot_manifest",
                "compliance.policy_update",
                "execution.fill_report",
            ),
            publishes_topics=("portfolio.target_book", "risk.alert", "risk.block"),
            qmt_touchpoints=(
                "QMTClient.query_asset",
                "QMTClient.query_positions",
                "QMTClient.get_market_snapshot",
            ),
            downgrade_notes=("合并机构级投资组合经理和实时风控代理，去掉股指期货对冲。",),
        ),
        AgentDefinition(
            agent_id="compliance_gatekeeper",
            name="合规闸门代理",
            layer="合规与交易前检查",
            mission="在组合目标和执行订单之间设置硬闸门，限制频率、权限、股票池和实盘开关。",
            inputs=("目标持仓簿", "风险拦截", "订单意图", "人工授权", "交易日状态"),
            outputs=("放行/拒绝", "合规拦截", "策略参数更新"),
            hard_rules=(
                "实盘下单必须具备 API Key、交易模块开启和明确的非 dry_run 授权。",
                "使用保守阈值监控申报/撤单频率：280 笔/秒预警，18000 笔/日预警。",
                "只允许 A 股股票交易链路；股指期货、期权、融资融券默认不进入本矩阵执行路径。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "portfolio.target_book",
                "risk.block",
                "execution.order_intent",
            ),
            publishes_topics=("compliance.clearance", "compliance.block", "compliance.policy_update"),
            qmt_touchpoints=("QMTClient.get_account_status", "QMTClient.query_orders"),
            downgrade_notes=("保留程序化交易合规红线，但不做机构级多产品穿透。",),
        ),
        AgentDefinition(
            agent_id="execution_attribution",
            name="执行归因代理",
            layer="执行与闭环反馈",
            mission="把合规放行后的目标拆成节奏化订单，记录成交、滑点、失败原因和代理贡献反馈。",
            inputs=("合规放行", "目标持仓簿", "风险告警", "成交回报", "市场快照"),
            outputs=("订单意图", "成交报告", "滑点归因", "代理权重反馈"),
            hard_rules=(
                "没有合规放行不得发起真实委托。",
                "子订单必须平滑发送，避免瞬时集中报单和无意义撤单。",
                "每个订单必须关联 strategy_name、order_remark、signal_id 或审计上下文。",
            ),
            consumes_topics=(
                "system.task",
                "system.context",
                "system.halt",
                "portfolio.target_book",
                "risk.alert",
                "compliance.clearance",
                "compliance.block",
            ),
            publishes_topics=(
                "execution.order_intent",
                "execution.fill_report",
                "feedback.agent_score",
                "feedback.data_quality",
            ),
            qmt_touchpoints=(
                "QMTClient.place_order",
                "QMTClient.cancel_order",
                "QMTClient.subscribe_trade_events",
            ),
            downgrade_notes=("合并机构级执行交易员和投后归因，先满足单账户 QMT 实盘闭环。",),
        ),
    )

    links = (
        AgentLink(
            "supervisor",
            "data_steward",
            "system.task",
            "研究任务、交易日范围、股票池和快照要求",
            guardrails=("任务不得要求读取 .env 或账户密钥。",),
        ),
        AgentLink("supervisor", "alpha_analyst", "system.task", "量价趋势因子研究任务和调仓频率"),
        AgentLink("supervisor", "alpha_fundamental_analyst", "system.task", "基本面因子研究任务和财报滞后口径"),
        AgentLink("supervisor", "alpha_style_analyst", "system.task", "风格状态因子研究任务和市场状态边界"),
        AgentLink("supervisor", "strategy_debate_judge", "system.task", "候选标的研判任务"),
        AgentLink("supervisor", "portfolio_risk", "system.task", "组合构建任务与账户边界"),
        AgentLink("supervisor", "compliance_gatekeeper", "system.task", "合规阈值和实盘授权状态"),
        AgentLink("supervisor", "execution_attribution", "system.task", "执行批次和复盘任务"),
        AgentLink(
            "supervisor",
            "data_steward",
            "system.context",
            "全局运行上下文、股票池、交易日和人工备注",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "alpha_analyst",
            "system.context",
            "全局运行上下文、调仓频率和量价研究边界",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "alpha_fundamental_analyst",
            "system.context",
            "全局运行上下文、财务公告口径和基本面研究边界",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "alpha_style_analyst",
            "system.context",
            "全局运行上下文、市场状态和风格研究边界",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "strategy_debate_judge",
            "system.context",
            "市场状态、数据质量和人工观察备注",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "portfolio_risk",
            "system.context",
            "账户边界、现金约束和策略运行状态",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "compliance_gatekeeper",
            "system.context",
            "交易权限、实盘授权和日内阈值上下文",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "execution_attribution",
            "system.context",
            "执行批次、节奏要求和复盘上下文",
            required=False,
        ),
        AgentLink(
            "supervisor",
            "execution_attribution",
            "system.halt",
            "人工或系统触发的暂停/恢复执行指令",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "supervisor",
            "data.quality_alert",
            "数据质量告警回传协调器",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "alpha_analyst",
            "data.snapshot_manifest",
            "量价研究快照路径、版本、asof_date、字段覆盖率",
            guardrails=("下游只能读取快照口径，不得临时混入未来更新数据。",),
        ),
        AgentLink(
            "data_steward",
            "alpha_fundamental_analyst",
            "data.snapshot_manifest",
            "基本面研究快照路径、版本、asof_date、字段覆盖率",
            guardrails=("下游只能读取快照口径，不得临时混入未来更新数据。",),
        ),
        AgentLink(
            "data_steward",
            "alpha_style_analyst",
            "data.snapshot_manifest",
            "风格状态研究快照路径、版本、asof_date、字段覆盖率",
            guardrails=("下游只能读取快照口径，不得临时混入未来更新数据。",),
        ),
        AgentLink(
            "data_steward",
            "alpha_analyst",
            "data.feature_frame",
            "量价特征表、行业映射和有效日线视图",
        ),
        AgentLink(
            "data_steward",
            "alpha_fundamental_analyst",
            "data.feature_frame",
            "财务三表、公告滞后、估值质量成长特征表",
        ),
        AgentLink(
            "data_steward",
            "alpha_style_analyst",
            "data.feature_frame",
            "行业轮动、市场宽度、流动性和风格状态特征表",
        ),
        AgentLink(
            "data_steward",
            "alpha_analyst",
            "data.quality_alert",
            "量价数据缺口、停牌、复权异常等信号降权提示",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "alpha_fundamental_analyst",
            "data.quality_alert",
            "财务缺失、公告日期异常、备用源冲突等信号降权提示",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "alpha_style_analyst",
            "data.quality_alert",
            "行业映射缺失、市场宽度异常、交易日口径异常等信号降权提示",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "strategy_debate_judge",
            "data.quality_alert",
            "停牌、缺失、复权异常、财务字段覆盖不足告警",
            required=False,
        ),
        AgentLink(
            "data_steward",
            "portfolio_risk",
            "data.snapshot_manifest",
            "组合构建使用的数据版本、交易日和可交易性边界",
            required=False,
        ),
        AgentLink(
            "alpha_analyst",
            "supervisor",
            "alpha.signal_vector",
            "量价趋势信号向量回传总调度",
        ),
        AgentLink(
            "alpha_analyst",
            "supervisor",
            "alpha.evidence_pack",
            "量价趋势证据包回传总调度",
        ),
        AgentLink(
            "alpha_analyst",
            "supervisor",
            "alpha.research_summary",
            "量价趋势研究摘要回传总调度",
        ),
        AgentLink(
            "alpha_fundamental_analyst",
            "supervisor",
            "alpha.signal_vector",
            "基本面信号向量回传总调度",
        ),
        AgentLink(
            "alpha_fundamental_analyst",
            "supervisor",
            "alpha.evidence_pack",
            "基本面证据包回传总调度",
        ),
        AgentLink(
            "alpha_fundamental_analyst",
            "supervisor",
            "alpha.research_summary",
            "基本面研究摘要回传总调度",
        ),
        AgentLink(
            "alpha_style_analyst",
            "supervisor",
            "alpha.signal_vector",
            "风格状态信号向量回传总调度",
        ),
        AgentLink(
            "alpha_style_analyst",
            "supervisor",
            "alpha.evidence_pack",
            "风格状态证据包回传总调度",
        ),
        AgentLink(
            "alpha_style_analyst",
            "supervisor",
            "alpha.research_summary",
            "风格状态研究摘要回传总调度",
        ),
        AgentLink(
            "supervisor",
            "strategy_debate_judge",
            "alpha.reviewed_signal_vector",
            "总调度审核后的三因子合并信号向量",
        ),
        AgentLink(
            "supervisor",
            "strategy_debate_judge",
            "alpha.reviewed_evidence_pack",
            "总调度审核后的三因子证据包和冲突处理说明",
        ),
        AgentLink(
            "strategy_debate_judge",
            "portfolio_risk",
            "strategy.decision",
            "Buy/Hold/Sell、置信度、建议基础权重和否决原因",
            guardrails=("置信度低于 60 的标的不得进入建仓目标。",),
        ),
        AgentLink(
            "strategy_debate_judge",
            "supervisor",
            "strategy.decision",
            "裁决结果和低置信度观察池摘要",
            required=False,
        ),
        AgentLink(
            "strategy_debate_judge",
            "portfolio_risk",
            "strategy.audit_trail",
            "多头论证、空头质询、裁决摘要",
            required=False,
        ),
        AgentLink(
            "portfolio_risk",
            "compliance_gatekeeper",
            "portfolio.target_book",
            "目标持仓、调仓差额、T+1 锁定池和现金约束",
        ),
        AgentLink(
            "portfolio_risk",
            "compliance_gatekeeper",
            "risk.block",
            "风险硬拦截及恢复条件",
        ),
        AgentLink(
            "portfolio_risk",
            "supervisor",
            "risk.alert",
            "风险预警回传协调器",
            required=False,
        ),
        AgentLink(
            "portfolio_risk",
            "supervisor",
            "risk.block",
            "风险硬拦截回传协调器",
            required=False,
        ),
        AgentLink(
            "portfolio_risk",
            "execution_attribution",
            "portfolio.target_book",
            "经风控处理后的目标持仓和调仓差额",
        ),
        AgentLink(
            "portfolio_risk",
            "execution_attribution",
            "risk.alert",
            "盘中降速、暂停或撤退提示",
            required=False,
        ),
        AgentLink(
            "compliance_gatekeeper",
            "execution_attribution",
            "compliance.clearance",
            "允许执行的订单批次、额度、频率限制和有效期",
        ),
        AgentLink(
            "compliance_gatekeeper",
            "execution_attribution",
            "compliance.block",
            "拒绝原因、人工复核要求和恢复条件",
        ),
        AgentLink(
            "compliance_gatekeeper",
            "supervisor",
            "compliance.block",
            "合规拦截和人工复核需求回传协调器",
            required=False,
        ),
        AgentLink(
            "compliance_gatekeeper",
            "portfolio_risk",
            "compliance.policy_update",
            "日内额度、频率阈值、交易权限或黑名单变更",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "compliance_gatekeeper",
            "execution.order_intent",
            "准备发出的子订单请求，用于最后一道交易前检查",
        ),
        AgentLink(
            "execution_attribution",
            "portfolio_risk",
            "execution.fill_report",
            "委托、成交、撤单、失败原因和剩余目标差额",
        ),
        AgentLink(
            "execution_attribution",
            "supervisor",
            "execution.fill_report",
            "成交与失败摘要回传协调器",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "alpha_analyst",
            "feedback.agent_score",
            "量价信号贡献、滑点、胜率和失效标签",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "alpha_fundamental_analyst",
            "feedback.agent_score",
            "基本面信号贡献、滑点、胜率和失效标签",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "alpha_style_analyst",
            "feedback.agent_score",
            "风格状态信号贡献、滑点、胜率和失效标签",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "supervisor",
            "feedback.agent_score",
            "代理贡献和权重调整建议回传协调器",
            required=False,
        ),
        AgentLink(
            "execution_attribution",
            "data_steward",
            "feedback.data_quality",
            "行情延迟、成交回报缺口、快照字段异常",
            required=False,
        ),
    )

    return AgentMatrix(agents=agents, links=links)

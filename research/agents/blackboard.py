"""代理矩阵的轻量黑板通信实现。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from .matrix import AgentMatrix, build_default_quant_agent_matrix


@dataclass(frozen=True)
class AgentMessage:
    """黑板中的一条代理消息。"""

    message_id: str
    source: str
    target: str
    topic: str
    payload: dict[str, Any]
    created_at: str
    priority: int = 1
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化结构。"""

        return {
            "message_id": self.message_id,
            "source": self.source,
            "target": self.target,
            "topic": self.topic,
            "payload": self.payload,
            "created_at": self.created_at,
            "priority": self.priority,
            "correlation_id": self.correlation_id,
        }


class AgentBlackboard:
    """带路由校验的内存黑板。

    这个类用于 notebook、回测和轻量策略原型里的进程内通信。它不是消息队列，
    也不负责持久化；正式实盘可以把同样的主题和链路迁移到 SQLite、Redis 或
    WebSocket 事件流。
    """

    def __init__(self, matrix: AgentMatrix | None = None) -> None:
        self.matrix = matrix or build_default_quant_agent_matrix()
        errors = self.matrix.validate()
        if errors:
            raise ValueError("invalid agent matrix: " + "; ".join(errors))
        self._messages: list[AgentMessage] = []
        self._lock = RLock()

    def publish(
        self,
        *,
        source: str,
        target: str,
        topic: str,
        payload: dict[str, Any],
        priority: int = 1,
        correlation_id: str = "",
    ) -> AgentMessage:
        """发布一条消息；未声明链路会被拒绝。"""

        if not self.matrix.allows_route(source, target, topic):
            raise ValueError(f"route not allowed: {source} -> {target} [{topic}]")
        if priority < 0:
            raise ValueError("priority must be non-negative")

        message = AgentMessage(
            message_id=uuid4().hex,
            source=source,
            target=target,
            topic=topic,
            payload=dict(payload),
            created_at=datetime.now(timezone.utc).isoformat(),
            priority=priority,
            correlation_id=correlation_id,
        )
        with self._lock:
            self._messages.append(message)
        return message

    def messages(
        self,
        *,
        source: str | None = None,
        target: str | None = None,
        topic: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[AgentMessage, ...]:
        """按条件读取消息，返回时间顺序结果。"""

        with self._lock:
            rows = tuple(self._messages)
        return tuple(
            message
            for message in rows
            if (source is None or message.source == source)
            and (target is None or message.target == target)
            and (topic is None or message.topic == topic)
            and (correlation_id is None or message.correlation_id == correlation_id)
        )

    def latest(self, *, target: str | None = None, topic: str | None = None) -> AgentMessage | None:
        """读取符合条件的最新消息。"""

        rows = self.messages(target=target, topic=topic)
        return rows[-1] if rows else None

    def pending_for(self, agent_id: str) -> tuple[AgentMessage, ...]:
        """读取某个代理待消费消息，按优先级和时间排序。"""

        rows = self.messages(target=agent_id)
        return tuple(sorted(rows, key=lambda message: (-message.priority, message.created_at)))

    def summary(self) -> dict[str, Any]:
        """返回黑板当前消息分布。"""

        with self._lock:
            rows = tuple(self._messages)

        by_topic: dict[str, int] = {}
        by_target: dict[str, int] = {}
        for message in rows:
            by_topic[message.topic] = by_topic.get(message.topic, 0) + 1
            by_target[message.target] = by_target.get(message.target, 0) + 1

        return {
            "message_count": len(rows),
            "by_topic": dict(sorted(by_topic.items())),
            "by_target": dict(sorted(by_target.items())),
        }

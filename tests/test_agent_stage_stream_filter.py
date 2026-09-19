# -*- coding: utf-8 -*-
"""只有产出最终答复的阶段允许把模型原文推流给客户端。

流水线里的中间阶段（技术面 / 情报 / 风险 / 战法）也会走 LLM 流式调用，但它们的
输出是中间推理和原始 JSON。前端把 ``content_delta`` 实时拼进回答气泡，于是用户在
整段分析期间看到的是一堆英文和 JSON，直到 ``done`` 才被最终答复覆盖。
"""

import unittest
from unittest.mock import MagicMock

from src.agent.orchestrator import FINAL_ANSWER_STAGES, AgentOrchestrator
from src.agent.protocols import AgentContext, StageResult, StageStatus


class _StreamingAgent:
    """阶段 agent 桩：把一串事件原样推给收到的 progress_callback。"""

    def __init__(self, agent_name, events):
        self.agent_name = agent_name
        self._events = events

    def run(self, ctx, progress_callback=None, timeout_seconds=None):
        for event in self._events:
            if progress_callback is not None:
                progress_callback(event)
        return StageResult(stage_name=self.agent_name, status=StageStatus.COMPLETED)


class _NoCallbackAgent:
    """阶段 agent 桩：记录收到的 progress_callback 是否为 None。"""

    def __init__(self):
        self.agent_name = "technical"
        self.received_callback = "unset"

    def run(self, ctx, progress_callback=None, timeout_seconds=None):
        self.received_callback = progress_callback
        return StageResult(stage_name=self.agent_name, status=StageStatus.COMPLETED)


def _events():
    return [
        {"type": "thinking", "message": "正在制定分析路径..."},
        {"type": "content_delta", "delta": "I'll fetch the data for 600519."},
        {"type": "tool_start", "tool": "get_realtime_quote"},
        {"type": "content_delta", "delta": '{"signal": "hold"}'},
        {"type": "tool_done", "tool": "get_realtime_quote", "success": True},
    ]


class TestStageStreamFilter(unittest.TestCase):
    def _orchestrator(self):
        return AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())

    def _run_stage(self, agent_name):
        orchestrator = self._orchestrator()
        received = []
        orchestrator._run_stage_agent(
            _StreamingAgent(agent_name, _events()),
            AgentContext(query="test", stock_code="600519"),
            progress_callback=received.append,
        )
        return received

    def test_intermediate_stages_do_not_stream_model_text(self):
        for agent_name in ("technical", "intel", "risk", "skill_shrink_pullback"):
            received = self._run_stage(agent_name)
            self.assertEqual(
                [e["type"] for e in received],
                ["thinking", "tool_start", "tool_done"],
                msg=f"{agent_name} 不应把 content_delta 推给客户端",
            )

    def test_final_stage_keeps_streaming_model_text(self):
        received = self._run_stage("decision")
        self.assertEqual(
            [e["type"] for e in received],
            ["thinking", "content_delta", "tool_start", "content_delta", "tool_done"],
        )
        self.assertEqual(
            "".join(e["delta"] for e in received if e["type"] == "content_delta"),
            'I\'ll fetch the data for 600519.{"signal": "hold"}',
        )

    def test_missing_callback_stays_none(self):
        orchestrator = self._orchestrator()
        agent = _NoCallbackAgent()
        orchestrator._run_stage_agent(
            agent, AgentContext(query="test", stock_code="600519")
        )
        self.assertIsNone(agent.received_callback)

    def test_decision_is_the_last_stage_in_every_mode(self):
        """decision 始终是流水线收尾阶段，因此是唯一允许推流的阶段。"""
        self.assertEqual(FINAL_ANSWER_STAGES, frozenset({"decision"}))
        orchestrator = self._orchestrator()
        for mode in ("quick", "standard", "full", "specialist"):
            orchestrator.mode = mode
            chain = orchestrator._build_agent_chain(
                AgentContext(query="test", stock_code="600519")
            )
            self.assertEqual(chain[-1].agent_name, "decision", msg=f"mode={mode}")

    def test_other_event_types_still_reach_the_client(self):
        received = self._run_stage("technical")
        self.assertTrue(any(e["type"] == "tool_start" for e in received))
        self.assertTrue(any(e["type"] == "thinking" for e in received))


if __name__ == "__main__":
    unittest.main()

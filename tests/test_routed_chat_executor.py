# -*- coding: utf-8 -*-
"""Per-turn routing between the general single Agent and the multi pipeline."""

from __future__ import annotations

from types import SimpleNamespace

from src.agent.routed_chat_executor import RoutedAgentChatExecutor


class _FakeDelegate:
    def __init__(self, name: str) -> None:
        self.name = name
        self.prepared: list[dict] = []
        self.executed: list[dict] = []

    def prepare_turn(self, *, message, session_id, context=None, selected_skill_ids=None):
        self.prepared.append({
            "message": message,
            "session_id": session_id,
            "context": context,
            "selected_skill_ids": selected_skill_ids,
        })
        return {"delegate": self.name, "message": message}

    def execute_turn(self, turn, *, progress_callback=None, cancel_event=None):
        self.executed.append({
            "turn": turn,
            "progress_callback": progress_callback,
            "cancel_event": cancel_event,
        })
        return f"{self.name}-result"


def _build(config_overrides=None):
    values = {
        "agent_arch": "multi",
        "agent_chat_intent_routing_enabled": True,
        "agent_orchestrator_timeout_s": 600,
    }
    values.update(config_overrides or {})
    config = SimpleNamespace(**values)
    single = _FakeDelegate("single")
    multi = _FakeDelegate("multi")
    return (
        RoutedAgentChatExecutor(
            single_executor=single,
            multi_executor=multi,
            config=config,
        ),
        single,
        multi,
        config,
    )


def test_general_question_uses_single_agent() -> None:
    executor, single, multi, _ = _build()

    turn = executor.prepare_turn(message="你好", session_id="s1")
    result = executor.execute_turn(turn)

    assert result == "single-result"
    assert len(single.prepared) == 1
    assert not multi.prepared
    assert len(single.executed) == 1
    assert not multi.executed


def test_stock_analysis_question_uses_multi_pipeline() -> None:
    executor, single, multi, _ = _build()

    turn = executor.prepare_turn(message="分析一下 600519", session_id="s1")
    result = executor.execute_turn(turn)

    assert result == "multi-result"
    assert not single.prepared
    assert len(multi.prepared) == 1
    assert len(multi.executed) == 1


def test_site_question_with_stock_code_stays_on_single_agent() -> None:
    executor, single, multi, _ = _build()

    executor.prepare_turn(message="把 600519 加入自选", session_id="s1")

    assert len(single.prepared) == 1
    assert not multi.prepared


def test_routing_disabled_forces_multi() -> None:
    executor, single, multi, _ = _build({"agent_chat_intent_routing_enabled": False})

    executor.prepare_turn(message="你好", session_id="s1")

    assert not single.prepared
    assert len(multi.prepared) == 1


def test_chat_helper_routes_and_executes_in_one_call() -> None:
    executor, single, multi, _ = _build()

    assert executor.chat("今天大盘怎么样", "s1") == "single-result"
    assert executor.chat("分析 600519", "s1") == "multi-result"
    assert len(single.executed) == 1
    assert len(multi.executed) == 1


def test_timeout_budget_is_exposed_to_the_stream_endpoint() -> None:
    executor, _, _, _ = _build({"agent_orchestrator_timeout_s": 120})

    assert executor.timeout_seconds == 120

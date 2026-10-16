# -*- coding: utf-8 -*-
"""Stopping a LiteLLM Agent turn: loop checks, stream interruption, mapping.

Covers the contract that ``/chat/stream/{request_id}/cancel`` relies on for the
default (non-Codex) backend: the runner observes the caller's cancel event, the
adapter abandons an in-flight streamed generation instead of retrying it, and a
cancelled run reaches the executor as ``error_code="cancelled"``.
"""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agent.agent_backend import AgentRunRequest, LiteLLMAgentBackend
from src.agent.llm_adapter import LLMCallCancelled, LLMResponse, LLMToolAdapter, ToolCall
from src.agent.runner import run_agent_loop
from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry


class _ScriptedAdapter:
    """Adapter returning queued responses and recording the cancel event it saw."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.seen_cancel_events = []
        self.call_count = 0

    def call_with_tools(self, messages, tools, timeout=None, progress_callback=None, cancel_event=None):
        self.seen_cancel_events.append(cancel_event)
        self.call_count += 1
        return self.responses.pop(0)


class _InterruptingAdapter:
    """Adapter that simulates a stop arriving while the model is generating."""

    def __init__(self) -> None:
        self.seen_cancel_events = []

    def call_with_tools(self, messages, tools, timeout=None, progress_callback=None, cancel_event=None):
        self.seen_cancel_events.append(cancel_event)
        raise LLMCallCancelled("LLM streaming cancelled by caller")


def _registry_with_echo(on_call=None) -> ToolRegistry:
    registry = ToolRegistry()

    def _echo(message):
        if on_call is not None:
            on_call()
        return {"echo": message}

    registry.register(
        ToolDefinition(
            name="echo",
            description="Echoes back the input",
            parameters=[ToolParameter(name="message", type="string", description="Message to echo")],
            handler=_echo,
        )
    )
    return registry


def _tool_call_response() -> LLMResponse:
    return LLMResponse(
        content="Need data.",
        tool_calls=[ToolCall(id="call_1", name="echo", arguments={"message": "hi"})],
        usage={"total_tokens": 7},
        provider="openai",
        model="openai/gpt-test",
    )


def _final_answer_response() -> LLMResponse:
    return LLMResponse(
        content="answer",
        tool_calls=[],
        usage={"total_tokens": 7},
        provider="openai",
        model="openai/gpt-test",
    )


def _request(**overrides) -> AgentRunRequest:
    values = {
        "system_prompt": "system",
        "history_messages": [],
        "user_message": "question",
        "session_id": "session-1",
        "stock_scope": None,
        "max_steps": 3,
        "max_wall_clock_seconds": 30,
        "progress_callback": None,
        "cancel_event": None,
    }
    values.update(overrides)
    return AgentRunRequest(**values)


def test_agent_loop_returns_cancelled_before_calling_the_model() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    adapter = _ScriptedAdapter([_final_answer_response()])

    result = run_agent_loop(
        messages=[{"role": "user", "content": "question"}],
        tool_registry=_registry_with_echo(),
        llm_adapter=adapter,
        max_steps=3,
        cancel_event=cancel_event,
    )

    assert result.cancelled is True
    assert result.success is False
    assert result.error == "Agent request was cancelled"
    assert result.total_steps == 0
    assert adapter.call_count == 0


def test_agent_loop_checks_cancellation_after_a_tool_call_finishes() -> None:
    """A stop issued while a tool runs lands at the next step boundary."""
    cancel_event = threading.Event()
    adapter = _ScriptedAdapter([_tool_call_response(), _final_answer_response()])

    result = run_agent_loop(
        messages=[{"role": "user", "content": "question"}],
        tool_registry=_registry_with_echo(on_call=cancel_event.set),
        llm_adapter=adapter,
        max_steps=3,
        cancel_event=cancel_event,
    )

    assert result.cancelled is True
    assert result.success is False
    # The tool's own result is kept — the in-flight call is not interrupted —
    # but the loop must not pay for another model round-trip.
    assert len(result.tool_calls_log) == 1
    assert result.total_steps == 1
    assert adapter.call_count == 1


def test_agent_loop_reports_cancelled_when_the_model_call_is_interrupted() -> None:
    cancel_event = threading.Event()
    adapter = _InterruptingAdapter()

    result = run_agent_loop(
        messages=[{"role": "user", "content": "question"}],
        tool_registry=_registry_with_echo(),
        llm_adapter=adapter,
        max_steps=3,
        cancel_event=cancel_event,
    )

    assert result.cancelled is True
    assert result.success is False
    assert result.error == "Agent request was cancelled"
    assert adapter.seen_cancel_events == [cancel_event]


def test_litellm_backend_threads_the_cancel_event_and_maps_cancelled() -> None:
    cancel_event = threading.Event()
    adapter = _InterruptingAdapter()

    result = LiteLLMAgentBackend(_registry_with_echo(), adapter).run(
        _request(cancel_event=cancel_event)
    )

    assert adapter.seen_cancel_events == [cancel_event]
    assert result.success is False
    assert result.error_code == "cancelled"
    assert result.backend == "litellm"


def test_litellm_backend_without_a_cancel_event_still_succeeds() -> None:
    adapter = _ScriptedAdapter([_final_answer_response()])

    result = LiteLLMAgentBackend(_registry_with_echo(), adapter).run(_request())

    assert result.success is True
    assert result.final_answer == "answer"
    assert result.error_code is None
    assert adapter.seen_cancel_events == [None]


def test_call_completion_does_not_retry_a_cancelled_call(monkeypatch) -> None:
    """A stop must not be "recovered" by trying the next configured model."""
    adapter = LLMToolAdapter.__new__(LLMToolAdapter)
    adapter._config = SimpleNamespace()
    adapter._backend_error = None
    monkeypatch.setattr(
        "src.agent.llm_adapter.resolve_agent_litellm_route",
        lambda config: SimpleNamespace(models_to_try=["model-a", "model-b"]),
    )
    attempts = []

    def _cancelled(*args, **kwargs):
        attempts.append(kwargs)
        raise LLMCallCancelled("stop")

    monkeypatch.setattr(LLMToolAdapter, "_call_litellm_model", _cancelled)

    with pytest.raises(LLMCallCancelled):
        adapter.call_completion([{"role": "user", "content": "question"}], tools=[])

    assert len(attempts) == 1


class _FakeStream:
    """Minimal streaming response: two content chunks, then exhaustion."""

    def __init__(self) -> None:
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for delta in ("first", "second"):
            self.consumed += 1
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=delta, tool_calls=None, reasoning_content=None)
                    )
                ],
            )

    def close(self) -> None:
        self.closed = True


def _stream_once(stream: _FakeStream, cancel_event, deltas: list):
    adapter = LLMToolAdapter.__new__(LLMToolAdapter)
    return adapter._call_litellm_stream(
        completion_callable=lambda kwargs: stream,
        call_kwargs={"model": "openai/gpt-test"},
        model="openai/gpt-test",
        openai_messages=[{"role": "user", "content": "question"}],
        model_list=[],
        progress_callback=deltas.append,
        cancel_event=cancel_event,
    )


def test_streaming_stops_between_chunks_and_closes_the_connection() -> None:
    """The stop lands mid-generation, not after the model finishes."""
    cancel_event = threading.Event()
    stream = _FakeStream()
    deltas: list = []

    def _set_on_first_delta(event):
        deltas.append(event)
        cancel_event.set()

    adapter = LLMToolAdapter.__new__(LLMToolAdapter)
    with pytest.raises(LLMCallCancelled):
        adapter._call_litellm_stream(
            completion_callable=lambda kwargs: stream,
            call_kwargs={"model": "openai/gpt-test"},
            model="openai/gpt-test",
            openai_messages=[{"role": "user", "content": "question"}],
            model_list=[],
            progress_callback=_set_on_first_delta,
            cancel_event=cancel_event,
        )

    assert stream.closed is True
    # Only the first delta reached the client. The check sits at the top of the
    # loop body, so the generator may already have produced the next chunk by
    # the time it fires — that one is discarded unread, never streamed on.
    assert len(deltas) == 1
    assert stream.consumed == 2


def test_streaming_without_cancellation_consumes_every_chunk() -> None:
    """Positive control for the test above: no event, no interruption."""
    stream = _FakeStream()
    deltas: list = []

    response = _stream_once(stream, None, deltas)

    assert stream.closed is False
    assert stream.consumed == 2
    assert len(deltas) == 2
    assert response.content == "firstsecond"


def test_streaming_does_not_mask_cancellation_as_a_transport_error() -> None:
    """Closing a stream may raise; the caller must still see LLMCallCancelled."""
    cancel_event = threading.Event()
    cancel_event.set()

    class _RefusingStream(_FakeStream):
        def close(self) -> None:
            raise RuntimeError("transport already gone")

    with pytest.raises(LLMCallCancelled):
        _stream_once(_RefusingStream(), cancel_event, [])


# ---------------------------------------------------------------------------
# Multi-agent Chat (AGENT_ARCH=multi): the same event must be accepted by the
# orchestrator and reach every stage, including the parallel skill wave.
# ---------------------------------------------------------------------------


class _ScriptedStageAgent:
    """Stage agent double that records the cancel event it was handed.

    ``cancel_on_run`` mimics what :meth:`BaseAgent.run` does when its LLM loop
    comes back cancelled: the event is set and the stage reports FAILED without
    a failure reason (a stop is not a stage failure).
    """

    def __init__(self, name: str, *, cancel_on_run: bool = False) -> None:
        self.agent_name = name
        self.calls = 0
        self.seen_cancel_events: list = []
        self._cancel_on_run = cancel_on_run

    def run(self, ctx, progress_callback=None, timeout_seconds=None, cancel_event=None):
        from src.agent.protocols import StageResult, StageStatus

        self.calls += 1
        self.seen_cancel_events.append(cancel_event)
        if self._cancel_on_run and cancel_event is not None:
            cancel_event.set()
            result = StageResult(
                stage_name=self.agent_name,
                status=StageStatus.FAILED,
                error="Agent request was cancelled",
            )
            result.failure_reason = None
            result.meta["cancelled"] = True
            return result

        result = StageResult(stage_name=self.agent_name, status=StageStatus.COMPLETED)
        result.meta["raw_text"] = "ok"
        return result


def _orchestrator():
    from src.agent.orchestrator import AgentOrchestrator

    return AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())


def _turn(ctx):
    from src.agent.orchestrator import PreparedOrchestratorChatTurn

    return PreparedOrchestratorChatTurn(session_id="session-1", context=ctx)


def test_every_chat_executor_accepts_the_cancel_event() -> None:
    """Both real Chat executors must take the event the SSE endpoint passes.

    The endpoint hands ``cancel_event`` to whatever ``_build_executor`` returns,
    and that is not always the single-agent executor: with ``AGENT_ARCH=multi``
    it is the :class:`AgentOrchestrator`.  A mocked ``execute_turn`` accepts any
    keyword, which is exactly how the multi-agent branch shipped raising
    ``TypeError: AgentOrchestrator.execute_turn() got an unexpected keyword
    argument 'cancel_event'`` on every turn.  This asserts the real signatures
    instead of the mock's.
    """
    import inspect

    from src.agent.chat_executor import AgentChatExecutor
    from src.agent.orchestrator import AgentOrchestrator

    for executor_cls in (AgentChatExecutor, AgentOrchestrator):
        parameters = inspect.signature(executor_cls.execute_turn).parameters
        assert "cancel_event" in parameters, executor_cls.__name__
        assert parameters["cancel_event"].kind is inspect.Parameter.KEYWORD_ONLY


def test_orchestrator_stops_before_the_first_stage() -> None:
    from unittest.mock import patch

    from src.agent.protocols import AgentContext

    orchestrator = _orchestrator()
    technical = _ScriptedStageAgent("technical")
    decision = _ScriptedStageAgent("decision")
    cancel_event = threading.Event()
    cancel_event.set()

    with patch.object(orchestrator, "_build_agent_chain", return_value=[technical, decision]), \
         patch("src.agent.conversation.conversation_manager.add_message") as add_message:
        result = orchestrator.execute_turn(
            _turn(AgentContext(query="test")), cancel_event=cancel_event
        )

    assert result.success is False
    assert result.error_code == "cancelled"
    assert technical.calls == 0
    assert decision.calls == 0
    assert [call.args for call in add_message.call_args_list] == [
        ("session-1", "assistant", "[已停止] 本次分析已由用户停止。"),
    ]


def test_orchestrator_stops_when_a_stage_is_interrupted_mid_run() -> None:
    from unittest.mock import patch

    from src.agent.protocols import AgentContext

    orchestrator = _orchestrator()
    technical = _ScriptedStageAgent("technical", cancel_on_run=True)
    intel = _ScriptedStageAgent("intel")
    decision = _ScriptedStageAgent("decision")
    cancel_event = threading.Event()

    with patch.object(orchestrator, "_build_agent_chain", return_value=[technical, intel, decision]), \
         patch("src.agent.conversation.conversation_manager.add_message") as add_message:
        result = orchestrator.execute_turn(
            _turn(AgentContext(query="test")), cancel_event=cancel_event
        )

    assert result.error_code == "cancelled"
    assert technical.seen_cancel_events == [cancel_event]
    assert technical.calls == 1
    assert intel.calls == 0
    assert decision.calls == 0
    # A stopped stage is not an outcome: it is not recorded and not persisted
    # as an analysis failure.
    assert result.total_steps == 0
    assert [call.args for call in add_message.call_args_list] == [
        ("session-1", "assistant", "[已停止] 本次分析已由用户停止。"),
    ]


def test_orchestrator_threads_the_cancel_event_into_the_parallel_skill_wave() -> None:
    """The skill wave runs through the thread-pool scheduler, not the stage loop.

    The event has to ride along on the callable the scheduler is given, or a
    stop issued during the specialist wave would wait out every skill.
    """
    from src.agent.protocols import AgentContext

    orchestrator = _orchestrator()
    skill = _ScriptedStageAgent("strategy_bull_trend")
    cancel_event = threading.Event()

    orchestrator._run_specialist_agent_batch(
        [skill], AgentContext(query="test"), cancel_event=cancel_event
    )

    assert skill.seen_cancel_events == [cancel_event]


def test_orchestrator_runs_stages_without_a_cancel_event() -> None:
    """Positive control: no event, no stopping, the pipeline keeps going."""
    from unittest.mock import patch

    from src.agent.protocols import AgentContext

    orchestrator = _orchestrator()
    technical = _ScriptedStageAgent("technical")
    decision = _ScriptedStageAgent("decision")

    with patch.object(orchestrator, "_build_agent_chain", return_value=[technical, decision]), \
         patch("src.agent.conversation.conversation_manager.add_message"):
        result = orchestrator.execute_turn(_turn(AgentContext(query="test")))

    assert result.error_code is None
    assert technical.calls == 1
    assert decision.calls == 1
    assert technical.seen_cancel_events == [None]

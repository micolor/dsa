# -*- coding: utf-8 -*-
"""Tests for content_delta streaming events.

Covers the two backends that now feed the shared SSE ``content_delta`` pipeline:
- litellm: ``LLMToolAdapter._call_litellm_stream`` consumes a streaming completion,
  emits one ``content_delta`` per text chunk, and reassembles tool-call arguments.
- codex: ``CodexAppServerTransport._route_message`` emits a ``content_delta`` for each
  completed ``agentMessage`` answer item without changing ``final_text`` assembly.
"""

from types import SimpleNamespace

import pytest

from src.agent.codex_app_server_transport import CodexAppServerTransport
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.tools.execution import ToolAccessContext


def _stream_chunk(content=None, tool_calls=None):
    """Build a litellm-style stream chunk (SimpleNamespace shape)."""
    delta = SimpleNamespace(
        content=content,
        reasoning_content=None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _stream_tool_call(index, *, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
        provider_specific_fields=None,
        thought_signature=None,
    )


class TestLiteLLMStreaming:
    def test_emits_content_delta_and_reassembles_tool_calls(self):
        adapter = LLMToolAdapter.__new__(LLMToolAdapter)
        captured = {}

        def fake_parse(response, model, messages, **kwargs):
            captured["response"] = response
            captured["model"] = model
            return SimpleNamespace(ok=True)

        adapter._parse_litellm_response = fake_parse

        events = []
        stream = iter([
            _stream_chunk(content="你好"),
            _stream_chunk(content="，"),
            _stream_chunk(tool_calls=[_stream_tool_call(0, call_id="call_1", name="lookup", arguments='{"q":')]),
            _stream_chunk(tool_calls=[_stream_tool_call(0, arguments='"AAPL"}')]),
            _stream_chunk(content="总结"),
        ])

        def fake_callable(kwargs):
            assert kwargs.get("stream") is True
            return stream

        adapter._call_litellm_stream(
            completion_callable=fake_callable,
            call_kwargs={"model": "x"},
            model="x",
            openai_messages=[{"role": "user", "content": "hi"}],
            model_list=[],
            progress_callback=events.append,
        )

        # Only text chunks become content_delta events, in order.
        assert [e["type"] for e in events] == ["content_delta", "content_delta", "content_delta"]
        assert "".join(e["delta"] for e in events) == "你好，总结"

        synthetic = captured["response"]
        message = synthetic.choices[0].message
        assert message.content == "你好，总结"
        # Tool-call arguments arrived as two deltas and were concatenated.
        assert len(message.tool_calls) == 1
        tc = message.tool_calls[0]
        assert tc.id == "call_1"
        assert tc.function.name == "lookup"
        assert tc.function.arguments == '{"q":"AAPL"}'

    def test_emits_content_delta_for_plain_text_only(self):
        adapter = LLMToolAdapter.__new__(LLMToolAdapter)
        captured = {}
        adapter._parse_litellm_response = lambda response, model, messages, **kwargs: captured.setdefault(
            "response", response
        )

        events = []
        stream = iter([
            _stream_chunk(content="a"),
            _stream_chunk(content="b"),
            _stream_chunk(content="c"),
        ])

        adapter._call_litellm_stream(
            completion_callable=lambda kwargs: stream,
            call_kwargs={"model": "x"},
            model="x",
            openai_messages=[],
            model_list=[],
            progress_callback=events.append,
        )

        assert captured["response"].choices[0].message.content == "abc"
        assert "".join(e["delta"] for e in events) == "abc"


class TestCodexContentDelta:
    def _transport(self):
        return CodexAppServerTransport(
            ["unused"],
            tool_surface=object(),
            tool_context=ToolAccessContext(),
            request_timeout=3.0,
        )

    def _item_completed(self, thread_id, turn_id, *, phase, text):
        return {
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {"type": "agentMessage", "phase": phase, "text": text},
            },
        }

    def test_answer_item_frames_emit_content_delta_and_are_retained(self):
        client = self._transport()
        events = []
        client.set_stream_progress_callback(events.append)

        client._route_message(self._item_completed("t1", "tr1", phase="final_answer", text="hello "))
        client._route_message(self._item_completed("t1", "tr1", phase="final_answer", text="world"))

        assert [e["type"] for e in events] == ["content_delta", "content_delta"]
        assert "".join(e["delta"] for e in events) == "hello world"

        # Items are still retained so run_turn's final_text assembly is unchanged.
        items = client._completed_answer_items[("t1", "tr1")]
        assert [i["text"] for i in items] == ["hello ", "world"]

    def test_reasoning_items_do_not_emit_content_delta(self):
        client = self._transport()
        events = []
        client.set_stream_progress_callback(events.append)

        client._route_message(self._item_completed("t1", "tr1", phase="reasoning", text="chain of thought"))
        assert events == []

    def test_no_callback_does_not_raise(self):
        client = self._transport()
        client._route_message(self._item_completed("t1", "tr1", phase="final_answer", text="ok"))
        assert client._completed_answer_items[("t1", "tr1")][0]["text"] == "ok"

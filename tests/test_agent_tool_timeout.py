# -*- coding: utf-8 -*-
"""Verify a timed-out tool call cannot hold the process open.

``runner._execute_tools`` abandons a call that overruns its timeout rather
than cancelling it — Python cannot stop a thread that is already blocked
inside a third-party library, and ``Future.cancel()`` is a no-op on a call
that has started. The abandoned worker must therefore be a daemon thread: a
``ThreadPoolExecutor`` worker is not, and those are joined at interpreter
exit, so a stuck tool call keeps the whole process from shutting down.
"""
from __future__ import annotations

import json
import threading
import unittest

from src.agent.tools.registry import ToolDefinition, ToolRegistry


class _FakeToolCall:
    """Minimal stand-in for the ToolCall dataclass used by runner."""

    def __init__(self, name: str, arguments: dict | None = None):
        self.name = name
        self.arguments = arguments or {}
        self.id = f"fake_{name}"


def _registry_of(tool_names: list[str], handler) -> ToolRegistry:
    registry = ToolRegistry()
    for name in tool_names:
        registry.register(
            ToolDefinition(name=name, description="spy", parameters=[], handler=handler)
        )
    return registry


class ExecuteToolsTimeoutThreadTestCase(unittest.TestCase):
    """A timed-out call must leave no thread that blocks interpreter exit."""

    def _threads_since(self, before: set[int]) -> list[threading.Thread]:
        return [t for t in threading.enumerate() if t.ident not in before]

    def _assert_only_daemon_workers_survive(self, before: set[int]) -> None:
        survivors = self._threads_since(before)
        self.assertTrue(survivors, "expected the abandoned tool worker to still be running")
        not_daemon = [t.name for t in survivors if not t.daemon]
        self.assertEqual(
            not_daemon, [], f"a stuck tool call left non-daemon threads: {not_daemon}"
        )

    def test_timed_out_single_tool_leaves_only_a_daemon_worker(self) -> None:
        from src.agent.runner import _execute_tools

        release = threading.Event()

        def _hang(**_kwargs):
            release.wait(10)
            return json.dumps({"ok": True})

        registry = _registry_of(["hang_tool"], _hang)
        tc = _FakeToolCall("hang_tool")
        tool_calls_log: list[dict] = []
        before = {t.ident for t in threading.enumerate()}
        try:
            results = _execute_tools(
                tool_calls=[tc],
                tool_registry=registry,
                step=1,
                progress_callback=None,
                tool_calls_log=tool_calls_log,
                tool_wait_timeout_seconds=0.2,
            )
            # The caller got the timeout result and was not blocked by the hang.
            self.assertEqual(json.loads(results[0]["result_str"])["timeout"], True)
            self.assertEqual(tool_calls_log[0]["timeout"], True)
            self._assert_only_daemon_workers_survive(before)
        finally:
            release.set()

    def test_timed_out_tool_batch_leaves_only_daemon_workers(self) -> None:
        from src.agent.runner import _execute_tools

        release = threading.Event()

        def _hang(**_kwargs):
            release.wait(10)
            return json.dumps({"ok": True})

        names = ["hang_a", "hang_b"]
        registry = _registry_of(names, _hang)
        tool_calls = [_FakeToolCall(name) for name in names]
        tool_calls_log: list[dict] = []
        before = {t.ident for t in threading.enumerate()}
        try:
            results = _execute_tools(
                tool_calls=tool_calls,
                tool_registry=registry,
                step=1,
                progress_callback=None,
                tool_calls_log=tool_calls_log,
                tool_wait_timeout_seconds=0.2,
            )
            self.assertEqual(len(results), len(names))
            for entry in results:
                self.assertEqual(json.loads(entry["result_str"])["timeout"], True)
            self.assertEqual([entry["timeout"] for entry in tool_calls_log], [True, True])
            self._assert_only_daemon_workers_survive(before)
        finally:
            release.set()

    def test_tool_finishing_within_the_timeout_returns_its_real_result(self) -> None:
        """The bounded wait must not turn a completed call into a timeout."""
        from src.agent.runner import _execute_tools

        def _fast(**_kwargs):
            return json.dumps({"ok": True, "value": 42})

        registry = _registry_of(["fast_tool"], _fast)
        tool_calls_log: list[dict] = []
        results = _execute_tools(
            tool_calls=[_FakeToolCall("fast_tool")],
            tool_registry=registry,
            step=1,
            progress_callback=None,
            tool_calls_log=tool_calls_log,
            tool_wait_timeout_seconds=5.0,
        )

        payload = json.loads(results[0]["result_str"])
        self.assertEqual(payload.get("value"), 42)
        self.assertNotIn("timeout", tool_calls_log[0])


if __name__ == "__main__":
    unittest.main()

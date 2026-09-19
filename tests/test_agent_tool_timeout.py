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
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from src.agent.tools.registry import ToolDefinition, ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parents[1]


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


_CHILD_SCRIPT = """
import json
import threading

from src.agent.runner import _execute_tools
from src.agent.tools.registry import ToolDefinition, ToolRegistry

# The stuck call outlives the timeout by two orders of magnitude: if the
# interpreter waits for it, this child cannot finish in time.
release = threading.Event()


def _hang(**_kwargs):
    release.wait(120)
    return json.dumps({"ok": True})


class _Call:
    name = "hang_tool"
    arguments = {}
    id = "call_1"


registry = ToolRegistry()
registry.register(
    ToolDefinition(name="hang_tool", description="hang", parameters=[], handler=_hang)
)
tool_calls_log = []
print("runner-start", flush=True)
results = _execute_tools(
    tool_calls=[_Call()],
    tool_registry=registry,
    step=1,
    progress_callback=None,
    tool_calls_log=tool_calls_log,
    tool_wait_timeout_seconds=0.2,
)
print(json.dumps({
    "result": json.loads(results[0]["result_str"]),
    "logged_timeout": tool_calls_log[0].get("timeout"),
}), flush=True)
print("runner-done", flush=True)
"""


class AbandonedToolCallProcessExitTestCase(unittest.TestCase):
    """End-to-end: a stuck tool call must not keep the process from exiting.

    The in-process tests above pin the thread's ``daemon`` flag, which is the
    mechanism; this test pins the consequence the fix exists for — with a tool
    blocked for 120s and a 0.2s timeout, the interpreter still exits promptly.
    """

    def test_process_exits_promptly_while_a_tool_call_is_still_stuck(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [_REPO_ROOT.as_posix(), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)

        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT],
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertIn("runner-done", completed.stdout)
        json_lines = [
            line for line in completed.stdout.splitlines() if line.startswith("{")
        ]
        self.assertTrue(json_lines, completed.stdout[-2000:])
        payload = json.loads(json_lines[-1])
        self.assertEqual(payload["result"]["timeout"], True)
        self.assertEqual(payload["logged_timeout"], True)
        self.assertLess(
            elapsed,
            30.0,
            f"the stuck tool call held the process for {elapsed:.1f}s",
        )


if __name__ == "__main__":
    unittest.main()

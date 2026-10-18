# -*- coding: utf-8 -*-
"""Task 12–13 proposal envelopes must exist for every registered write tool.

The confirmation card is only rendered when the handler returns
``{kind, summary, proposal}`` with a kind the Web knows.  A new proposal tool
without a case here fails the coverage test instead of silently shipping a card
the frontend cannot dispatch.
"""

import json

import pytest

from src.agent.action_contract import (
    ACTION_EXECUTED_KINDS,
    ACTION_TASK_KINDS,
)
from src.agent.factory import get_tool_registry

# 用独立用例覆盖、不进通用表的工具：
# - ``propose_portfolio_trade`` 由 tests/test_action_tools.py 覆盖；
# - ``propose_config_update`` 需要按用例替换 config schema，见本文件下方两个专测。
EXTERNALLY_COVERED = {"propose_portfolio_trade", "propose_config_update"}

CASES = [
    {
        "label": "alert create",
        "tool": "propose_alert_rule_change",
        "args": {
            "action": "create",
            "target": "600519",
            "alert_type": "price_cross",
            "parameters": {"price": 1800},
        },
        "kind": "alert_create",
        "proposal": {
            "target": "600519",
            "alert_type": "price_cross",
            "severity": "info",
        },
        "summary_contains": "600519",
    },
    {
        "label": "cash ledger delete",
        "tool": "propose_portfolio_ledger_entry",
        "args": {"action": "delete_cash", "entry_id": 5},
        "kind": "portfolio_cash_delete",
        "proposal": {"entry_id": 5},
        "summary_contains": "#5",
    },
    {
        "label": "account deactivate",
        "tool": "propose_portfolio_account_change",
        "args": {"action": "deactivate", "account_id": 3},
        "kind": "portfolio_account_deactivate",
        "proposal": {"account_id": 3},
        "summary_contains": "#3",
    },
    {
        "label": "signal status",
        "tool": "propose_signal_change",
        "args": {"action": "status", "signal_id": 9, "status": "archived"},
        "kind": "signal_status",
        "proposal": {"signal_id": 9, "status": "archived"},
        "summary_contains": "#9",
    },
    {
        "label": "paper reset",
        "tool": "propose_paper_reset",
        "args": {},
        "kind": "paper_reset",
        "proposal": {},
        "summary_contains": "模拟盘",
    },
    {
        "label": "history delete by record ids",
        "tool": "propose_history_deletion",
        "args": {"action": "records", "record_ids": [11, 12]},
        "kind": "history_delete_records",
        "proposal": {"record_ids": [11, 12]},
        "summary_contains": "11",
    },
    {
        "label": "history delete by stock code",
        "tool": "propose_history_deletion",
        "args": {"action": "by_code", "stock_code": "600519"},
        "kind": "history_delete_by_code",
        "proposal": {"stock_code": "600519"},
        "summary_contains": "600519",
    },
    {
        "label": "intelligence source create",
        "tool": "propose_intelligence_source_change",
        "args": {
            "action": "create",
            "payload": {
                "name": "示例 RSS",
                # Public IP literal: the validator accepts it without a DNS lookup.
                "url": "https://93.184.216.34/feed.xml",
            },
        },
        "kind": "intelligence_source_create",
        "proposal": {
            "name": "示例 RSS",
            "url": "https://93.184.216.34/feed.xml",
            "source_type": "rss",
            "scope_type": "market",
            "market": "cn",
        },
        "summary_contains": "示例 RSS",
    },
    {
        "label": "scheduler run",
        "tool": "propose_scheduler_run",
        "args": {},
        "kind": "scheduler_run",
        "proposal": {},
        "summary_contains": "调度",
    },
    {
        "label": "notification test",
        "tool": "propose_notification_test",
        "args": {"channel": "feishu", "title": "测试", "content": "内容"},
        "kind": "notification_test",
        "proposal": {"channel": "feishu", "title": "测试", "content": "内容"},
        "summary_contains": "feishu",
    },
]


@pytest.mark.parametrize("case", CASES, ids=[case["label"] for case in CASES])
def test_write_proposal_envelope(case):
    registry = get_tool_registry()
    payload = registry.execute(case["tool"], **case["args"])
    assert "error" not in payload, payload
    assert payload["kind"] == case["kind"]
    assert payload["summary"]
    assert case["summary_contains"] in payload["summary"]
    proposal = payload["proposal"]
    assert isinstance(proposal, dict)
    for key, value in case["proposal"].items():
        assert proposal[key] == value, (key, proposal)
    # 提案载荷只保留接口字段，绝不能夹带密钥/令牌。
    serialized = json.dumps(proposal, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "sk-" not in serialized


def test_every_registered_proposal_tool_has_a_case():
    registry = get_tool_registry()
    registered = {
        tool.name
        for tool in registry.list_tools()
        if tool.policy.execution_mode == "proposal"
    }
    covered = {case["tool"] for case in CASES} | EXTERNALLY_COVERED
    assert registered, "注册表里没有 proposal 工具，断言会空过"
    assert registered == covered, (
        f"未覆盖的提案工具: {sorted(registered - covered)}；"
        f"表里多余的工具: {sorted(covered - registered)}"
    )


def test_alert_delete_requires_rule_id():
    payload = get_tool_registry().execute(
        "propose_alert_rule_change", action="delete"
    )
    assert "rule_id" in payload["error"]


def test_history_by_code_requires_stock_code():
    payload = get_tool_registry().execute(
        "propose_history_deletion", action="by_code"
    )
    assert "stock_code" in payload["error"]


def test_config_update_rejects_sensitive_keys_without_echoing_values(monkeypatch):
    import src.agent.tools.action_tools as action_tools

    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{
                "category": "llm",
                "fields": [
                    {"key": "STOCK_LIST", "is_sensitive": False},
                    {"key": "DEEPSEEK_API_KEY", "is_sensitive": True},
                ],
            }]}

    monkeypatch.setattr(
        action_tools, "_system_config_service", lambda: FakeConfigService()
    )
    payload = get_tool_registry().execute(
        "propose_config_update",
        config_version="v1",
        items=[{"key": "DEEPSEEK_API_KEY", "value": "sk-real-secret"}],
    )
    assert "DEEPSEEK_API_KEY" in payload["error"]
    assert "sk-real-secret" not in json.dumps(payload, ensure_ascii=False)


def test_config_update_accepts_non_sensitive_schema_keys(monkeypatch):
    import src.agent.tools.action_tools as action_tools

    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{
                "category": "base",
                "fields": [{"key": "STOCK_LIST", "is_sensitive": False}],
            }]}

    monkeypatch.setattr(
        action_tools, "_system_config_service", lambda: FakeConfigService()
    )
    payload = get_tool_registry().execute(
        "propose_config_update",
        config_version="v1",
        items=[{"key": "STOCK_LIST", "value": "600519,000001"}],
    )
    assert payload["kind"] == "config_update"
    assert payload["proposal"] == {
        "config_version": "v1",
        "items": [{"key": "STOCK_LIST", "value": "600519,000001"}],
    }


def test_web_executed_and_task_kind_arrays_match_backend():
    """``ACTION_EXECUTED_KINDS`` / ``ACTION_TASK_KINDS`` 必须与后端严格一致。"""
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1]
    source = (root / "apps/dsa-web/src/types/agentAction.ts").read_text(encoding="utf-8")
    literal = re.compile(r"""['"]([^'"]+)['"]""")

    def parse(name: str) -> set:
        match = re.search(
            rf"export const {name} = \[(.*?)\] as const;",
            source,
            re.DOTALL,
        )
        assert match, f"agentAction.ts 里找不到 {name}"
        values = set(literal.findall(match.group(1)))
        assert values, f"{name} 解析为空"
        return values

    assert parse("ACTION_EXECUTED_KINDS") == set(ACTION_EXECUTED_KINDS)
    assert parse("ACTION_TASK_KINDS") == set(ACTION_TASK_KINDS)

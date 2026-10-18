"""Security contract for ``propose_config_update``.

The tool must never turn a model request into a confirmation card for a secret
field: the value would be echoed back in the SSE event and rendered by the Web
client before the user ever confirms.  The only source of truth for "is this
field sensitive" is ``SystemConfigService.get_schema()`` — never a local
``KEY``/``TOKEN`` substring guess, which would drift from the settings page.
"""

import json

from src.agent.factory import get_tool_registry


def _patch_schema(monkeypatch, fields):
    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{"fields": fields}]}

    monkeypatch.setattr(
        "src.agent.tools.action_tools._system_config_service",
        lambda: FakeConfigService(),
    )


def test_config_update_rejects_sensitive_keys(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
        {"key": "DEEPSEEK_API_KEY", "is_sensitive": True},
    ])
    payload = get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "DEEPSEEK_API_KEY", "value": "sk-x"}],
    })
    assert payload["error"]
    assert "sk-x" not in json.dumps(payload)


def test_config_update_rejects_sensitive_keys_case_insensitively(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "DEEPSEEK_API_KEY", "is_sensitive": True},
    ])
    payload = get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "deepseek_api_key", "value": "sk-x"}],
    })
    assert payload["error"]
    assert "sk-x" not in json.dumps(payload)


def test_config_update_accepts_plain_key(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
    ])
    payload = get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "ENABLE_REALTIME_QUOTE", "value": "false"}],
    })
    assert payload["kind"] == "config_update"
    assert payload["proposal"]["config_version"] == "v1"
    assert payload["proposal"]["items"] == [
        {"key": "ENABLE_REALTIME_QUOTE", "value": "false"}
    ]


def test_config_update_rejects_empty_items_and_missing_version(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
    ])
    assert get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1", "items": [],
    })["error"]
    assert get_tool_registry().execute("propose_config_update", **{
        "config_version": "", "items": [{"key": "ENABLE_REALTIME_QUOTE", "value": "x"}],
    })["error"]


def test_config_update_rejects_unknown_keys_without_echoing_values(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
    ])
    payload = get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [
            {"key": "ENABLE_REALTIME_QUOTE", "value": "false"},
            {"key": "NOT_A_REAL_SETTING", "value": "secret-value"},
        ],
    })
    assert payload["error"]
    assert "NOT_A_REAL_SETTING" in payload["error"]
    assert "secret-value" not in json.dumps(payload)


def test_config_update_rejects_malformed_item_without_raising(monkeypatch):
    _patch_schema(monkeypatch, [
        {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
    ])
    payload = get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "ENABLE_REALTIME_QUOTE"}],
    })
    assert payload["error"]

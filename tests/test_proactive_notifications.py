# -*- coding: utf-8 -*-
"""Quiet-hours policy for proactive assistant pushes."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.services.proactive_notifications import in_quiet_hours, send_proactive_message

TZ = ZoneInfo("Asia/Shanghai")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 6, hour, minute, tzinfo=TZ)


def _config(value: str):
    return SimpleNamespace(agent_proactive_quiet_hours=value)


def test_empty_or_invalid_value_never_mutes():
    assert in_quiet_hours(_config(""), _at(23)) is False
    assert in_quiet_hours(_config("not-a-range"), _at(23)) is False
    assert in_quiet_hours(_config("25:00-08:00"), _at(23)) is False


def test_cross_midnight_range():
    config = _config("22:00-08:00")
    assert in_quiet_hours(config, _at(22, 0)) is True
    assert in_quiet_hours(config, _at(23, 30)) is True
    assert in_quiet_hours(config, _at(7, 59)) is True
    assert in_quiet_hours(config, _at(8, 0)) is False
    assert in_quiet_hours(config, _at(12, 0)) is False


def test_same_day_range():
    config = _config("12:00-14:00")
    assert in_quiet_hours(config, _at(11, 59)) is False
    assert in_quiet_hours(config, _at(12, 0)) is True
    assert in_quiet_hours(config, _at(13, 59)) is True
    assert in_quiet_hours(config, _at(14, 0)) is False


def test_equal_start_and_end_disables_quiet_hours():
    assert in_quiet_hours(_config("09:00-09:00"), _at(9)) is False


class _RecordingNotifier:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [True])

    def send(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        result = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _notify_config(**overrides):
    values = {
        "agent_proactive_notify_min_severity": "info",
        "agent_proactive_notify_route": "",
        "agent_proactive_notify_retries": 1,
        "agent_proactive_notify_base_url": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_send_respects_min_severity():
    notifier = _RecordingNotifier()
    config = _notify_config(agent_proactive_notify_min_severity="warning")

    assert send_proactive_message(notifier, config, "info", severity="info") is False
    assert notifier.calls == []
    assert send_proactive_message(notifier, config, "warn", severity="warning") is True


def test_send_retries_and_reports_success():
    notifier = _RecordingNotifier([RuntimeError("boom"), True])
    config = _notify_config(agent_proactive_notify_retries=1)

    assert send_proactive_message(notifier, config, "hello") is True
    assert len(notifier.calls) == 2


def test_send_gives_up_after_retries():
    notifier = _RecordingNotifier([False, False])
    config = _notify_config(agent_proactive_notify_retries=1)

    assert send_proactive_message(notifier, config, "hello") is False
    assert len(notifier.calls) == 2


def test_send_appends_station_deep_link():
    notifier = _RecordingNotifier()
    config = _notify_config(agent_proactive_notify_base_url="https://dsa.example.com")

    assert send_proactive_message(notifier, config, "body", item_id=7) is True
    content = notifier.calls[0]["content"]
    assert content.startswith("body")
    assert "https://dsa.example.com/?inbox=7" in content


def test_send_deep_link_without_item_targets_inbox_panel():
    notifier = _RecordingNotifier()
    config = _notify_config(agent_proactive_notify_base_url="https://dsa.example.com/")

    assert send_proactive_message(notifier, config, "body") is True
    assert "https://dsa.example.com/?inbox=1" not in notifier.calls[0]["content"]
    assert "https://dsa.example.com/" in notifier.calls[0]["content"]

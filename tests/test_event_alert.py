# -*- coding: utf-8 -*-
"""Tests for event-driven alert evaluation (dragon-tiger / capital flow / announcements).

Event facts are fetched through the single ``_fetch_event_fact`` boundary; these
tests mock that boundary with ``unittest.mock.patch`` so evaluation logic is
verified offline without touching network providers.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.services.event_alerts import (
    EVENT_ALERT_TYPES,
    EventAlert,
    evaluate_event_alert,
    normalize_event_alert_parameters,
)
from src.notification_routing import (
    NOTIFICATION_ROUTE_CONFIGS,
    get_notification_route_config,
    split_notification_route_channels,
)


def _rule(alert_type: str, parameters: dict, target: str = "600519") -> EventAlert:
    return EventAlert(
        target_scope="single_symbol",
        target=target,
        alert_type=alert_type,
        parameters=parameters,
        metadata={"persisted_rule_id": 7},
    )


class EventAlertNormalizeTestCase(unittest.TestCase):
    def test_dragon_tiger_defaults(self) -> None:
        self.assertEqual(
            normalize_event_alert_parameters("event_dragon_tiger", {}),
            {"min_recent_count": 1},
        )

    def test_capital_flow_defaults(self) -> None:
        params = normalize_event_alert_parameters("event_capital_flow", {})
        self.assertEqual(params["min_abs_inflow"], 100_000_000.0)

    def test_announcement_defaults(self) -> None:
        self.assertEqual(
            normalize_event_alert_parameters("event_announcement", {}),
            {"min_count": 1},
        )

    def test_unsupported_type_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported event alert_type"):
            normalize_event_alert_parameters("event_totally_unknown", {})

    def test_event_types_are_symbol_scoped(self) -> None:
        self.assertIn("event_dragon_tiger", EVENT_ALERT_TYPES)
        self.assertIn("event_capital_flow", EVENT_ALERT_TYPES)
        self.assertIn("event_announcement", EVENT_ALERT_TYPES)


class EventAlertEvaluationTestCase(unittest.TestCase):
    def test_dragon_tiger_triggers_when_on_list(self) -> None:
        fact = {"status": "ok", "is_on_list": True, "recent_count": 3, "latest_date": "2026-08-20"}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_dragon_tiger", {"min_recent_count": 2}))

        self.assertTrue(result["triggered"])
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["observed_value"], 3.0)
        diagnostics = json.loads(result["diagnostics"])
        self.assertTrue(diagnostics["is_on_list"])
        self.assertEqual(diagnostics["recent_count"], 3)

    def test_dragon_tiger_not_triggered_when_count_below_threshold(self) -> None:
        fact = {"status": "ok", "is_on_list": True, "recent_count": 1, "latest_date": None}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_dragon_tiger", {"min_recent_count": 2}))

        self.assertFalse(result["triggered"])
        self.assertEqual(result["status"], "not_triggered")

    def test_capital_flow_triggers_above_abs_threshold(self) -> None:
        fact = {"status": "ok", "main_net_inflow": 150_000_000.0}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_capital_flow", {"min_abs_inflow": 100_000_000.0}))

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 150_000_000.0)

    def test_capital_flow_not_triggered_below_threshold(self) -> None:
        fact = {"status": "ok", "main_net_inflow": 30_000_000.0}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_capital_flow", {"min_abs_inflow": 100_000_000.0}))

        self.assertFalse(result["triggered"])

    def test_announcement_triggers_when_count_met(self) -> None:
        fact = {"status": "ok", "count": 4}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_announcement", {"min_count": 3}))

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 4.0)

    def test_announcement_not_triggered_when_no_events(self) -> None:
        fact = {"status": "ok", "count": 0}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact):
            result = evaluate_event_alert(_rule("event_announcement", {"min_count": 1}))

        self.assertFalse(result["triggered"])

    def test_fetch_failure_degrades_without_raising(self) -> None:
        with patch("src.services.event_alerts._fetch_event_fact", side_effect=RuntimeError("provider down")):
            result = evaluate_event_alert(_rule("event_capital_flow", {}))

        self.assertFalse(result["triggered"])
        self.assertEqual(result["record_status"], "degraded")

    def test_event_uses_module_cache(self) -> None:
        cache: dict = {}
        fact = {"status": "ok", "is_on_list": True, "recent_count": 2, "latest_date": None}
        with patch("src.services.event_alerts._fetch_event_fact", return_value=fact) as mock_fetch:
            evaluate_event_alert(_rule("event_dragon_tiger", {}), cache=cache)
            evaluate_event_alert(_rule("event_dragon_tiger", {}), cache=cache)

        mock_fetch.assert_called_once()


class EventNotificationRouteTestCase(unittest.TestCase):
    def test_event_route_is_configured(self) -> None:
        config = NOTIFICATION_ROUTE_CONFIGS["event"]
        self.assertEqual(config["env_key"], "NOTIFICATION_EVENT_CHANNELS")
        self.assertEqual(config["config_attr"], "notification_event_channels")

    def test_get_route_config_is_case_insensitive(self) -> None:
        self.assertEqual(get_notification_route_config("EVENT")["config_attr"], "notification_event_channels")

    def test_invalid_channels_split_does_not_crash(self) -> None:
        valid, invalid = split_notification_route_channels(["feishu", "not_a_channel"])
        self.assertEqual(valid, ["feishu"])
        self.assertEqual(invalid, ["not_a_channel"])

    def test_event_route_with_no_channels_returns_empty(self) -> None:
        valid, invalid = split_notification_route_channels([])
        self.assertEqual(valid, [])
        self.assertEqual(invalid, [])


if __name__ == "__main__":
    unittest.main()

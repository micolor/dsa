from unittest import mock

import src.services.system_alert as sa


def test_send_system_alert_dispatches_with_system_error_route():
    sent = {}

    class _Svc:
        def send_with_results(self, content, **kw):
            sent["content"] = content
            sent["kw"] = kw
            return mock.Mock(success=True)

    with mock.patch.object(sa, "get_config", return_value=mock.Mock(
            runtime_analysis_failure_alert_enabled=True)):
        with mock.patch.object(sa, "NotificationService", return_value=_Svc()):
            ok = sa.send_system_alert("失败：LLM 超时", dedup_key="analysis-failure:2026-09-05")
    assert ok is True
    assert sent["kw"]["route_type"] == "system_error"
    assert sent["kw"]["dedup_key"] == "analysis-failure:2026-09-05"


def test_send_system_alert_returns_false_on_send_failure_no_loop():
    with mock.patch.object(sa, "get_config", return_value=mock.Mock(
            runtime_analysis_failure_alert_enabled=True)):
        with mock.patch.object(sa, "NotificationService", side_effect=RuntimeError("boom")):
            ok = sa.send_system_alert("x", dedup_key="k")
    assert ok is False  # 失败只返回 False，不抛、不再次触发告警

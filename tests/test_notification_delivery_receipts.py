# -*- coding: utf-8 -*-
"""Tests for NotificationService._record_general_delivery_receipts (Task 3)."""

from __future__ import annotations

from types import SimpleNamespace

from src.notification import (
    ChannelAttemptResult,
    NotificationDispatchResult,
    NotificationService,
)


def _dispatch(**kw):
    defaults = dict(
        dispatched=True,
        success=True,
        status="sent",
        channel_results=[
            ChannelAttemptResult(channel="telegram", success=True, latency_ms=50),
        ],
    )
    defaults.update(kw)
    return NotificationDispatchResult(**defaults)


def _make_service(monkeypatch, enabled=True):
    """Build a NotificationService without a full __init__ (avoids channel probes)."""
    svc = NotificationService.__new__(NotificationService)
    cfg = SimpleNamespace(notification_delivery_receipts_enabled=enabled)
    type(svc)._config = cfg
    monkeypatch.setattr(
        "src.notification.NotificationService._sanitize_notification_diagnostics",
        staticmethod(lambda s: str(s or "")),
    )
    return svc


def _install_fake_repo(monkeypatch):
    calls = []

    class FakeRepo:
        def __init__(self):
            pass

        def record_delivery(self, fields):
            calls.append(dict(fields))

    monkeypatch.setattr(
        "src.repositories.notification_delivery_repo.NotificationDeliveryRepository",
        lambda: FakeRepo(),
    )
    return calls


# ---- Behavior assertions -------------------------------------------------


def test_general_routes_record_delivery(monkeypatch):
    svc = _make_service(monkeypatch)
    calls = _install_fake_repo(monkeypatch)
    for route in (None, "report", "system_error"):
        calls.clear()
        svc._record_general_delivery_receipts(route, _dispatch())
        assert len(calls) == 1, f"route={route!r} should record one receipt"


def test_alert_routes_do_not_record(monkeypatch):
    svc = _make_service(monkeypatch)
    calls = _install_fake_repo(monkeypatch)
    for route in ("alert", "event"):
        calls.clear()
        svc._record_general_delivery_receipts(route, _dispatch())
        assert calls == [], f"route={route!r} must not record"


def test_disabled_config_does_not_record(monkeypatch):
    svc = _make_service(monkeypatch, enabled=False)
    calls = _install_fake_repo(monkeypatch)
    svc._record_general_delivery_receipts("report", _dispatch())
    assert calls == []


def test_no_real_channel_records_single_synthetic(monkeypatch):
    svc = _make_service(monkeypatch)
    calls = _install_fake_repo(monkeypatch)
    dispatch = _dispatch(
        status="noise_suppressed",
        channel_results=[ChannelAttemptResult(channel="__context__", success=True)],
    )
    svc._record_general_delivery_receipts("report", dispatch)
    assert len(calls) == 1
    assert calls[0]["channel"] == "__noise_suppressed__"
    # Also confirm a real channel is recorded once per attempt.
    calls.clear()
    svc._record_general_delivery_receipts("report", _dispatch())
    assert len(calls) == 1
    assert calls[0]["channel"] == "telegram"


def test_repo_failure_does_not_raise(monkeypatch):
    svc = _make_service(monkeypatch)

    def _raise_init():
        raise RuntimeError("boom init")

    monkeypatch.setattr(
        "src.repositories.notification_delivery_repo.NotificationDeliveryRepository",
        _raise_init,
    )
    dispatch = _dispatch()
    # Must not raise and must not alter dispatch semantics.
    svc._record_general_delivery_receipts("report", dispatch)
    assert dispatch.success is True
    assert dispatch.status == "sent"

    # write failure path
    class BrokenRepo:
        def __init__(self):
            pass

        def record_delivery(self, fields):
            raise RuntimeError("boom write")

    monkeypatch.setattr(
        "src.repositories.notification_delivery_repo.NotificationDeliveryRepository",
        lambda: BrokenRepo(),
    )
    svc._record_general_delivery_receipts("report", dispatch)
    assert dispatch.success is True
    assert dispatch.status == "sent"

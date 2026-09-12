# -*- coding: utf-8 -*-
"""Unit tests for the paper-trading fill notifier."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

import src.services.paper_notify as pn

_TRADE = SimpleNamespace(
    id=7,
    account_id=1,
    stock_code="600519",
    stock_name="贵州茅台",
    side="buy",
    quantity=2000.0,
    price=100.026,
    amount=200052.0,
    fee=52.0,
    trade_date=date(2026, 1, 5),
    reason="signal_action",
)


def test_build_message_marks_exit_reasons():
    assert pn.build_paper_fill_message(_TRADE, cash_after=799948.0).startswith("ℹ️ **模拟盘成交 | 600519 贵州茅台**")
    assert "买入开仓" in pn.build_paper_fill_message(_TRADE, cash_after=799948.0)
    assert "799,948.00" in pn.build_paper_fill_message(_TRADE, cash_after=799948.0)

    stop_loss = SimpleNamespace(**{**_TRADE.__dict__, "side": "sell", "reason": "stop_loss"})
    assert pn.build_paper_fill_message(stop_loss, cash_after=1.0).startswith("⚠️ ")
    assert "止损平仓" in pn.build_paper_fill_message(stop_loss, cash_after=1.0)

    take_profit = SimpleNamespace(**{**_TRADE.__dict__, "side": "sell", "reason": "take_profit"})
    assert pn.build_paper_fill_message(take_profit, cash_after=1.0).startswith("✅ ")
    assert "止盈平仓" in pn.build_paper_fill_message(take_profit, cash_after=1.0)


def test_build_message_distinguishes_add_and_reduce_by_disposition():
    # 加仓与首次开仓都用 side=buy 落库，减仓与清仓都用 side=sell：靠 disposition 区分。
    assert "加仓" in pn.build_paper_fill_message(_TRADE, cash_after=0.0, disposition="added")
    assert "买入开仓" in pn.build_paper_fill_message(_TRADE, cash_after=0.0, disposition="opened")

    sell = SimpleNamespace(**{**_TRADE.__dict__, "side": "sell"})
    assert "减仓卖出" in pn.build_paper_fill_message(sell, cash_after=0.0, disposition="reduced")
    assert "清仓卖出" in pn.build_paper_fill_message(sell, cash_after=0.0, disposition="closed")


def test_disabled_sends_nothing():
    svc = mock.Mock()
    with mock.patch.object(pn, "NotificationService", svc):
        assert pn.send_paper_fill_notification(_TRADE, cash_after=0.0, enabled=False) is False
    svc.assert_not_called()


def test_enabled_dispatches_on_event_route_with_dedup_key():
    sent = {}

    class _Svc:
        def send_with_results(self, content, **kw):
            sent["content"] = content
            sent["kw"] = kw
            return mock.Mock(success=True)

    with mock.patch.object(pn, "get_config", return_value=mock.Mock(paper_notify_enabled=True)):
        with mock.patch.object(pn, "NotificationService", return_value=_Svc()):
            ok = pn.send_paper_fill_notification(_TRADE, cash_after=799948.0, disposition="opened")

    assert ok is True
    assert sent["kw"]["route_type"] == "event"
    assert sent["kw"]["dedup_key"] == "paper-fill:1:7"
    assert "模拟盘成交" in sent["content"]


def test_reads_enabled_flag_from_config_when_not_passed():
    svc = mock.Mock()
    with mock.patch.object(pn, "get_config", return_value=mock.Mock(paper_notify_enabled=False)):
        with mock.patch.object(pn, "NotificationService", svc):
            assert pn.send_paper_fill_notification(_TRADE, cash_after=0.0) is False
    svc.assert_not_called()


def test_send_failure_returns_false_and_never_raises():
    with mock.patch.object(pn, "NotificationService", side_effect=RuntimeError("boom")):
        assert pn.send_paper_fill_notification(_TRADE, cash_after=0.0, enabled=True) is False


def test_unsuccessful_dispatch_returns_false():
    class _Svc:
        def send_with_results(self, content, **kw):
            return mock.Mock(success=False, status="all_failed")

    with mock.patch.object(pn, "NotificationService", return_value=_Svc()):
        assert pn.send_paper_fill_notification(_TRADE, cash_after=0.0, enabled=True) is False

# -*- coding: utf-8 -*-
"""Service tests for the paper-trading engine."""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import pytest

from src.config import Config
from src.services.paper_service import PaperService
from src.storage import DatabaseManager, DecisionSignalRecord, utc_naive_now


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "paper_service.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


@pytest.fixture()
def service(isolated_db):
    return PaperService(isolated_db)


def _seed_daily(db, code, d, o, h, l, c):
    df = pd.DataFrame([{
        "date": d,
        "open": o, "high": h, "low": l, "close": c,
        "volume": 0, "amount": 0.0, "pct_chg": 0.0,
        "ma5": c, "ma10": c, "ma20": c, "volume_ratio": 1.0,
    }])
    db.save_daily_data(df, code, "test")


def _make_signal(db, action="buy", code="600519", entry_high=100.0, stop_loss=95.0,
                 target_price=115.0, created_at=None, market="cn"):
    with db.get_session() as session:
        row = DecisionSignalRecord(
            stock_code=code,
            stock_name="贵州茅台",
            market=market,
            source_type="analysis",
            source_agent="test",
            source_report_id=1001,
            trace_id="trace-x",
            market_phase="intraday",
            trigger_source="api",
            action=action,
            action_label=action,
            confidence=0.8,
            score=88,
            horizon="3d",
            entry_low=98.0,
            entry_high=entry_high,
            stop_loss=stop_loss,
            target_price=target_price,
            status="active",
            created_at=created_at or utc_naive_now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def test_process_signal_opens_position(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)
    assert result["disposition"] == "opened"

    positions = service.get_positions(service.get_or_create_account()["account_id"])
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000  # 20% of 1e6 / 100
    assert open_pos["avg_cost"] == 100.0

    snapshot = service.get_snapshot(service.get_or_create_account()["account_id"])
    assert snapshot["cash"] == 800000.0


def test_process_signal_dedup(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    first = service.process_signal(sig.id)
    second = service.process_signal(sig.id)
    assert first["disposition"] == "opened"
    assert second["status"] == "skipped"


def test_hold_signal_ignored(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)
    assert result["disposition"] == "ignored"
    account_id = service.get_or_create_account()["account_id"]
    assert service.get_positions(account_id) == []


def test_sell_closes_position(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 105, 105, 105, 105)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    sell_sig = _make_signal(isolated_db, action="sell", created_at=datetime(2026, 1, 6))

    service.process_signal(buy_sig.id)
    result = service.process_signal(sell_sig.id)
    assert result["disposition"] == "closed"

    account_id = service.get_or_create_account()["account_id"]
    positions = service.get_positions(account_id)
    assert all(p["status"] == "closed" for p in positions)


def test_take_profit_exit(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)  # high touches target 115
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)

    snapshot = service.run_daily_valuation(account_id, as_of_date=d2)
    assert snapshot["trade_date"] == "2026-01-06"

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0]["reason"] == "take_profit"
    assert sell_trades[0]["price"] == 115.0

    positions = service.get_positions(account_id)
    assert all(p["status"] == "closed" for p in positions)


def test_stop_loss_precedence(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    # Both stop-loss and take-profit touched on D2 -> stop-loss wins.
    _seed_daily(isolated_db, "600519", d2, 90, 120, 90, 95)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           target_price=115.0, created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades[0]["reason"] == "ambiguous_stop_loss"
    assert sell_trades[0]["price"] == 95.0


def test_daily_valuation_idempotent(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(sig.id)

    s1 = service.run_daily_valuation(account_id, as_of_date=d1)
    s2 = service.run_daily_valuation(account_id, as_of_date=d1)  # idempotent
    assert s1["net_value"] == s2["net_value"]
    curve = service.get_equity_curve(account_id)
    assert len([p for p in curve if p["trade_date"] == "2026-01-05"]) == 1


def test_backfill_replays_signals(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.backfill_history(account_id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31))
    assert result["signals_replayed"] == 1

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000
    assert service.get_equity_curve(account_id)  # curve non-empty


def test_backfill_paginates_past_repo_cap(isolated_db, service):
    # The repo's list() caps page_size at 100; backfill must replay every signal
    # in the range, not silently drop the tail.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    for _ in range(150):
        _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.backfill_history(account_id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31))
    assert result["signals_replayed"] == 150


def test_add_capped_by_target_weight_and_cash(isolated_db, service):
    # Repeated buy/add signals must not push a single position past its target
    # weight nor spend more than the cash on hand.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)  # opens at ~20% of net value

    # Many more buy signals on the same name: the position is already at target, so
    # further adds are "hold" and cash stays put.
    for _ in range(20):
        add_sig = _make_signal(isolated_db, action="add", entry_high=100.0, created_at=datetime(2026, 1, 6))
        service.process_signal(add_sig.id)

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    # 2000 shares @ 100 = 200,000 == target (20% of 1,000,000); no further buys.
    assert open_pos["quantity"] == 2000
    assert service.get_snapshot(account_id)["cash"] == 800000.0


def test_add_updates_stop_and_target(isolated_db, service):
    # A buy/add signal can carry a revised stop-loss / take-profit; an add must
    # update those lines on the open position instead of leaving them pinned to open.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "AAPL", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "AAPL", d2, 97, 97, 97, 97)
    buy_sig = _make_signal(isolated_db, action="buy", code="AAPL", market="us",
                           entry_high=100.0, stop_loss=95.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    add_sig = _make_signal(isolated_db, action="add", code="AAPL", market="us",
                           entry_high=97.0, stop_loss=90.0, target_price=110.0,
                           created_at=datetime(2026, 1, 6))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)
    # Re-price the position at d2 (97) so its market value drops below the 20%
    # target; otherwise the position sits exactly at target and the add is capped.
    service.run_daily_valuation(account_id, as_of_date=d2)
    result = service.process_signal(add_sig.id)
    assert result["disposition"] == "added"

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] > 2000
    assert open_pos["stop_loss"] == 90.0
    assert open_pos["target_price"] == 110.0


def test_no_same_day_stop_out_on_entry(isolated_db, service):
    # Entry uses the day's high; the same day's low may dip below the stop-loss
    # without the position actually being stopped out. Exits only apply from the
    # day after entry.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    # D1: high 100 (entry), low 90 < stop 95 -> must NOT exit on the entry day.
    _seed_daily(isolated_db, "600519", d1, 100, 100, 90, 95)
    _seed_daily(isolated_db, "600519", d2, 90, 90, 90, 90)  # D2 breaches stop.
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)  # opens on D1; no same-day exit
    trades_after_open = service.get_trades(account_id)["items"]
    assert all(t["side"] == "buy" for t in trades_after_open)

    service.run_daily_valuation(account_id, as_of_date=d2)
    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades and sell_trades[0]["reason"] == "stop_loss"


def test_custom_position_weight(isolated_db):
    # A custom position weight must resize the opened position proportionally.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    service = PaperService(isolated_db, position_weight=0.5)

    service.process_signal(sig.id)
    account_id = service.get_or_create_account()["account_id"]
    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    # 50% of 1,000,000 / 100 = 5,000 shares (vs 2,000 at the default 20%).
    assert open_pos["quantity"] == 5000


def test_hk_lot_rounding(isolated_db, service):
    # HK trades in board lots too (approximated as 100), not plain integer shares.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "00700", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", code="00700", market="hk",
                       entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)
    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000  # 20% of 1e6 / 100, rounded to 100-lot
    assert open_pos["quantity"] % 100 == 0


def test_hold_signal_does_not_snapshot(isolated_db, service):
    # Dispositions that don't touch positions (hold/ignored) must not write a
    # redundant daily snapshot / pollute the equity curve.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)
    assert service.get_equity_curve(account_id) == []


def test_buy_without_price_is_data_unavailable_and_retryable(isolated_db, service):
    # A buy with no entry price and no price bar is data-unavailable, NOT consumed,
    # so a later run (e.g. after the data source recovers) can still fill it
    # instead of permanently dropping the signal.
    sig = _make_signal(isolated_db, action="buy", entry_high=None, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.process_signal(sig.id)
    assert result["status"] == "data_unavailable"
    assert result["disposition"] == "data_unavailable"

    # Not consumed: positions untouched and no signal record written.
    assert service.get_positions(account_id) == []
    assert service.get_signals(account_id)["total"] == 0

    # Once the price is available, a later run (fresh service instance, as the
    # background valuation task recreates it) can still consume the same signal.
    _seed_daily(isolated_db, "600519", date(2026, 1, 5), 100, 100, 100, 100)
    result2 = PaperService(isolated_db).process_signal(sig.id)
    assert result2["disposition"] == "opened"

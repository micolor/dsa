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

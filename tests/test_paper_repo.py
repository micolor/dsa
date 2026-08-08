# -*- coding: utf-8 -*-
"""Repository tests for paper_* tables."""

from __future__ import annotations

import os
from datetime import date

import pytest

from src.config import Config
from src.repositories.paper_repo import PaperRepository
from src.storage import DatabaseManager, DecisionSignalRecord, utc_naive_now


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "paper_repo.db"
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
def repo(isolated_db):
    return PaperRepository(isolated_db)


def _make_signal(db, stock_code="600519", action="buy", signal_id=None):
    with db.get_session() as session:
        row = DecisionSignalRecord(
            stock_code=stock_code,
            stock_name="贵州茅台",
            market="cn",
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
            entry_low=1680.0,
            entry_high=1700.0,
            stop_loss=1600.0,
            target_price=1850.0,
            status="active",
            created_at=utc_naive_now(),
        )
        if signal_id is not None:
            row.id = signal_id
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def test_ensure_account_creates_then_reuses(isolated_db, repo):
    acc1 = repo.ensure_account(initial_capital=500000.0)
    assert acc1.initial_capital == 500000.0
    assert acc1.cash == 500000.0

    acc2 = repo.ensure_account(initial_capital=999999.0)
    assert acc2.id == acc1.id  # reused, not duplicated
    assert acc2.initial_capital == 500000.0


def test_update_account(repo):
    acc = repo.ensure_account()
    updated = repo.update_account(acc.id, {"cash": 12345.0})
    assert updated.cash == 12345.0


def test_upsert_position_merges_same_stock(repo):
    acc = repo.ensure_account()
    p1 = repo.upsert_position(acc.id, "600519", {"quantity": 100.0, "avg_cost": 100.0})
    p2 = repo.upsert_position(acc.id, "600519", {"quantity": 200.0, "avg_cost": 105.0})
    assert p1.id == p2.id  # same open-position row merged
    assert p2.quantity == 200.0

    open_pos = repo.get_open_position(acc.id, "600519")
    assert open_pos is not None
    assert open_pos.status == "open"


def test_close_position(repo):
    acc = repo.ensure_account()
    repo.upsert_position(acc.id, "600519", {"quantity": 100.0, "avg_cost": 100.0})
    closed = repo.close_position(acc.id, "600519", {"current_price": 120.0})
    assert closed is not None
    assert closed.status == "closed"
    assert repo.get_open_position(acc.id, "600519") is None
    assert len(repo.list_open_positions(acc.id)) == 0


def test_add_trade_and_list(repo):
    acc = repo.ensure_account()
    t1 = repo.add_trade(acc.id, stock_code="600519", side="buy", quantity=100.0, price=100.0, amount=10000.0, trade_date=date(2026, 1, 5))
    t2 = repo.add_trade(acc.id, stock_code="600519", side="sell", quantity=100.0, price=120.0, amount=12000.0, trade_date=date(2026, 1, 10))
    assert t1.amount == 10000.0
    trades = repo.list_trades(acc.id)
    assert len(trades) == 2
    assert repo.count_trades(acc.id) == 2


def test_signal_record_dedup(repo):
    acc = repo.ensure_account()
    sig = _make_signal(repo.db)
    assert repo.has_signal_record(acc.id, sig.id) is False
    repo.add_signal_record(acc.id, sig.id, action="buy", disposition="opened")
    assert repo.has_signal_record(acc.id, sig.id) is True
    assert len(repo.list_signal_records(acc.id)) == 1
    assert repo.count_signal_records(acc.id) == 1


def test_add_snapshot_is_idempotent(repo):
    acc = repo.ensure_account()
    d = date(2026, 1, 5)
    repo.add_snapshot(acc.id, d, cash=900000.0, market_value=100000.0, net_value=1000000.0, return_pct=0.0)
    repo.add_snapshot(acc.id, d, cash=880000.0, market_value=120000.0, net_value=1000000.0, return_pct=0.0)
    snaps = repo.list_snapshots(acc.id)
    assert len(snaps) == 1  # same date updated, not duplicated
    assert snaps[0].market_value == 120000.0

    repo.add_snapshot(acc.id, date(2026, 1, 6), cash=900000.0, market_value=100000.0, net_value=1000000.0, return_pct=0.0)
    assert len(repo.list_snapshots(acc.id)) == 2
    assert repo.has_snapshot(acc.id, d) is True

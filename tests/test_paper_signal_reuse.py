# -*- coding: utf-8 -*-
"""决策信号 id 复用对模拟盘的影响（回归测试）。

背景：``decision_signals.id`` 在 SQLite 上是 ``INTEGER PRIMARY KEY``，没开
AUTOINCREMENT，历史记录被删除释放 id 后新信号会复用同一个 id；而 ``paper_signals``
的唯一键是 (account_id, signal_id)，只看 id 会把复用 id 的新信号当成「已处理」永久跳过，
表现为「模拟盘一直不成交」。这里锁定三层保护：

1. 新库 ``decision_signals`` 带 AUTOINCREMENT，id 不再复用；
2. 删除历史时级联清理 ``paper_signals``，不留孤儿消费记录（见 test_analysis_history）；
3. 判重时同时比对 action：action 不一致说明旧记录属于另一条已删除信号，按新信号处理。
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from src.config import Config
from src.services.paper_service import PaperService, clear_bar_cache_for_tests
from src.storage import (
    DatabaseManager,
    DecisionSignalRecord,
    PaperSignalRecord,
    utc_naive_now,
)


def setup_function(_function):
    clear_bar_cache_for_tests()


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "paper_signal_reuse.db")
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


def _add_signal(db, *, signal_id: int, action: str = "buy", code: str = "600519"):
    with db.get_session() as session:
        row = DecisionSignalRecord(
            id=signal_id,
            stock_code=code,
            stock_name="贵州茅台",
            market="cn",
            source_type="analysis",
            source_agent="test",
            source_report_id=1001,
            trace_id="trace-reuse",
            market_phase="intraday",
            trigger_source="api",
            action=action,
            action_label=action,
            confidence=0.8,
            score=88,
            horizon="3d",
            entry_low=98.0,
            entry_high=100.0,
            stop_loss=95.0,
            target_price=115.0,
            status="active",
            created_at=datetime(2026, 1, 5),
        )
        session.add(row)
        session.commit()
    return signal_id


def test_fresh_database_marks_decision_signals_autoincrement(isolated_db) -> None:
    with isolated_db.get_session() as session:
        ddl = session.execute(
            text("select sql from sqlite_master where type='table' and name='decision_signals'")
        ).scalar_one()

    assert "AUTOINCREMENT" in ddl.upper()


def test_recycled_signal_id_is_processed_again(isolated_db, service) -> None:
    """旧记录属于另一条已删除信号（action 不同）时不能挡住新信号。"""
    _seed_daily(isolated_db, "600519", date(2026, 1, 5), 100, 100, 100, 100)
    account_id = service.get_or_create_account()["account_id"]

    with isolated_db.get_session() as session:
        session.add(
            PaperSignalRecord(
                account_id=account_id,
                signal_id=42,
                action="hold",
                disposition="ignored",
                processed_at=utc_naive_now(),
            )
        )
        session.commit()

    _add_signal(isolated_db, signal_id=42, action="buy")

    result = service.process_signal(42)

    assert result["status"] == "processed"
    assert result["disposition"] == "opened"
    positions = service.get_positions(account_id)
    assert any(p["status"] == "open" for p in positions)
    # 旧记录被替换成新信号的消费记录，不再是孤儿
    with isolated_db.get_session() as session:
        records = session.query(PaperSignalRecord).filter(PaperSignalRecord.signal_id == 42).all()
    assert len(records) == 1
    assert records[0].action == "buy"


def test_same_signal_action_is_still_deduped(isolated_db, service) -> None:
    """同一 (account, signal) + 同一 action 仍然只成交一次。"""
    _seed_daily(isolated_db, "600519", date(2026, 1, 5), 100, 100, 100, 100)
    _add_signal(isolated_db, signal_id=43, action="buy")

    first = service.process_signal(43)
    second = service.process_signal(43)

    assert first["status"] == "processed"
    assert second["status"] == "skipped"

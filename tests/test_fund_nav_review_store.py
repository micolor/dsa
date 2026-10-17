# -*- coding: utf-8 -*-
"""Storage-level tests for fund NAV review anchors."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pytest

from data_provider.fund_fetcher import FundProfile, NavRecord
from src.config import Config
from src.services.fund_nav_review_service import (
    FUND_NAV_REVIEW_DUE_DAYS,
    FUND_NAV_REVIEW_ENGINE_VERSION,
    FUND_NAV_REVIEW_HORIZONS,
    FundNavReviewService,
)
from src.storage import DatabaseManager, FundNavReviewRecord


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "fund_nav_review.db")
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


def _profile(code="003095", nav="1.2345", day="2024-01-02", name="中欧医疗健康混合A"):
    return FundProfile(
        code=code,
        name=name,
        nav_history=[NavRecord(date=day, unit_nav=float(nav), acc_nav=float(nav), change_pct=0.1)],
    )


def _rows(db):
    with db.get_session() as session:
        return session.query(FundNavReviewRecord).order_by(FundNavReviewRecord.id).all()


def test_record_anchor_creates_one_row_per_horizon(isolated_db):
    result = FundNavReviewService().record_anchor(_profile(), analysis_history_id=7)

    rows = _rows(isolated_db)
    assert result["created"] == 4
    assert len(rows) == 4
    assert {row.horizon for row in rows} == set(FUND_NAV_REVIEW_HORIZONS)
    assert all(row.engine_version == FUND_NAV_REVIEW_ENGINE_VERSION for row in rows)
    assert all(row.anchor_date == date(2024, 1, 2) for row in rows)
    assert all(row.anchor_nav == pytest.approx(1.2345) for row in rows)
    assert all(row.analysis_history_id == 7 for row in rows)
    for row in rows:
        assert row.eval_status == "pending"
        assert row.due_date == date(2024, 1, 2) + timedelta(days=FUND_NAV_REVIEW_DUE_DAYS[row.horizon])


def test_record_anchor_is_idempotent_for_same_nav_date(isolated_db):
    service = FundNavReviewService()
    first = service.record_anchor(_profile(), analysis_history_id=7, source="pipeline")
    second = service.record_anchor(_profile(), analysis_history_id=99, source="backfill")

    rows = _rows(isolated_db)
    assert first["created"] == 4
    assert second["created"] == 0
    assert second["skipped"] == 4
    assert len(rows) == 4
    assert all(row.analysis_history_id == 7 for row in rows)


def test_record_anchor_accepts_prefixed_and_uppercase_code(isolated_db):
    FundNavReviewService().record_anchor(_profile(code="FUND:003095"))
    rows = _rows(isolated_db)
    assert len(rows) == 4
    assert all(row.fund_code == "003095" for row in rows)


def test_record_anchor_rejects_non_fund_code_and_invalid_nav(isolated_db):
    service = FundNavReviewService()
    assert service.record_anchor(_profile(code="abc123"))["created"] == 0
    assert service.record_anchor(_profile(nav="0"))["created"] == 0
    assert service.record_anchor(FundProfile(code="003095", name="x", nav_history=[]))["created"] == 0
    assert _rows(isolated_db) == []


def test_anchor_does_not_touch_other_tables(isolated_db):
    from src.storage import AnalysisHistory

    FundNavReviewService().record_anchor(_profile())
    with isolated_db.get_session() as session:
        assert session.query(AnalysisHistory).count() == 0


def test_restore_columns_default_to_null(isolated_db):
    FundNavReviewService().record_anchor(_profile())

    for row in _rows(isolated_db):
        assert row.anchor_acc_nav == pytest.approx(1.2345)
        assert row.eval_acc_nav is None
        assert row.acc_nav_return_pct is None
        assert row.acc_max_drawdown_pct is None
        assert row.restored_return_pct is None
        assert row.restored_max_drawdown_pct is None
        assert row.restored_basis is None
        assert row.restored_reason is None
        assert row.restored_events is None
        assert row.restored_checked_at is None


def test_update_review_persists_restore_fields(isolated_db):
    from src.repositories.fund_nav_review_repo import FundNavReviewRepository

    FundNavReviewService().record_anchor(_profile())
    row = _rows(isolated_db)[0]
    checked_at = datetime(2026, 10, 17, 8, 30)

    updated = FundNavReviewRepository(isolated_db).update_review(
        review_id=row.id,
        fields={
            "anchor_acc_nav": 1.5,
            "eval_acc_nav": 1.65,
            "acc_nav_return_pct": 10.0,
            "acc_max_drawdown_pct": -3.2,
            "restored_return_pct": 12.5,
            "restored_max_drawdown_pct": -2.8,
            "restored_basis": "dividend_reinvested",
            "restored_reason": None,
            "restored_events": 2,
            "restored_checked_at": checked_at,
        },
    )

    refreshed = _rows(isolated_db)[0]
    assert updated is True
    assert refreshed.anchor_acc_nav == pytest.approx(1.5)
    assert refreshed.eval_acc_nav == pytest.approx(1.65)
    assert refreshed.acc_nav_return_pct == pytest.approx(10.0)
    assert refreshed.acc_max_drawdown_pct == pytest.approx(-3.2)
    assert refreshed.restored_return_pct == pytest.approx(12.5)
    assert refreshed.restored_max_drawdown_pct == pytest.approx(-2.8)
    assert refreshed.restored_basis == "dividend_reinvested"
    assert refreshed.restored_reason is None
    assert refreshed.restored_events == 2
    assert refreshed.restored_checked_at == checked_at


def _mark_evaluated(db, *, checked_at=None):
    from src.repositories.fund_nav_review_repo import FundNavReviewRepository

    repo = FundNavReviewRepository(db)
    for row in _rows(db):
        repo.update_review(
            review_id=row.id,
            fields={
                "eval_status": "evaluated",
                "eval_date": row.anchor_date,
                "eval_nav": row.anchor_nav,
                "checked_at": checked_at,
            },
        )


def test_list_restore_backlog_selects_only_unchecked_evaluated_rows(isolated_db):
    from src.repositories.fund_nav_review_repo import FundNavReviewRepository

    service = FundNavReviewService()
    service.record_anchor(_profile(day="2024-01-02"))
    service.record_anchor(_profile(code="006229", day="2024-02-02"))
    _mark_evaluated(isolated_db)

    repo = FundNavReviewRepository(isolated_db)
    backlog = repo.list_restore_backlog(engine_version=FUND_NAV_REVIEW_ENGINE_VERSION)
    assert len(backlog) == 8
    assert all(row.eval_status == "evaluated" for row in backlog)
    assert all(row.restored_checked_at is None for row in backlog)

    repo.update_review(
        review_id=backlog[0].id,
        fields={"restored_checked_at": datetime(2026, 10, 17, 9, 0)},
    )
    remaining = repo.list_restore_backlog(engine_version=FUND_NAV_REVIEW_ENGINE_VERSION)
    assert len(remaining) == 7
    assert backlog[0].id not in {row.id for row in remaining}

    refreshed = repo.list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION, force=True
    )
    assert len(refreshed) == 8


def test_list_restore_backlog_filters_by_fund_and_anchor_range(isolated_db):
    from src.repositories.fund_nav_review_repo import FundNavReviewRepository

    service = FundNavReviewService()
    service.record_anchor(_profile(day="2024-01-02"))
    service.record_anchor(_profile(code="006229", day="2024-02-02"))
    _mark_evaluated(isolated_db)

    repo = FundNavReviewRepository(isolated_db)
    by_fund = repo.list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION, fund_code="006229"
    )
    assert len(by_fund) == 4
    assert {row.fund_code for row in by_fund} == {"006229"}

    by_range = repo.list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
        anchor_from=date(2024, 2, 1),
    )
    assert len(by_range) == 4
    assert {row.anchor_date for row in by_range} == {date(2024, 2, 2)}

    by_horizon = repo.list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION, horizon="1m"
    )
    assert len(by_horizon) == 2
    assert {row.horizon for row in by_horizon} == {"1m"}


def test_list_restore_backlog_skips_pending_rows(isolated_db):
    from src.repositories.fund_nav_review_repo import FundNavReviewRepository

    FundNavReviewService().record_anchor(_profile())
    backlog = FundNavReviewRepository(isolated_db).list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION
    )
    assert backlog == []

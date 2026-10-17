# -*- coding: utf-8 -*-
"""Storage-level tests for fund NAV review anchors."""

from __future__ import annotations

import os
from datetime import date, timedelta

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

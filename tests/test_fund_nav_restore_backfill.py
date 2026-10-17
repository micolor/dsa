# -*- coding: utf-8 -*-
"""Restore-metric backfill tests for existing fund NAV reviews."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from data_provider.fund_fetcher import (
    FundDistributionEvents,
    FundDividendEvent,
    FundProfile,
    NavRecord,
)
from src.config import Config
from src.repositories.fund_nav_review_repo import FundNavReviewRepository
from src.services.fund_nav_review_service import (
    FUND_NAV_REVIEW_ENGINE_VERSION,
    FUND_NAV_REVIEW_HORIZONS,
    FundNavReviewService,
)
from src.storage import DatabaseManager, FundNavReviewRecord

ANCHOR_DAY = date(2025, 1, 2)


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "fund_nav_restore_backfill.db")
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


def _navs():
    rows = [
        NavRecord(date=ANCHOR_DAY.isoformat(), unit_nav=1.00, acc_nav=1.10, change_pct=0.0),
        NavRecord(
            date=(ANCHOR_DAY + timedelta(days=1)).isoformat(),
            unit_nav=0.90,
            acc_nav=1.10,
            change_pct=0.0,
        ),
    ]
    for index in range(2, 30):
        rows.append(
            NavRecord(
                date=(ANCHOR_DAY + timedelta(days=index)).isoformat(),
                unit_nav=0.99,
                acc_nav=1.10,
                change_pct=0.0,
            )
        )
    return rows


class FakeFetcher:
    def __init__(self, navs, events=None, events_error=None):
        self.navs = navs
        self.events = events if events is not None else FundDistributionEvents()
        self.events_error = events_error
        self.calls = []

    def get_nav_range(self, code, start_date, end_date):
        self.calls.append((code, start_date, end_date))
        return list(self.navs)

    def get_distribution_events(self, code):
        if self.events_error is not None:
            raise self.events_error
        return self.events


def _evaluated_row(db, navs, *, eval_date=None):
    """Create one legacy-style evaluated row with no restore metrics."""
    FundNavReviewService().record_anchor(
        FundProfile(code="003095", name="测试基金", nav_history=[navs[0]])
    )
    with db.get_session() as session:
        row = (
            session.query(FundNavReviewRecord)
            .filter(FundNavReviewRecord.horizon == "1m")
            .one()
        )
        row.eval_status = "evaluated"
        row.eval_date = eval_date or ANCHOR_DAY + timedelta(days=22)
        row.eval_nav = 0.99
        row.nav_return_pct = -1.0
        row.max_drawdown_pct = -1.0
        row.eval_window_records = FUND_NAV_REVIEW_HORIZONS["1m"]
        row.restored_basis = None
        row.restored_checked_at = None
        session.commit()
        return row.id


def _reload(db, row_id):
    with db.get_session() as session:
        return session.get(FundNavReviewRecord, row_id)


def test_backfill_restore_metrics_dry_run_does_not_write(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)
    fetcher = FakeFetcher(
        navs,
        events=FundDistributionEvents(
            dividends=[FundDividendEvent((ANCHOR_DAY + timedelta(days=1)).isoformat(), 0.10)]
        ),
    )

    result = FundNavReviewService(fetcher=fetcher).backfill_restored_metrics(dry_run=True)

    assert result["dry_run"] is True
    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert _reload(isolated_db, row_id).restored_basis is None
    assert _reload(isolated_db, row_id).restored_checked_at is None


def test_backfill_restore_metrics_updates_idempotently(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)
    fetcher = FakeFetcher(
        navs,
        events=FundDistributionEvents(
            dividends=[FundDividendEvent((ANCHOR_DAY + timedelta(days=1)).isoformat(), 0.10)]
        ),
    )
    service = FundNavReviewService(fetcher=fetcher)

    first = service.backfill_restored_metrics(dry_run=False)
    second = service.backfill_restored_metrics(dry_run=False)

    assert first["updated"] == 1
    assert second["scanned"] == 0
    row = _reload(isolated_db, row_id)
    assert row.restored_basis == "dividend_reinvested"
    assert row.restored_return_pct == pytest.approx(10.0)
    assert row.restored_events == 1
    assert row.restored_checked_at is not None
    assert row.nav_return_pct == pytest.approx(-1.0)


def test_backfill_marks_window_mismatch(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(
        isolated_db,
        navs,
        eval_date=ANCHOR_DAY + timedelta(days=23),
    )

    result = FundNavReviewService(fetcher=FakeFetcher(navs)).backfill_restored_metrics(
        dry_run=False
    )

    assert result["skipped"] == 1
    assert result["updated"] == 0
    row = _reload(isolated_db, row_id)
    assert row.restored_basis is None
    assert row.restored_checked_at is None


def test_backfill_force_recomputes_checked_rows(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)
    service = FundNavReviewService(fetcher=FakeFetcher(navs))

    service.backfill_restored_metrics(dry_run=False)
    forced = service.backfill_restored_metrics(dry_run=False, force=True)

    assert forced["scanned"] == 1
    assert forced["updated"] == 1
    assert _reload(isolated_db, row_id).restored_basis == "dividend_reinvested"


def test_event_fetch_failure_falls_back_to_acc_nav(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)
    fetcher = FakeFetcher(navs, events_error=RuntimeError("boom"))

    result = FundNavReviewService(fetcher=fetcher).backfill_restored_metrics(dry_run=False)

    assert result["updated"] == 1
    row = _reload(isolated_db, row_id)
    assert row.restored_basis == "acc_nav"
    assert row.restored_reason == "distribution_fetch_error"
    assert row.acc_nav_return_pct == pytest.approx(0.0)


def test_backfill_skips_when_fund_nav_is_missing(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)

    result = FundNavReviewService(fetcher=FakeFetcher([])).backfill_restored_metrics(
        dry_run=False
    )

    assert result["skipped"] == 1
    assert _reload(isolated_db, row_id).restored_basis is None


def test_backfill_rejects_invalid_fund_code(isolated_db):
    with pytest.raises(ValueError):
        FundNavReviewService(fetcher=FakeFetcher([])).backfill_restored_metrics(
            fund_code="abc123"
        )


def test_backfill_filters_by_fund_code_and_anchor_range(isolated_db):
    navs = _navs()
    _evaluated_row(isolated_db, navs)

    service = FundNavReviewService(fetcher=FakeFetcher(navs))
    other_fund = service.backfill_restored_metrics(fund_code="006229", dry_run=True)
    before_range = service.backfill_restored_metrics(
        anchor_from=date(2026, 1, 1), dry_run=True
    )

    assert other_fund["scanned"] == 0
    assert before_range["scanned"] == 0


def test_backfill_rows_stay_unchecked_when_unavailable(isolated_db):
    navs = _navs()
    row_id = _evaluated_row(isolated_db, navs)
    # Invalid window values make the adjusted series unusable, then acc_nav is
    # also unusable, leaving restored_basis empty.
    broken = [
        NavRecord(date=record.date, unit_nav=0.0, acc_nav=0.0, change_pct=0.0)
        for record in navs
    ]

    result = FundNavReviewService(fetcher=FakeFetcher(broken)).backfill_restored_metrics(
        dry_run=False
    )

    assert result["unavailable"] == 1
    assert _reload(isolated_db, row_id).restored_checked_at is None


def test_repository_backlog_uses_engine_version(isolated_db):
    navs = _navs()
    _evaluated_row(isolated_db, navs)
    repo = FundNavReviewRepository(isolated_db)

    assert len(repo.list_restore_backlog(engine_version=FUND_NAV_REVIEW_ENGINE_VERSION)) == 1
    assert repo.list_restore_backlog(engine_version="fund-nav-v0") == []

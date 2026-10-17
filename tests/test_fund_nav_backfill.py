# -*- coding: utf-8 -*-
"""Tests for backfilling fund NAV review anchors from analysis history."""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import pytest

from data_provider.fund_fetcher import NavRecord
from src.config import Config
from src.services.fund_nav_review_service import FundNavReviewService
from src.storage import AnalysisHistory, DatabaseManager, FundNavReviewRecord


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "fund_nav_backfill.db")
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


class FakeFetcher:
    def __init__(self, navs=None, error=None):
        self.navs = navs or []
        self.error = error
        self.calls = []

    def get_nav_range(self, code, start_date, end_date):
        self.calls.append((code, start_date, end_date))
        if self.error is not None:
            raise self.error
        return list(self.navs)


def _add_history(
    db,
    *,
    code="003095",
    name="中欧医疗健康混合A",
    day=date(2024, 1, 2),
    latest_nav=1.2345,
    latest_nav_date=None,
):
    dashboard = {"report_type": "fund", "latest_nav": latest_nav}
    if latest_nav_date is not None:
        dashboard["latest_nav_date"] = latest_nav_date
    with db.session_scope() as session:
        row = AnalysisHistory(
            query_id="q-fund",
            code=code,
            name=name,
            report_type="fund",
            raw_result=json.dumps({"dashboard": dashboard}),
            created_at=datetime(day.year, day.month, day.day, 18, 0, 0),
        )
        session.add(row)
        session.flush()
        return int(row.id)


def _rows(db):
    with db.get_session() as session:
        return session.query(FundNavReviewRecord).order_by(FundNavReviewRecord.id).all()


def test_new_report_backfills_offline_and_dry_run_writes_nothing(isolated_db):
    _add_history(isolated_db, latest_nav_date="2023-12-29", latest_nav=1.2)
    fetcher = FakeFetcher()

    preview = FundNavReviewService(fetcher=fetcher).backfill_from_history()

    assert preview["dry_run"] is True
    assert preview["created"] == 4
    assert fetch_calls(fetcher) == 0
    assert _rows(isolated_db) == []

    applied = FundNavReviewService(fetcher=fetcher).backfill_from_history(dry_run=False)

    assert applied["created"] == 4
    rows = _rows(isolated_db)
    assert all(row.anchor_date == date(2023, 12, 29) for row in rows)
    assert all(row.anchor_source == "backfill" for row in rows)


def test_backfill_is_idempotent_per_fund_and_anchor_date(isolated_db):
    _add_history(isolated_db, latest_nav_date="2023-12-29", latest_nav=1.2)
    service = FundNavReviewService(fetcher=FakeFetcher())
    service.backfill_from_history(dry_run=False)

    second = service.backfill_from_history(dry_run=False)

    assert second["created"] == 0
    assert second["skipped"] == 4
    assert len(_rows(isolated_db)) == 4


def test_legacy_report_requires_resolve_missing(isolated_db):
    _add_history(isolated_db, latest_nav=1.2345)
    fetcher = FakeFetcher()

    result = FundNavReviewService(fetcher=fetcher).backfill_from_history(dry_run=False)

    assert result["unmatched"] == 1
    assert result["created"] == 0
    assert fetch_calls(fetcher) == 0
    assert _rows(isolated_db) == []


def test_legacy_report_matches_nav_value_within_created_date(isolated_db):
    _add_history(isolated_db, day=date(2024, 1, 4), latest_nav=1.2345)
    fetcher = FakeFetcher(navs=[
        NavRecord("2024-01-02", 1.2000, 1.2, 0.0),
        NavRecord("2024-01-03", 1.2345, 1.2345, 0.0),
        NavRecord("2024-01-04", 1.4000, 1.4, 0.0),
    ])

    result = FundNavReviewService(fetcher=fetcher).backfill_from_history(
        dry_run=False,
        resolve_missing=True,
    )

    assert result["created"] == 4
    rows = _rows(isolated_db)
    assert all(row.anchor_date == date(2024, 1, 3) for row in rows)
    assert all(row.anchor_nav == pytest.approx(1.2345) for row in rows)
    assert fetch_calls(fetcher) == 1


def test_legacy_report_is_unmatched_when_no_nav_value_matches(isolated_db):
    _add_history(isolated_db, day=date(2024, 1, 4), latest_nav=1.2345)
    fetcher = FakeFetcher(navs=[NavRecord("2024-01-03", 1.1000, 1.1, 0.0)])

    result = FundNavReviewService(fetcher=fetcher).backfill_from_history(
        dry_run=False,
        resolve_missing=True,
    )

    assert result["unmatched"] == 1
    assert result["created"] == 0
    assert _rows(isolated_db) == []


def test_backfill_fetch_failure_counts_unmatched_and_does_not_raise(isolated_db):
    _add_history(isolated_db, latest_nav=1.2345)
    fetcher = FakeFetcher(error=RuntimeError("network down"))

    result = FundNavReviewService(fetcher=fetcher).backfill_from_history(
        dry_run=False,
        resolve_missing=True,
    )

    assert result["unmatched"] == 1
    assert _rows(isolated_db) == []


def test_backfill_can_filter_by_fund_code(isolated_db):
    _add_history(isolated_db, code="003095", latest_nav_date="2023-12-29", latest_nav=1.2)
    _add_history(isolated_db, code="000001", latest_nav_date="2023-12-29", latest_nav=1.5)

    result = FundNavReviewService(fetcher=FakeFetcher()).backfill_from_history(
        fund_code="fund:000001",
        dry_run=False,
    )

    assert result["funds"] == 1
    assert {row.fund_code for row in _rows(isolated_db)} == {"000001"}


def fetch_calls(fetcher):
    return len(fetcher.calls)

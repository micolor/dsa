# -*- coding: utf-8 -*-
"""Service tests for fund NAV forward review."""

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
from src.services.fund_nav_review_service import FundNavReviewService
from src.storage import DatabaseManager, FundNavReviewRecord

ANCHOR_DAY = date(2024, 1, 2)


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "fund_nav_review_service.db")
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
    def __init__(self, navs=None, error=None, events=None, events_error=None):
        self.navs = navs or []
        self.error = error
        self.events = events if events is not None else FundDistributionEvents()
        self.events_error = events_error
        self.calls = []
        self.event_calls = []

    def get_nav_range(self, code, start_date, end_date):
        self.calls.append((code, start_date, end_date))
        if self.error is not None:
            raise self.error
        return list(self.navs)

    def get_distribution_events(self, code):
        self.event_calls.append(code)
        if self.events_error is not None:
            raise self.events_error
        return self.events


def _navs(count=300, start=ANCHOR_DAY, base=1.0, step=0.01):
    return [
        NavRecord(
            date=(start + timedelta(days=index)).isoformat(),
            unit_nav=round(base + step * index, 6),
            acc_nav=round(base + step * index, 6),
            change_pct=0.0,
        )
        for index in range(count)
    ]


def _profile(navs):
    return FundProfile(code="003095", name="中欧医疗健康混合A", nav_history=[navs[0]])


def _rows(db):
    with db.get_session() as session:
        return session.query(FundNavReviewRecord).order_by(FundNavReviewRecord.id).all()


def _set_due(db, due_date):
    with db.get_session() as session:
        for row in session.query(FundNavReviewRecord).all():
            row.due_date = due_date
        session.commit()


def _record(navs):
    return FundNavReviewService().record_anchor(_profile(navs))


def test_evaluates_all_horizons_at_exact_record_offsets(isolated_db):
    navs = _navs()
    _record(navs)
    fetcher = FakeFetcher(navs)

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    assert result["evaluated"] == 4
    assert len(fetcher.calls) == 1  # one NAV range fetch per fund per run
    rows = {row.horizon: row for row in _rows(isolated_db)}
    assert rows["1m"].eval_window_records == 22
    assert rows["1m"].eval_date == ANCHOR_DAY + timedelta(days=22)
    assert rows["1m"].nav_return_pct == pytest.approx(22.0)
    assert rows["3m"].nav_return_pct == pytest.approx(66.0)
    assert rows["6m"].nav_return_pct == pytest.approx(126.0)
    assert rows["1y"].nav_return_pct == pytest.approx(252.0)
    assert all(row.eval_status == "evaluated" for row in rows.values())


def test_max_drawdown_covers_the_anchor_to_eval_window(isolated_db):
    navs = _navs(count=30)
    navs[5] = NavRecord(date=navs[5].date, unit_nav=0.8, acc_nav=0.8, change_pct=0.0)
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(navs)

    FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=40),
    )

    row = next(row for row in _rows(isolated_db) if row.horizon == "1m")
    assert row.eval_status == "evaluated"
    # 峰值在下跌前的 1.04，最低 0.8 → 0.8 / 1.04 - 1 = -23.0769%
    assert row.max_drawdown_pct == pytest.approx(-23.0769)


def test_waits_when_forward_records_are_insufficient(isolated_db):
    navs = _navs(count=10)
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(navs)

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=10),
    )

    assert result["pending"] == 4
    rows = _rows(isolated_db)
    assert all(row.eval_status == "pending" for row in rows)
    assert all(row.last_error == "waiting_for_records" for row in rows)
    assert all(row.checked_at is not None for row in rows)


def test_marks_delisted_or_suspended_when_nav_stops_updating(isolated_db):
    navs = _navs(count=10)
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(navs)

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=100),
    )

    assert result["unable"] == 4
    rows = _rows(isolated_db)
    assert all(row.eval_status == "unable" for row in rows)
    assert all(row.unable_reason == "fund_delisted_or_suspended" for row in rows)


def test_fetch_error_keeps_rows_pending_and_records_attempt(isolated_db):
    navs = _navs()
    _record(navs)
    fetcher = FakeFetcher(error=RuntimeError("network down"))

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    assert result["skipped"] == 4
    rows = _rows(isolated_db)
    assert all(row.eval_status == "pending" for row in rows)
    assert all(row.last_error == "fetch_error" for row in rows)
    assert all(row.attempts == 1 for row in rows)


def test_missing_anchor_date_does_not_shift_the_window(isolated_db):
    navs = _navs()
    _record(navs)
    fetch_navs = navs[1:]
    fetcher = FakeFetcher(fetch_navs)

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    assert result["pending"] == 4
    rows = _rows(isolated_db)
    assert all(row.last_error == "anchor_not_found" for row in rows)
    assert all(row.nav_return_pct is None for row in rows)


def test_invalid_anchor_nav_becomes_unable(isolated_db):
    navs = _navs()
    _record(navs)
    with isolated_db.get_session() as session:
        for row in session.query(FundNavReviewRecord).all():
            row.anchor_nav = 0
        session.commit()

    result = FundNavReviewService(fetcher=FakeFetcher(navs)).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    assert result["unable"] == 4
    rows = _rows(isolated_db)
    assert all(row.unable_reason == "invalid_anchor_nav" for row in rows)


def test_stats_aggregates_months_horizons_and_positive_rate(isolated_db):
    navs = _navs()
    _record(navs)
    FundNavReviewService(fetcher=FakeFetcher(navs)).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    stats = FundNavReviewService().get_stats()

    assert stats["total"] == 4
    assert stats["anchor_count"] == 1
    assert stats["evaluated"] == 4
    assert stats["positive_rate_pct"] == pytest.approx(100.0)
    assert stats["avg_nav_return_pct"] == pytest.approx(116.5)
    assert stats["avg_max_drawdown_pct"] == pytest.approx(0.0)
    assert [bucket["value"] for bucket in stats["breakdowns"]["horizon"]] == ["1m", "3m", "6m", "1y"]
    assert stats["breakdowns"]["month"][0]["value"] == "2024-01"
    assert stats["breakdowns"]["month"][0]["anchor_count"] == 1


def test_stats_returns_null_rates_when_nothing_is_evaluated(isolated_db):
    navs = _navs()
    _record(navs)

    stats = FundNavReviewService().get_stats()

    assert stats["evaluated"] == 0
    assert stats["positive_rate_pct"] is None
    assert stats["avg_nav_return_pct"] is None
    assert stats["avg_max_drawdown_pct"] is None


def test_list_reviews_filters_and_paginates(isolated_db):
    _record(_navs())
    service = FundNavReviewService()

    page = service.list_reviews(fund_code="fund:003095", horizon="1m", page_size=1)
    assert page["total"] == 1
    assert page["items"][0]["horizon"] == "1m"
    assert service.list_reviews(horizon="1y")["total"] == 1
    assert service.list_reviews(eval_status="evaluated")["total"] == 0
    with pytest.raises(ValueError):
        service.list_reviews(fund_code="abc123")


def _dividend_navs():
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


def test_evaluate_row_writes_dividend_reinvested_metrics(isolated_db):
    navs = _dividend_navs()
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(
        navs,
        events=FundDistributionEvents(
            dividends=[FundDividendEvent((ANCHOR_DAY + timedelta(days=1)).isoformat(), 0.10)]
        ),
    )

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=40),
    )

    item = result["items"][0]
    assert item["restored_basis"] == "dividend_reinvested"
    assert item["restored_return_pct"] == pytest.approx(10.0)
    assert item["nav_return_pct"] == pytest.approx(-1.0)
    assert item["restored_events"] == 1
    assert item["anchor_acc_nav"] == pytest.approx(1.10)
    assert item["eval_acc_nav"] == pytest.approx(1.10)
    assert item["acc_nav_return_pct"] == pytest.approx(0.0)
    assert fetcher.event_calls == ["003095"]


def test_event_fetch_failure_does_not_block_evaluation(isolated_db):
    navs = _dividend_navs()
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(navs, events_error=RuntimeError("boom"))

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=40),
    )

    items = {item["horizon"]: item for item in result["items"]}
    assert result["evaluated"] == 1
    assert result["pending"] == 3
    item = items["1m"]
    assert item["eval_status"] == "evaluated"
    assert item["restored_basis"] == "acc_nav"
    assert item["restored_reason"] == "distribution_fetch_error"
    assert item["restored_return_pct"] == pytest.approx(0.0)


def test_event_fetch_failure_falls_back_to_unit_nav_without_acc_nav(isolated_db):
    navs = [
        NavRecord(date=record.date, unit_nav=record.unit_nav, acc_nav=0.0, change_pct=0.0)
        for record in _navs()
    ]
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(navs, events_error=RuntimeError("boom"))

    result = FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )

    item = next(item for item in result["items"] if item["horizon"] == "1m")
    assert item["restored_basis"] == "unit_nav"
    assert item["restored_reason"] == "distribution_fetch_error"
    assert item["restored_return_pct"] == pytest.approx(item["nav_return_pct"])


def test_record_anchor_stores_anchor_acc_nav(isolated_db):
    navs = _dividend_navs()
    _record(navs)

    row = _rows(isolated_db)[0]
    assert row.anchor_acc_nav == pytest.approx(1.10)


def test_stats_expose_restored_metrics_alongside_unit_metrics(isolated_db):
    navs = _dividend_navs()
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    fetcher = FakeFetcher(
        navs,
        events=FundDistributionEvents(
            dividends=[FundDividendEvent((ANCHOR_DAY + timedelta(days=1)).isoformat(), 0.10)]
        ),
    )
    FundNavReviewService(fetcher=fetcher).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=40),
    )

    stats = FundNavReviewService().get_stats()

    assert stats["restored_evaluated"] == 1
    assert stats["avg_restored_return_pct"] == pytest.approx(10.0)
    assert stats["restored_positive_rate_pct"] == pytest.approx(100.0)
    assert stats["restored_bases"] == {"dividend_reinvested": 1}
    # Unit basis and restored basis stay separate.
    assert stats["avg_nav_return_pct"] == pytest.approx(-1.0)


def test_stats_restored_fields_are_null_when_restore_never_ran(isolated_db):
    navs = _navs()
    _record(navs)
    _set_due(isolated_db, ANCHOR_DAY)
    FundNavReviewService(fetcher=FakeFetcher(navs)).run_reviews(
        as_of=ANCHOR_DAY + timedelta(days=400),
    )
    # Simulate legacy rows: evaluated before restoration columns existed.
    with isolated_db.get_session() as session:
        for row in session.query(FundNavReviewRecord).all():
            row.restored_return_pct = None
            row.restored_max_drawdown_pct = None
            row.restored_basis = None
        session.commit()

    stats = FundNavReviewService().get_stats()

    assert stats["restored_evaluated"] == 0
    assert stats["restored_positive_rate_pct"] is None
    assert stats["avg_restored_return_pct"] is None
    assert stats["restored_bases"] == {}

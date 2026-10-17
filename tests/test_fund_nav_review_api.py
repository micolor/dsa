# -*- coding: utf-8 -*-
"""API tests for fund NAV review endpoints."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from data_provider.fund_fetcher import (
    FundDividendEvent,
    FundDistributionEvents,
    NavRecord,
)
from src.config import Config
from src.services.fund_nav_review_service import FundNavReviewService
from src.storage import DatabaseManager, FundNavReviewRecord


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


@pytest.fixture()
def client_and_db(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "fund_nav_review_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    _reset_auth_globals()
    Config.reset_instance()
    DatabaseManager.reset_instance()
    app = create_app(static_dir=Path(static_dir))
    client = TestClient(app)
    db = DatabaseManager.get_instance()
    try:
        yield client, db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        _reset_auth_globals()
        if old_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = old_env_file
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _add_review(db, **overrides):
    payload = {
        "fund_code": "003095",
        "fund_name": "中欧医疗健康混合A",
        "horizon": "1m",
        "engine_version": "fund-nav-v1",
        "anchor_date": date(2024, 1, 2),
        "anchor_nav": 1.0,
        "due_date": date(2024, 2, 6),
        "eval_status": "pending",
        "anchor_source": "pipeline",
        "attempts": 0,
    }
    payload.update(overrides)
    with db.session_scope() as session:
        row = FundNavReviewRecord(**payload)
        session.add(row)
        session.flush()
        return int(row.id)


def test_list_reviews_supports_filters_and_pagination(client_and_db):
    client, db = client_and_db
    _add_review(db)
    _add_review(db, horizon="1y")
    _add_review(db, fund_code="000001", fund_name="华夏成长")

    response = client.get(
        "/api/v1/fund-nav-reviews",
        params={"fund_code": "fund:003095", "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["fund_code"] == "003095"

    filtered = client.get("/api/v1/fund-nav-reviews", params={"horizon": "1y"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["horizon"] == "1y"


def test_list_reviews_rejects_invalid_fund_code(client_and_db):
    client, _ = client_and_db
    response = client.get("/api/v1/fund-nav-reviews", params={"fund_code": "abc"})
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_stats_returns_breakdowns(client_and_db):
    client, db = client_and_db
    _add_review(
        db,
        eval_status="evaluated",
        eval_date=date(2024, 2, 1),
        eval_nav=1.1,
        nav_return_pct=10.0,
        max_drawdown_pct=-2.0,
        eval_window_records=22,
    )

    response = client.get("/api/v1/fund-nav-reviews/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["evaluated"] == 1
    assert body["positive_rate_pct"] == pytest.approx(100.0)
    assert body["breakdowns"]["horizon"][0]["value"] == "1m"
    assert body["breakdowns"]["month"][0]["value"] == "2024-01"


def test_run_endpoint_maps_service_result(client_and_db, monkeypatch):
    client, _ = client_and_db
    captured = {}

    def fake_run(self, **kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {
                    "id": 1,
                    "fund_code": "003095",
                    "fund_name": "中欧医疗健康混合A",
                    "horizon": "1m",
                    "engine_version": "fund-nav-v1",
                    "anchor_date": date(2024, 1, 2),
                    "anchor_nav": 1.0,
                    "due_date": date(2024, 2, 6),
                    "eval_status": "evaluated",
                    "anchor_source": "pipeline",
                    "attempts": 1,
                }
            ],
            "evaluated": 1,
            "pending": 0,
            "unable": 0,
            "skipped": 0,
            "engine_version": "fund-nav-v1",
        }

    monkeypatch.setattr(FundNavReviewService, "run_reviews", fake_run)
    response = client.post(
        "/api/v1/fund-nav-reviews/run",
        json={"fund_code": "fund:003095", "horizons": ["1m"], "force": True, "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["evaluated"] == 1
    assert body["items"][0]["fund_code"] == "003095"
    assert captured["fund_code"] == "fund:003095"
    assert captured["horizons"] == ["1m"]
    assert captured["force"] is True
    assert captured["limit"] == 20


def test_run_endpoint_rejects_unknown_horizon(client_and_db):
    client, _ = client_and_db
    response = client.post(
        "/api/v1/fund-nav-reviews/run",
        json={"horizons": ["2w"]},
    )
    assert response.status_code == 422


def test_backfill_endpoint_defaults_to_dry_run(client_and_db, monkeypatch):
    client, _ = client_and_db
    captured = {}

    def fake_backfill(self, **kwargs):
        captured.update(kwargs)
        return {
            "scanned": 3,
            "created": 0,
            "skipped": 0,
            "unmatched": 0,
            "funds": 1,
            "dry_run": True,
            "engine_version": "fund-nav-v1",
        }

    monkeypatch.setattr(FundNavReviewService, "backfill_from_history", fake_backfill)
    response = client.post("/api/v1/fund-nav-reviews/backfill/run", json={})

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert captured["dry_run"] is True
    assert captured["resolve_missing"] is False


def test_restore_backfill_endpoint_defaults_to_dry_run(client_and_db, monkeypatch):
    client, _ = client_and_db
    captured = {}

    def fake_backfill(self, **kwargs):
        captured.update(kwargs)
        return {
            "scanned": 2,
            "updated": 2,
            "unavailable": 0,
            "skipped": 0,
            "funds": 1,
            "dry_run": True,
            "engine_version": "fund-nav-v1",
        }

    monkeypatch.setattr(
        FundNavReviewService, "backfill_restored_metrics", fake_backfill
    )
    response = client.post(
        "/api/v1/fund-nav-reviews/backfill/restore-metrics", json={}
    )

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert captured["dry_run"] is True
    assert captured["force"] is False


def test_restore_backfill_endpoint_passes_filters(client_and_db, monkeypatch):
    client, _ = client_and_db
    captured = {}

    def fake_backfill(self, **kwargs):
        captured.update(kwargs)
        return {
            "scanned": 1,
            "updated": 1,
            "unavailable": 0,
            "skipped": 0,
            "funds": 1,
            "dry_run": False,
            "engine_version": "fund-nav-v1",
        }

    monkeypatch.setattr(
        FundNavReviewService, "backfill_restored_metrics", fake_backfill
    )
    response = client.post(
        "/api/v1/fund-nav-reviews/backfill/restore-metrics",
        json={
            "fund_code": "fund:003095",
            "anchor_from": "2024-01-02",
            "anchor_to": "2024-06-30",
            "limit": 50,
            "dry_run": False,
            "force": True,
        },
    )

    assert response.status_code == 200
    assert captured["fund_code"] == "fund:003095"
    assert captured["anchor_from"] == date(2024, 1, 2)
    assert captured["anchor_to"] == date(2024, 6, 30)
    assert captured["limit"] == 50
    assert captured["dry_run"] is False
    assert captured["force"] is True


def test_restore_backfill_endpoint_rejects_limit_over_400(client_and_db):
    client, _ = client_and_db
    response = client.post(
        "/api/v1/fund-nav-reviews/backfill/restore-metrics",
        json={"limit": 401},
    )
    assert response.status_code == 422


def test_restore_backfill_endpoint_maps_value_error_to_400(client_and_db, monkeypatch):
    client, _ = client_and_db

    def fake_backfill(self, **kwargs):
        raise ValueError("fund_code must be a 6-digit fund code")

    monkeypatch.setattr(
        FundNavReviewService, "backfill_restored_metrics", fake_backfill
    )
    response = client.post(
        "/api/v1/fund-nav-reviews/backfill/restore-metrics", json={}
    )

    assert response.status_code == 400


def test_restore_backfill_endpoint_maps_fetch_error_to_500(client_and_db, monkeypatch):
    client, _ = client_and_db

    def fake_backfill(self, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        FundNavReviewService, "backfill_restored_metrics", fake_backfill
    )
    response = client.post(
        "/api/v1/fund-nav-reviews/backfill/restore-metrics", json={}
    )

    assert response.status_code == 500


def test_list_reviews_exposes_restore_fields(client_and_db):
    client, db = client_and_db
    _add_review(
        db,
        eval_status="evaluated",
        restored_basis="dividend_reinvested",
        restored_return_pct=8.5,
        restored_max_drawdown_pct=-4.25,
        restored_events=1,
        acc_nav_return_pct=8.1,
    )

    response = client.get("/api/v1/fund-nav-reviews")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["restored_basis"] == "dividend_reinvested"
    assert item["restored_return_pct"] == pytest.approx(8.5)
    assert item["restored_max_drawdown_pct"] == pytest.approx(-4.25)
    assert item["restored_events"] == 1
    assert item["acc_nav_return_pct"] == pytest.approx(8.1)


def test_stats_expose_restored_metrics(client_and_db):
    client, db = client_and_db
    _add_review(
        db,
        eval_status="evaluated",
        nav_return_pct=-1.0,
        max_drawdown_pct=-2.0,
        restored_return_pct=9.5,
        restored_max_drawdown_pct=-1.5,
        restored_basis="dividend_reinvested",
    )

    body = client.get("/api/v1/fund-nav-reviews/stats").json()

    assert body["restored_evaluated"] == 1
    assert body["avg_restored_return_pct"] == pytest.approx(9.5)
    assert body["restored_positive_rate_pct"] == pytest.approx(100.0)
    assert body["restored_bases"] == {"dividend_reinvested": 1}
    bucket = body["breakdowns"]["horizon"][0]
    assert bucket["restored_evaluated"] == 1
    assert bucket["avg_restored_return_pct"] == pytest.approx(9.5)
    assert bucket["restored_positive_rate_pct"] == pytest.approx(100.0)
    # Unit basis stays unchanged.
    assert body["avg_nav_return_pct"] == pytest.approx(-1.0)


def test_nav_range_returns_ordered_records_with_adjusted_nav(client_and_db, monkeypatch):
    client, _ = client_and_db

    class FakeFetcher:
        def get_nav_range(self, code, start, end):
            return [
                NavRecord("2025-01-02", 1.00, 1.10, 0.0),
                NavRecord("2025-01-03", 0.90, 1.10, -10.0),
                NavRecord("2025-01-06", 0.99, 1.10, 10.0),
            ]

        def get_distribution_events(self, code):
            return FundDistributionEvents(
                dividends=[FundDividendEvent("2025-01-03", 0.10)]
            )

    monkeypatch.setattr(
        "src.services.fund_nav_review_service.FundFetcher", lambda: FakeFetcher()
    )
    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "fund:003095",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fund_code"] == "003095"
    assert body["adjustment_status"] == "available"
    assert body["truncated"] is False
    assert body["events"] == {"dividends": 1, "splits": 0}
    assert [item["date"] for item in body["items"]] == [
        "2025-01-02",
        "2025-01-03",
        "2025-01-06",
    ]
    assert body["items"][1]["adjusted_nav"] == pytest.approx(1.0)
    assert body["items"][2]["adjusted_nav"] == pytest.approx(1.1)


def test_nav_range_rejects_too_wide_window(client_and_db):
    client, _ = client_and_db

    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "003095",
            "start_date": "2020-01-01",
            "end_date": "2026-01-01",
        },
    )

    assert response.status_code == 400


def test_nav_range_rejects_reversed_window_and_bad_code(client_and_db):
    client, _ = client_and_db

    reversed_window = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "003095",
            "start_date": "2025-02-01",
            "end_date": "2025-01-01",
        },
    )
    assert reversed_window.status_code == 400

    bad_code = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "12345",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    )
    assert bad_code.status_code == 400


def test_nav_range_marks_adjustment_unavailable_on_event_failure(
    client_and_db, monkeypatch
):
    client, _ = client_and_db

    class FakeFetcher:
        def get_nav_range(self, code, start, end):
            return [
                NavRecord("2025-01-02", 1.00, 1.10, 0.0),
                NavRecord("2025-01-03", 0.99, 1.09, -1.0),
            ]

        def get_distribution_events(self, code):
            raise RuntimeError("fhsp down")

    monkeypatch.setattr(
        "src.services.fund_nav_review_service.FundFetcher", lambda: FakeFetcher()
    )
    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "003095",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["adjustment_status"] == "unavailable"
    assert body["events"] == {"dividends": 0, "splits": 0}
    assert all(item["adjusted_nav"] is None for item in body["items"])


def test_nav_range_empty_range_returns_no_items(client_and_db, monkeypatch):
    client, _ = client_and_db

    class FakeFetcher:
        def get_nav_range(self, code, start, end):
            return []

        def get_distribution_events(self, code):
            raise AssertionError("events should not be fetched without navs")

    monkeypatch.setattr(
        "src.services.fund_nav_review_service.FundFetcher", lambda: FakeFetcher()
    )
    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={
            "fund_code": "003095",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["adjustment_status"] == "available"

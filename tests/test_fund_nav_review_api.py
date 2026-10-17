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

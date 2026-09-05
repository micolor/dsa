# -*- coding: utf-8 -*-
"""API tests for GET /api/v1/data-quality/discrepancies.

Response item keys are camelCase (stockCode/issueType/primarySource/...)
per the reconciliation design spec, unlike the older snake_case endpoints.
"""

from __future__ import annotations

import os
import sys
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
from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository
from src.storage import DatabaseManager


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
    db_path = tmp_path / "data_quality_discrepancies_api.db"
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


def _record(repo, *, issue_type, stock_code="600519.SH", market="cn"):
    return repo.record_discrepancy(
        {
            "market": market,
            "stock_code": stock_code,
            "issue_type": issue_type,
            "primary_source": "tencent",
            "secondary_source": "akshare_sina",
            "primary_price": 1500.0,
            "secondary_price": 1450.0,
            "price_diff_pct": 3.33,
            "primary_ts": "2026-09-05T10:00:00",
            "secondary_ts": "2026-09-05T10:00:00",
            "detail": "price mismatch",
        }
    )


def test_list_discrepancies_ok(client_and_db):
    client, db = client_and_db
    repo = DataQualityDiscrepancyRepository(db)
    _record(repo, issue_type="price_discrepancy")

    resp = client.get("/api/v1/data-quality/discrepancies", params={"issue_type": "price_discrepancy"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["items"][0]["issueType"] == "price_discrepancy"
    assert body["items"][0]["primarySource"] == "tencent"
    assert body["items"][0]["stockCode"] == "600519.SH"
    assert body["items"][0]["market"] == "cn"


def test_list_discrepancies_paginates(client_and_db):
    client, db = client_and_db
    repo = DataQualityDiscrepancyRepository(db)
    _record(repo, issue_type="price_discrepancy", stock_code="600519.SH")
    _record(repo, issue_type="date_mismatch", stock_code="000001.SZ")
    _record(repo, issue_type="field_missing", stock_code="hk00700")

    resp = client.get("/api/v1/data-quality/discrepancies", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["pageSize"] == 1
    assert len(body["items"]) == 1
    assert body["total"] >= 3

    # issue_type filter narrows
    resp = client.get(
        "/api/v1/data-quality/discrepancies",
        params={"issue_type": "date_mismatch", "page_size": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["issueType"] == "date_mismatch"

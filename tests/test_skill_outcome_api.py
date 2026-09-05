# -*- coding: utf-8 -*-
"""API tests for skill-opinion outcome run + performance stats endpoints."""

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
    db_path = tmp_path / "skill_outcome_api.db"
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


def test_run_skill_outcomes_returns_engine_version(client_and_db):
    client, *_ = client_and_db
    response = client.post("/api/v1/decision-signals/skill-outcomes/run", json={"limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine_version"] == "skill-opinion-outcome-v1"
    assert body["limit_unit"] == "outcome_key"
    assert "items" in body and "created" in body and "skipped" in body


def test_run_skill_outcomes_rejects_bad_limit(client_and_db):
    client, *_ = client_and_db
    response = client.post("/api/v1/decision-signals/skill-outcomes/run", json={"limit": 0})
    assert response.status_code == 422


def test_skill_outcome_stats_shape(client_and_db):
    client, *_ = client_and_db
    response = client.get("/api/v1/decision-signals/skill-outcomes/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine_version"] == "skill-opinion-outcome-v1"
    assert body["minimum_evaluated_sample_size"] == 30
    assert isinstance(body["buckets"], list)


def test_skill_outcome_stats_rejects_bad_horizon(client_and_db):
    client, *_ = client_and_db
    response = client.get(
        "/api/v1/decision-signals/skill-outcomes/stats", params={"horizons": ["bad"]}
    )
    assert response.status_code == 400

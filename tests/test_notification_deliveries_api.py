# -*- coding: utf-8 -*-
"""API tests for generic (non-alert) notification delivery receipts."""

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
from src.repositories.notification_delivery_repo import NotificationDeliveryRepository
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
    db_path = tmp_path / "notification_deliveries_api.db"
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


def test_list_deliveries_returns_items_snake_case(client_and_db):
    client, db = client_and_db
    NotificationDeliveryRepository(db).record_delivery(
        {
            "route_type": "report",
            "channel": "telegram",
            "success": True,
            "latency_ms": 80,
        }
    )
    resp = client.get("/api/v1/notifications/deliveries")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["route_type"] == "report"
    assert item["channel"] == "telegram"
    assert item["success"] is True
    assert item["latency_ms"] == 80


def test_list_deliveries_route_type_filter_narrows(client_and_db):
    client, db = client_and_db
    repo = NotificationDeliveryRepository(db)
    repo.record_delivery({"route_type": "report", "channel": "telegram", "success": True})
    repo.record_delivery({"route_type": "system_error", "channel": "telegram", "success": False})

    resp = client.get("/api/v1/notifications/deliveries", params={"route_type": "report"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["route_type"] == "report"

    # success filter
    resp = client.get("/api/v1/notifications/deliveries", params={"success": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["success"] is False


def test_list_deliveries_empty_query_returns_empty_items(client_and_db):
    client, db = client_and_db
    NotificationDeliveryRepository(db).record_delivery(
        {"route_type": "report", "channel": "telegram", "success": True}
    )
    resp = client.get("/api/v1/notifications/deliveries", params={"route_type": "nonexistent"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["page"] == 1

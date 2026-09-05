# -*- coding: utf-8 -*-
"""Repository tests for NotificationDelivery P1."""

from __future__ import annotations

import os

import pytest

from src.config import Config
from src.repositories.notification_delivery_repo import NotificationDeliveryRepository
from src.storage import DatabaseManager


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "notification_delivery_repo.db"
    os.environ["DATABASE_PATH"] = str(db_path)
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


@pytest.fixture
def repo(isolated_db):
    return NotificationDeliveryRepository(isolated_db)


def test_record_delivery(repo):
    fields = {
        "route_type": "report",
        "channel": "telegram",
        "attempt": 1,
        "success": True,
        "error_code": None,
        "retryable": False,
        "latency_ms": 120,
        "diagnostics": "ok",
    }
    row = repo.record_delivery(fields)
    assert row.id is not None
    assert row.route_type == "report"
    assert row.channel == "telegram"
    assert row.success is True
    assert row.attempt == 1


def test_list_deliveries_filters_and_pages(repo):
    repo.record_delivery({"route_type": "report", "channel": "telegram", "success": True})
    repo.record_delivery({"route_type": "report", "channel": "email", "success": False})
    repo.record_delivery({"route_type": "system_error", "channel": "telegram", "success": True})

    rows, total = repo.list_deliveries(route_type="report")
    assert total == 2
    assert len(rows) == 2

    rows, total = repo.list_deliveries(route_type="report", channel="telegram")
    assert total == 1
    assert rows[0].channel == "telegram"

    rows, total = repo.list_deliveries(success=False)
    assert total == 1
    assert rows[0].success is False

    rows, total = repo.list_deliveries(page=1, page_size=1)
    assert total == 3
    assert len(rows) == 1

# -*- coding: utf-8 -*-
"""API tests for the paper-trading account endpoints."""

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

# 配置值与默认值 1000000 拉开距离，这样「省略 body 时走配置」和
# 「body 覆盖配置」两种路径的断言不会同时成立而互相掩盖。
_CONFIGURED_CAPITAL = 250000


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


@pytest.fixture()
def client(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "paper_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={db_path}",
                f"PAPER_INITIAL_CAPITAL={_CONFIGURED_CAPITAL}",
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
    try:
        yield TestClient(app)
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


def test_reset_opens_a_new_account_with_the_supplied_capital(client):
    first = client.get("/api/v1/paper/account")
    assert first.status_code == 200
    old = first.json()
    assert old["initial_capital"] == _CONFIGURED_CAPITAL

    response = client.post("/api/v1/paper/reset", json={"initial_capital": 500000})

    assert response.status_code == 200
    new = response.json()
    assert new["account_id"] != old["account_id"]
    assert new["initial_capital"] == 500000
    assert new["cash"] == 500000
    assert new["status"] == "active"
    assert new["snapshot"]["initial_capital"] == 500000
    assert new["snapshot"]["net_value"] == 500000
    assert new["snapshot"]["open_position_count"] == 0

    # 新账户成为后续所有入口默认读到的那个。
    after = client.get("/api/v1/paper/account").json()
    assert after["account_id"] == new["account_id"]


def test_reset_without_body_falls_back_to_the_configured_capital(client):
    old = client.get("/api/v1/paper/account").json()

    response = client.post("/api/v1/paper/reset")

    assert response.status_code == 200
    new = response.json()
    assert new["account_id"] != old["account_id"]
    assert new["initial_capital"] == _CONFIGURED_CAPITAL
    assert new["cash"] == _CONFIGURED_CAPITAL


@pytest.mark.parametrize("capital", [0, -1, -500000])
def test_reset_rejects_non_positive_capital(client, capital):
    first = client.get("/api/v1/paper/account").json()

    response = client.post("/api/v1/paper/reset", json={"initial_capital": capital})

    assert response.status_code == 422
    # 被拒的重置不能有任何副作用：账户还是原来那个。
    assert client.get("/api/v1/paper/account").json()["account_id"] == first["account_id"]

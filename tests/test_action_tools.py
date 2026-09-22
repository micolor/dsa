"""Tests for the confirmable write-action proposal tools."""

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

from src.agent.tools.action_tools import (
    _handle_propose_portfolio_trade,
    propose_portfolio_trade_tool,
)

_ACCOUNTS = [
    {"id": 1, "name": "A股主账户", "is_active": True},
    {"id": 2, "name": "港美股", "is_active": True},
    {"id": 3, "name": "已停用账户", "is_active": False},
]


def _patch_accounts(accounts=None):
    service = mock.MagicMock()
    service.list_accounts.return_value = _ACCOUNTS if accounts is None else accounts
    return mock.patch("src.services.portfolio_service.PortfolioService", return_value=service)


def test_tool_declares_proposal_policy():
    assert propose_portfolio_trade_tool.name == "propose_portfolio_trade"
    assert propose_portfolio_trade_tool.category == "action"
    assert propose_portfolio_trade_tool.policy.read_only is True
    # 不进 Codex 只读面：cancellation_safe 必须为 False
    assert propose_portfolio_trade_tool.policy.cancellation_safe is False


def test_propose_trade_builds_api_shaped_payload():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="005827",
            side="buy",
            quantity=25900,
            price=1.6706,
            trade_date="2026-09-22",
        )
    assert "error" not in result
    assert result["kind"] == "portfolio_trade"
    assert result["proposal"] == {
        "account_id": 1,
        "symbol": "005827",
        "trade_date": "2026-09-22",
        "side": "buy",
        "quantity": 25900.0,
        "price": 1.6706,
        "fee": 0.0,
        "tax": 0.0,
    }
    assert result["summary"].startswith("在「A股主账户」记一笔买入 005827 25900 @ ¥1.6706")
    assert "2026-09-22" in result["summary"]


def test_propose_trade_defaults_trade_date_to_today():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert result["proposal"]["trade_date"] == date.today().isoformat()


def test_propose_trade_rejects_unknown_account_and_lists_choices():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=99, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "不存在" in result["error"]
    assert "A股主账户" in result["error"]


def test_propose_trade_rejects_inactive_account():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=3, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "已停用" in result["error"]


def test_propose_trade_rejects_bad_side_quantity_price():
    with _patch_accounts():
        assert "side" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="hold", quantity=100, price=1800
        )["error"]
        assert "quantity" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=0, price=1800
        )["error"]
        assert "price" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=-1
        )["error"]


def test_propose_trade_rejects_bad_date_and_empty_symbol():
    with _patch_accounts():
        assert "trade_date" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=1, price=1, trade_date="2026/09/22"
        )["error"]
        assert "symbol" in _handle_propose_portfolio_trade(
            account_id=1, symbol="  ", side="buy", quantity=1, price=1
        )["error"]


def test_propose_trade_includes_amount_and_reason():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="005827",
            side="buy",
            quantity=25900,
            price=1.6706,
            trade_date="2026-09-22",
            fee=5,
            reason="定投补仓",
        )
    # 25900 × 1.6706 = 43268.54，加 5 元手续费 = 43273.54
    assert "约 ¥43273.54" in result["summary"]
    assert result["summary"].endswith("（定投补仓）")


def test_propose_trade_envelope_is_json_serializable():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="sell", quantity=100, price=1800
        )
    json.dumps(result, ensure_ascii=False)
    assert result["summary"].startswith("在「A股主账户」记一笔卖出")

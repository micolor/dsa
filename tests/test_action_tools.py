"""Tests for the confirmable write-action proposal tools."""

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

from src.agent.tools.action_tools import (
    SUPPORTED_MARKETS,
    SUPPORTED_SIDES,
    _handle_propose_portfolio_trade,
    propose_portfolio_trade_tool,
)

_ACCOUNTS = [
    {"id": 1, "name": "A股主账户", "is_active": True},
    {"id": 2, "name": "港美股", "is_active": True},
    {"id": 3, "name": "已停用账户", "is_active": False},
]


_ACCOUNTS_CNY = [{"id": 1, "name": "A股主账户", "is_active": True, "base_currency": "CNY"}]
_ACCOUNTS_USD = [{"id": 1, "name": "港美股", "is_active": True, "base_currency": "USD"}]


def _patch_accounts(accounts=None):
    """Fake the service the way the real repo behaves: inactive rows only come back
    when ``include_inactive=True`` (``portfolio_repo.list_accounts``), so a handler
    that forgets the flag loses the "已停用" branch instead of the test passing anyway.
    """
    rows = _ACCOUNTS if accounts is None else accounts

    def _list_accounts(include_inactive=False):
        if include_inactive:
            return list(rows)
        return [a for a in rows if a.get("is_active")]

    service = mock.MagicMock()
    service.list_accounts.side_effect = _list_accounts
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
    # 夹具没有 base_currency，模型也没给 currency：只渲染数字，不猜 ¥
    assert result["summary"].startswith("在「A股主账户」记一笔买入 005827 25900 @ 1.6706")
    assert "约支出 43268.54" in result["summary"]
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
    with _patch_accounts() as patched_service:
        result = _handle_propose_portfolio_trade(
            account_id=3, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "已停用" in result["error"]
    # 必须显式带上停用账户：默认 include_inactive=False 时仓库层会把它过滤掉，
    # 模型就会对用户说账户「不存在」，这个分支也就成了死代码。
    patched_service.return_value.list_accounts.assert_called_once_with(include_inactive=True)


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
    assert "约支出 43273.54" in result["summary"]
    assert result["summary"].endswith("（定投补仓）")


def test_propose_trade_envelope_is_json_serializable():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="sell", quantity=100, price=1800
        )
    json.dumps(result, ensure_ascii=False)
    assert result["summary"].startswith("在「A股主账户」记一笔卖出")


def test_propose_trade_sell_nets_out_fees():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="600519",
            side="sell",
            quantity=100,
            price=1800,
            trade_date="2026-09-22",
            fee=5,
            tax=0.5,
        )
    # 卖出是现金流入：100 × 1800 − 5 − 0.5 = 179994.5
    assert "约收入 179994.50" in result["summary"]


def test_propose_trade_renders_currency_code_from_account_base():
    with _patch_accounts(_ACCOUNTS_CNY):
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "@ CNY 1800" in result["summary"]
    assert "约支出 CNY 180000.00" in result["summary"]
    # 账户本位币只用于展示，不能当成成交价币种写进请求体
    assert "currency" not in result["proposal"]


def test_propose_trade_renders_usd_account():
    with _patch_accounts(_ACCOUNTS_USD):
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="AAPL", side="buy", quantity=10, price=180, market="us"
        )
    assert "@ USD 180" in result["summary"]
    assert "约支出 USD 1800.00" in result["summary"]


def test_propose_trade_explicit_currency_wins_over_account_base():
    """美元账户买 A 股：成交价是 CNY，与账户本位币不同，模型显式给出的币种优先。"""
    with _patch_accounts(_ACCOUNTS_USD):
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=10, price=180.5, currency="cny"
        )
    assert result["proposal"]["currency"] == "CNY"
    assert "@ CNY 180.5" in result["summary"]


def test_propose_trade_rejects_oversized_symbol_negative_fee_and_bad_currency():
    with _patch_accounts():
        assert "symbol" in _handle_propose_portfolio_trade(
            account_id=1, symbol="0" * 17, side="buy", quantity=1, price=1
        )["error"]
        # allow_zero=True 只放宽到 0，负方向仍要拒绝
        assert "fee" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=1, price=1, fee=-1
        )["error"]
        assert "currency" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=1, price=1, currency="US"
        )["error"]


def test_propose_trade_truncates_note_to_schema_limit():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=1, price=1, note="备" * 300
        )
    assert len(result["proposal"]["note"]) == 255


def test_propose_trade_rejects_non_finite_numbers():
    """``json.loads('{"price": 1e999}')`` 得到的就是 ``inf``，而 ``inf``/``nan`` 对任何
    比较都返回 False，``<= 0`` 范围守卫拦不住它们：

    - ``nan`` 被工具放进 proposal、再被 Pydantic 拒 → 「提案通过但确认失败」；
    - ``inf`` 连 Pydantic 都收，最终经 SSE 发出非标准 JSON 的 ``Infinity``。
    """
    with _patch_accounts():
        for field, override in (
            ("price", {"price": float("nan")}),
            ("price", {"price": float("inf")}),
            ("quantity", {"quantity": float("inf")}),
            ("fee", {"fee": float("nan")}),
        ):
            payload = {
                "account_id": 1,
                "symbol": "600519",
                "side": "buy",
                "quantity": 1,
                "price": 1800,
            }
            payload.update(override)
            assert field in _handle_propose_portfolio_trade(**payload)["error"]


def test_enums_mirror_the_api_contract():
    from typing import get_args

    from api.v1.schemas.portfolio import PortfolioTradeCreateRequest
    from src.services.portfolio_service import VALID_MARKETS, VALID_SIDES

    fields = PortfolioTradeCreateRequest.model_fields
    api_sides = set(get_args(fields["side"].annotation))
    # market 是 Optional[Literal[...]]：先剥掉 NoneType 再取 Literal 的值
    api_markets = {v for arg in get_args(fields["market"].annotation) for v in get_args(arg)}

    assert set(SUPPORTED_SIDES) == api_sides == set(VALID_SIDES)
    assert set(SUPPORTED_MARKETS) == api_markets == set(VALID_MARKETS)

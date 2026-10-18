"""Tests for the confirmable write-action proposal tools."""

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from src.agent.runner import _maybe_emit_action_event
from src.agent.tools.action_tools import (
    SUPPORTED_MARKETS,
    SUPPORTED_SIDES,
    _handle_propose_paper_reset,
    _handle_propose_portfolio_account_change,
    _handle_propose_portfolio_ledger_entry,
    _handle_propose_portfolio_trade,
    _handle_propose_signal_change,
    propose_paper_reset,
    propose_portfolio_account_change,
    propose_portfolio_ledger_entry,
    propose_portfolio_trade_tool,
    propose_signal_change,
)

_ACCOUNTS = [
    {"id": 1, "name": "A股主账户", "is_active": True, "market": "cn", "base_currency": "CNY"},
    {"id": 2, "name": "港美股", "is_active": True, "market": "us", "base_currency": "USD"},
    {"id": 3, "name": "已停用账户", "is_active": False, "market": "cn", "base_currency": "CNY"},
]


_ACCOUNTS_CNY = [{"id": 1, "name": "A股主账户", "is_active": True, "market": "cn", "base_currency": "CNY"}]
_ACCOUNTS_USD = [{"id": 1, "name": "港美股", "is_active": True, "market": "us", "base_currency": "USD"}]
# market 与 base_currency 是两个互相独立的字段（`PortfolioAccount` 上都是 NOT NULL 默认值），
# 港美股账户配人民币本位币是 CN 用户的真实组合。落库币种只看 market，所以这类账户的卡片
# 必须写 USD —— 写 base_currency 的 CNY 就是给用户看错单位。
_ACCOUNTS_US_MARKET_CNY_BASE = [
    {"id": 1, "name": "港美股", "is_active": True, "market": "us", "base_currency": "CNY"}
]


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
    assert propose_portfolio_trade_tool.policy.read_only is False
    assert propose_portfolio_trade_tool.policy.execution_mode == "proposal"
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
    # 模型没给 currency：币种按服务端落库规则由 market 推出（cn → CNY），与 `¥` 无关
    assert result["summary"].startswith("在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706")
    assert "约支出 CNY 43268.54" in result["summary"]
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
    assert "约支出 CNY 43273.54" in result["summary"]
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
    assert "约收入 CNY 179994.50" in result["summary"]


def test_propose_trade_renders_currency_code_from_market():
    with _patch_accounts(_ACCOUNTS_CNY):
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "@ CNY 1800" in result["summary"]
    assert "约支出 CNY 180000.00" in result["summary"]
    # 账户字段只参与解析展示币种，不能当成成交价币种写进请求体
    assert "currency" not in result["proposal"]


def test_propose_trade_card_currency_follows_the_market_not_the_account_base():
    """market 与 base_currency 不一致时，卡片必须写**落库**的那个币种。

    `PortfolioService.record_trade` 用的是 `currency or default_currency_for_market(market or
    account.market)`，完全不看 `base_currency`；这里若退回账户本位币，卡片就会把 USD 的记录
    写成「约支出 CNY …」——用户唯一要确认的量级带着错的单位。
    """
    with _patch_accounts(_ACCOUNTS_US_MARKET_CNY_BASE):
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="AAPL", side="buy", quantity=10, price=180
        )
    assert "@ USD 180" in result["summary"]
    assert "约支出 USD 1800.00" in result["summary"]
    assert "CNY" not in result["summary"]


@pytest.fixture()
def temp_portfolio_service(tmp_path, monkeypatch):
    """真实 ``PortfolioService`` + 临时 SQLite（同 ``tests/test_portfolio_service.py`` 的做法），
    用于把工具的产出与真正落库的结果对照，而不是与另抄一份的期望值对照。"""
    from src.config import Config
    from src.services.portfolio_service import PortfolioService
    from src.storage import DatabaseManager

    env_path = tmp_path / ".env"
    db_path = tmp_path / "portfolio_test.db"
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
    monkeypatch.setenv("ENV_FILE", str(env_path))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        yield PortfolioService()
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


@pytest.mark.parametrize(
    ("market", "base_currency"),
    [("us", "CNY"), ("hk", "CNY"), ("cn", "USD")],
)
def test_card_currency_equals_the_currency_the_service_records(
    temp_portfolio_service, market, base_currency
):
    """F1 的端到端对照，期望币种不写死：账户由真实服务创建、工具读同一个服务，
    再把工具产出的 proposal **原样**交给 ``record_trade`` 落库，最后把「卡片上印的币种」
    与「记录里存下的币种」直接比对。工具侧若退回账户本位币（旧实现），第 2、3 组会转红——
    不依赖任何人记得同步两处映射。
    """
    service = temp_portfolio_service
    account = service.create_account(
        name="港美股", broker="Demo", market=market, base_currency=base_currency
    )
    result = _handle_propose_portfolio_trade(
        account_id=account["id"], symbol="AAPL", side="buy", quantity=10, price=180
    )
    assert "error" not in result

    proposal = result["proposal"]
    service.record_trade(
        account_id=proposal["account_id"],
        symbol=proposal["symbol"],
        trade_date=date.fromisoformat(proposal["trade_date"]),
        side=proposal["side"],
        quantity=proposal["quantity"],
        price=proposal["price"],
        fee=proposal["fee"],
        tax=proposal["tax"],
        market=proposal.get("market"),
        currency=proposal.get("currency"),
    )
    stored = service.list_trade_events(account_id=account["id"])["items"][0]

    assert f"约支出 {stored['currency']} 1800.00" in result["summary"]
    assert "@ " + stored["currency"] + " 180" in result["summary"]
    # 这几组夹具故意让本位币与落库币种不同：上一条断言才有区分度
    assert stored["currency"] != base_currency


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


def test_reason_is_length_bounded_in_the_action_tools():
    """reason 会流进 summary → SSE 事件 → 确认卡片，模型不能往里塞几 KB 文本。"""
    with _patch_accounts():
        trade = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="600519",
            side="buy",
            quantity=1,
            price=1,
            reason="很" * 5000,
        )
    assert len(trade["summary"]) < 400


def test_action_tools_are_registered_with_chinese_labels():
    from api.v1.endpoints.agent import TOOL_DISPLAY_NAMES
    from src.agent.factory import get_tool_registry
    from src.agent.runner import _THINKING_TOOL_LABELS

    registry = get_tool_registry()
    for name in (
        "propose_portfolio_trade",
        "propose_portfolio_ledger_entry",
        "propose_portfolio_account_change",
        "propose_signal_change",
        "propose_paper_reset",
    ):
        tool = registry.resolve(name)
        assert tool is not None, f"{name} 未注册"
        assert _THINKING_TOOL_LABELS.get(name), f"{name} 缺少思考过程标签"
        assert TOOL_DISPLAY_NAMES.get(name), f"{name} 缺少中文展示名"


def test_emitter_rejects_unknown_kind():
    from src.agent.factory import get_tool_registry

    events = []
    tc = SimpleNamespace(name="propose_portfolio_trade")
    raw = json.dumps({"kind": "drop_table", "summary": "x", "proposal": {"a": 1}})
    out = _maybe_emit_action_event(
        tc, raw, events.append, step=1, tool_registry=get_tool_registry()
    )
    assert out == raw
    assert events == []


def test_emitter_rejects_malformed_summary_and_proposal():
    from src.agent.factory import get_tool_registry

    events = []
    registry = get_tool_registry()
    tc = SimpleNamespace(name="propose_portfolio_trade")
    for payload in (
        {"kind": "portfolio_trade", "summary": 1, "proposal": {"a": 1}},
        {"kind": "portfolio_trade", "summary": "x", "proposal": "nope"},
        {"kind": ["portfolio_trade"], "summary": "x", "proposal": {"a": 1}},
        # 空白摘要：前端 `parseActionProposalEvent` 会丢弃它，runner 若放行就会「发射成功、
        # 卡片不存在，且把 {"message": ""} 回给模型」——模型据此宣称已生成卡片，用户什么也没看到。
        {"kind": "portfolio_trade", "summary": "", "proposal": {"a": 1}},
        {"kind": "portfolio_trade", "summary": "   ", "proposal": {"a": 1}},
    ):
        raw = json.dumps(payload)
        assert _maybe_emit_action_event(
            tc, raw, events.append, step=1, tool_registry=registry
        ) == raw
    assert events == []


@pytest.mark.parametrize(
    ("tool_name", "envelope", "expected_kind"),
    [
        (
            "propose_alert_rule_change",
            {"kind": "alert_create", "summary": "「600519」价格上穿 ¥1800",
             "proposal": {"target": "600519", "alert_type": "price_cross"}},
            "alert_create",
        ),
        (
            "propose_portfolio_trade",
            {"kind": "portfolio_trade", "summary": "在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706",
             "proposal": {"account_id": 1, "symbol": "005827", "side": "buy"}},
            "portfolio_trade",
        ),
    ],
)
def test_emitter_accepts_every_proposal_tool_envelope(tool_name, envelope, expected_kind):
    """每个提案工具的每种信封都必须被发射。

    漏掉任一工具的 `execution_mode="proposal"` 声明，或漏掉任一 kind
    （`ACTION_PROPOSAL_KINDS`），该动作的提案会被静默丢弃、前端永远等不到卡片；
    旧版只用 watchlist 一个工具的写法对 `propose_portfolio_trade` 缺失是空转的
    （实测：删掉它 46 个测试仍全绿）。
    """
    from src.agent.factory import get_tool_registry

    events = []
    tc = SimpleNamespace(name=tool_name)
    raw = json.dumps(envelope, ensure_ascii=False)
    out = _maybe_emit_action_event(
        tc, raw, events.append, step=2, tool_registry=get_tool_registry()
    )

    assert events[0]["type"] == "action_proposal"
    assert events[0]["kind"] == expected_kind
    assert json.loads(out) == {"message": envelope["summary"]}


def test_every_proposable_kind_is_accepted_by_the_runner():
    """工具能产出的 kind 必须包含在 action contract 的提案白名单内。

    这条把 runner 侧的白名单与工具侧的实际产出一致性钉住：漏一个，该动作的提案会被
    `_maybe_emit_action_event` 静默丢弃（工具结果按原样放行），前端永远等不到卡片，
    而且没有任何测试会红。
    """
    from src.agent.action_contract import ACTION_PROPOSAL_KINDS

    # ``propose_watchlist_change`` was replaced by the direct ``manage_watchlist``
    # tool; the remaining proposal tools produce the kinds below.
    producible = {
        "portfolio_trade",
        "alert_create", "alert_update", "alert_delete",
        "portfolio_cash_create", "portfolio_cash_delete",
        "portfolio_corporate_create", "portfolio_corporate_delete",
        "portfolio_account_create", "portfolio_account_update", "portfolio_account_deactivate",
        "signal_create", "signal_status", "signal_feedback", "signal_reassess",
        "paper_reset",
        "history_delete_records", "history_delete_by_code",
        "intelligence_source_create", "intelligence_defaults_create",
        "config_update", "scheduler_run", "notification_test",
    }
    assert producible <= ACTION_PROPOSAL_KINDS


def test_proposal_tool_names_match_the_registry():
    """注册表里所有 propose_* 工具都必须声明 execution_mode="proposal"。

    runner 现在按工具策略分派事件：漏声明或声明成别的模式，该工具的提案会被静默丢弃，
    且现有测试全绿。这条从注册表反查，把那个方向也堵上。
    """
    from src.agent.factory import get_tool_registry

    registry = get_tool_registry()
    registered = {n for n in registry.list_names() if n.startswith("propose_")}
    declared = {
        n for n in registry.list_names()
        if registry.resolve(n).policy.execution_mode == "proposal"
    }
    assert registered == declared, (
        f"提案工具集合不一致：仅注册表有 {sorted(registered - declared)}；"
        f"仅声明 proposal 的有 {sorted(declared - registered)}"
    )


# ============================================================
# propose_portfolio_ledger_entry
# ============================================================

def _invoke(name, args):
    from src.agent.factory import get_tool_registry

    # Handlers return the envelope dict; the runner serializes it for the LLM.
    # Goes through ``execute`` on purpose: the account-change handler also takes
    # a ``name`` argument, which used to collide with the registry method's
    # parameter (``execute(name, **{"name": ...})`` → TypeError at runtime).
    return get_tool_registry().execute(name, **args)


def test_portfolio_cash_delete_envelope():
    payload = _invoke("propose_portfolio_ledger_entry", {"action": "delete_cash", "entry_id": 9})
    assert payload["kind"] == "portfolio_cash_delete"
    assert payload["proposal"] == {"entry_id": 9}


def test_portfolio_corporate_delete_envelope():
    payload = _invoke(
        "propose_portfolio_ledger_entry", {"action": "delete_corporate", "action_id": 11}
    )
    assert payload["kind"] == "portfolio_corporate_delete"
    assert payload["proposal"] == {"action_id": 11}


def test_portfolio_cash_create_envelope_normalizes_aliases():
    """``deposit``/``withdraw`` 是自然语言别名；落库契约只接受 ``in``/``out``。"""
    with _patch_accounts():
        payload = _invoke(
            "propose_portfolio_ledger_entry",
            {
                "action": "cash",
                "account_id": 1,
                "event_date": "2026-09-22",
                "direction": "deposit",
                "amount": 1000,
                "note": "转入",
            },
        )
    assert payload["kind"] == "portfolio_cash_create"
    assert payload["proposal"] == {
        "account_id": 1,
        "event_date": "2026-09-22",
        "direction": "in",
        "amount": 1000.0,
        "currency": None,
        "note": "转入",
    }


def test_portfolio_cash_create_rejects_bad_date_direction_amount():
    with _patch_accounts():
        assert "event_date" in _handle_propose_portfolio_ledger_entry(
            action="cash", account_id=1, event_date="2026/09/22", direction="in", amount=1
        )["error"]
        assert "direction" in _handle_propose_portfolio_ledger_entry(
            action="cash", account_id=1, event_date="2026-09-22", direction="sideways", amount=1
        )["error"]
        assert "amount" in _handle_propose_portfolio_ledger_entry(
            action="cash", account_id=1, event_date="2026-09-22", direction="in", amount=0
        )["error"]


def test_portfolio_corporate_create_envelope():
    with _patch_accounts():
        payload = _invoke(
            "propose_portfolio_ledger_entry",
            {
                "action": "corporate",
                "account_id": 1,
                "symbol": "600519",
                "effective_date": "2026-06-20",
                "action_type": "cash_dividend",
                "cash_dividend_per_share": 2.5,
            },
        )
    assert payload["kind"] == "portfolio_corporate_create"
    assert payload["proposal"] == {
        "account_id": 1,
        "symbol": "600519",
        "effective_date": "2026-06-20",
        "action_type": "cash_dividend",
        "market": None,
        "currency": None,
        "cash_dividend_per_share": 2.5,
        "split_ratio": None,
        "note": None,
    }


def test_portfolio_corporate_create_requires_a_value_and_valid_action_type():
    with _patch_accounts():
        assert "cash_dividend_per_share" in _handle_propose_portfolio_ledger_entry(
            action="corporate",
            account_id=1,
            symbol="600519",
            effective_date="2026-06-20",
            action_type="cash_dividend",
        )["error"]
        assert "action_type" in _handle_propose_portfolio_ledger_entry(
            action="corporate",
            account_id=1,
            symbol="600519",
            effective_date="2026-06-20",
            action_type="merger",
            split_ratio=2,
        )["error"]


def test_portfolio_ledger_rejects_unknown_action():
    payload = _invoke("propose_portfolio_ledger_entry", {"action": "transfer"})
    assert "action" in payload["error"]


# ============================================================
# propose_portfolio_account_change
# ============================================================

def test_portfolio_account_create_envelope():
    payload = _invoke(
        "propose_portfolio_account_change",
        {"action": "create", "name": "A股主账户", "market": "cn"},
    )
    assert payload["kind"] == "portfolio_account_create"
    assert payload["proposal"] == {
        "name": "A股主账户",
        "broker": None,
        "market": "cn",
        "base_currency": "CNY",
        "owner_id": None,
    }


def test_portfolio_account_create_defaults_market_to_cn():
    """描述里承诺 market 可选、默认 cn；handler 必须真的接受省略值。"""
    payload = _invoke(
        "propose_portfolio_account_change",
        {"action": "create", "name": "A股主账户"},
    )
    assert payload["proposal"]["market"] == "cn"
    assert payload["proposal"]["base_currency"] == "CNY"


def test_portfolio_account_create_validates_name_and_market():
    assert "name" in _invoke(
        "propose_portfolio_account_change", {"action": "create", "name": "   ", "market": "cn"}
    )["error"]
    assert "market" in _invoke(
        "propose_portfolio_account_change", {"action": "create", "name": "x", "market": "eu"}
    )["error"]


def test_portfolio_account_deactivate_envelope():
    payload = _invoke(
        "propose_portfolio_account_change", {"action": "deactivate", "account_id": 3}
    )
    assert payload["kind"] == "portfolio_account_deactivate"
    assert payload["proposal"] == {"account_id": 3}


def test_portfolio_account_update_envelope():
    payload = _invoke(
        "propose_portfolio_account_change",
        {"action": "update", "account_id": 3, "name": "新名字", "is_active": True},
    )
    assert payload["kind"] == "portfolio_account_update"
    assert payload["proposal"] == {"account_id": 3, "name": "新名字", "is_active": True}


def test_portfolio_account_update_requires_a_field_and_valid_market():
    assert "至少需要" in _handle_propose_portfolio_account_change(
        action="update", account_id=3
    )["error"]
    assert "market" in _handle_propose_portfolio_account_change(
        action="update", account_id=3, market="eu"
    )["error"]


def test_portfolio_account_change_rejects_unknown_action():
    payload = _invoke("propose_portfolio_account_change", {"action": "merge", "account_id": 1})
    assert "action" in payload["error"]


# ============================================================
# propose_signal_change
# ============================================================

_SIGNAL_CREATE_PAYLOAD = {
    "stock_code": "600519",
    "market": "cn",
    "source_type": "agent",
    "trigger_source": "chat",
    "action": "buy",
}


def test_signal_create_envelope():
    payload = _invoke(
        "propose_signal_change",
        {"action": "create", "payload": _SIGNAL_CREATE_PAYLOAD},
    )
    assert payload["kind"] == "signal_create"
    assert payload["proposal"] == _SIGNAL_CREATE_PAYLOAD


def test_signal_create_requires_complete_payload():
    payload = _invoke(
        "propose_signal_change", {"action": "create", "payload": {"stock_code": "600519"}}
    )
    assert "payload 缺少字段" in payload["error"]


def test_signal_status_envelope():
    payload = _invoke(
        "propose_signal_change",
        {"action": "status", "signal_id": 5, "status": "active"},
    )
    assert payload["kind"] == "signal_status"
    assert payload["proposal"] == {"signal_id": 5, "status": "active"}


def test_signal_status_rejects_unknown_status():
    payload = _invoke(
        "propose_signal_change",
        {"action": "status", "signal_id": 5, "status": "draft"},
    )
    assert "status" in payload["error"]


def test_signal_feedback_envelope():
    payload = _invoke(
        "propose_signal_change",
        {
            "action": "feedback",
            "signal_id": 5,
            "feedback_value": "not_useful",
            "reason_code": "late",
            "note": "太慢了",
        },
    )
    assert payload["kind"] == "signal_feedback"
    assert payload["proposal"] == {
        "signal_id": 5,
        "feedback_value": "not_useful",
        "reason_code": "late",
        "note": "太慢了",
    }


def test_signal_feedback_rejects_unknown_value():
    payload = _invoke(
        "propose_signal_change",
        {"action": "feedback", "signal_id": 5, "feedback_value": "wrong"},
    )
    assert "feedback_value" in payload["error"]


def test_signal_reassess_envelope():
    proposal = {"source_report_id": 12, "decision_profile": "balanced", "persist": True}
    payload = _invoke("propose_signal_change", {"action": "reassess", "payload": proposal})
    assert payload["kind"] == "signal_reassess"
    assert payload["proposal"] == proposal


def test_signal_reassess_drops_extra_payload_keys():
    """DecisionSignalReassessRequest 是 extra="forbid"：转发多余键会让确认时 422。"""
    payload = _invoke(
        "propose_signal_change",
        {
            "action": "reassess",
            "payload": {
                "source_report_id": 12,
                "decision_profile": "balanced",
                "persist": False,
                "symbol": "600519",
            },
        },
    )
    assert payload["kind"] == "signal_reassess"
    assert payload["proposal"] == {
        "source_report_id": 12,
        "decision_profile": "balanced",
        "persist": False,
    }


def test_signal_change_rejects_unknown_action():
    payload = _invoke("propose_signal_change", {"action": "delete", "signal_id": 5})
    assert "action" in payload["error"]


# ============================================================
# propose_paper_reset
# ============================================================

def test_paper_reset_envelope():
    payload = _invoke("propose_paper_reset", {"initial_capital": 100000})
    assert payload["kind"] == "paper_reset"
    assert payload["proposal"]["initial_capital"] == 100000.0


def test_paper_reset_omits_empty_capital():
    result = _handle_propose_paper_reset()
    assert result["proposal"] == {}
    assert result["kind"] == "paper_reset"


def test_paper_reset_rejects_non_positive_capital():
    assert "initial_capital" in _handle_propose_paper_reset(initial_capital=-1)["error"]


# ============================================================
# registration / policy
# ============================================================

def test_new_proposal_tools_declare_proposal_policy():
    for tool in (
        propose_portfolio_ledger_entry,
        propose_portfolio_account_change,
        propose_signal_change,
        propose_paper_reset,
    ):
        assert tool.category == "action"
        assert tool.policy.read_only is False
        assert tool.policy.side_effects == ["write"]
        assert tool.policy.execution_mode == "proposal"
        assert tool.policy.risk_level == "high"
        assert tool.policy.cancellation_safe is False


def test_signal_feedback_emits_action_proposal_event():
    from src.agent.factory import get_tool_registry

    events = []
    result = _handle_propose_signal_change(
        action="status", signal_id=5, status="active"
    )
    tc = SimpleNamespace(name="propose_signal_change")
    out = _maybe_emit_action_event(
        tc,
        json.dumps(result, ensure_ascii=False),
        events.append,
        step=1,
        tool_registry=get_tool_registry(),
    )
    assert events[0]["kind"] == "signal_status"
    assert json.loads(out) == {"message": result["summary"]}


# ============================================================
# propose_history_deletion / propose_intelligence_source_change
# propose_config_update / propose_scheduler_run / propose_notification_test
# ============================================================

def test_history_delete_records_envelope():
    payload = _invoke("propose_history_deletion", {
        "action": "records", "record_ids": [1, 2],
    })
    assert payload["kind"] == "history_delete_records"
    assert payload["proposal"]["record_ids"] == [1, 2]


def test_history_delete_by_code_requires_code():
    payload = _invoke("propose_history_deletion", {"action": "by_code"})
    assert payload["error"]


def test_history_delete_by_code_envelope_names_the_target():
    payload = _invoke("propose_history_deletion", {
        "action": "by_code", "stock_code": "600519",
    })
    assert payload["kind"] == "history_delete_by_code"
    assert payload["proposal"] == {"stock_code": "600519"}
    assert "600519" in payload["summary"]


def test_history_delete_by_code_is_stock_scoped_for_the_runner_guard():
    from src.agent.factory import get_tool_registry
    from src.agent.stock_scope import StockScope
    from src.agent.tools.execution import _guard_tool_stock_scope

    registry = get_tool_registry()
    tool = registry.resolve("propose_history_deletion")
    assert any(param.name == "stock_code" for param in tool.parameters)
    assert tool.policy.scope_dimensions == ["stock"]

    scope = StockScope(expected_stock_code="600519", allowed_stock_codes={"600519"})
    denied = _guard_tool_stock_scope(
        registry, "propose_history_deletion", {"stock_code": "000001"}, scope,
    )
    assert denied is not None
    assert denied["error"] == "stock_scope_violation"
    assert _guard_tool_stock_scope(
        registry, "propose_history_deletion", {"stock_code": "600519"}, scope,
    ) is None


def test_history_delete_records_rejects_invalid_ids():
    payload = _invoke("propose_history_deletion", {
        "action": "records", "record_ids": [0],
    })
    assert payload["error"]
    assert _invoke("propose_history_deletion", {"action": "records"})["error"]


def test_intelligence_source_create_envelope_is_schema_shaped(monkeypatch):
    # ``_validate_url`` resolves DNS; keep the unit test deterministic and offline.
    monkeypatch.setattr(
        "src.services.intelligence_service.socket.getaddrinfo",
        lambda host, port: [(None, None, None, "", ("93.184.216.34", 0))],
    )
    payload = _invoke("propose_intelligence_source_change", {
        "action": "create",
        "payload": {
            "name": "我的 RSS",
            "url": "https://example.com/feed.xml",
        },
    })
    assert payload["kind"] == "intelligence_source_create"
    # Defaults mirror IntelligenceSourceCreateRequest so confirm never 422s.
    assert payload["proposal"] == {
        "name": "我的 RSS",
        "url": "https://example.com/feed.xml",
        "source_type": "rss",
        "enabled": True,
        "scope_type": "market",
        "scope_value": None,
        "market": "cn",
    }


def test_intelligence_source_create_rejects_missing_required_fields():
    assert _invoke("propose_intelligence_source_change", {"action": "create"})["error"]
    assert _invoke("propose_intelligence_source_change", {
        "action": "create", "payload": {"url": "https://example.com/feed.xml"},
    })["error"]
    assert _invoke("propose_intelligence_source_change", {
        "action": "create",
        "payload": {"name": "n", "url": "https://e.com", "source_type": "unknown"},
    })["error"]
    assert _invoke("propose_intelligence_source_change", {
        "action": "create",
        "payload": {"name": "n", "url": "https://e.com", "scope_type": "symbol"},
    })["error"]
    assert _invoke("propose_intelligence_source_change", {
        "action": "create",
        "payload": {"name": "n", "url": "ftp://e.com/feed"},
    })["error"]


def test_intelligence_source_create_rejects_urls_the_service_will_reject():
    for url in (
        "https://user:pass@example.com/feed.xml",
        "http://localhost:8080/feed",
        "http://127.0.0.1/feed",
        "http://feed.local/rss",
    ):
        payload = _invoke("propose_intelligence_source_change", {
            "action": "create",
            "payload": {"name": "n", "url": url},
        })
        assert payload["error"], url
        assert "payload.url" in payload["error"]


def test_intelligence_defaults_envelope():
    payload = _invoke("propose_intelligence_source_change", {"action": "defaults"})
    assert payload["kind"] == "intelligence_defaults_create"
    assert payload["proposal"] == {}


def test_scheduler_run_envelope():
    # run-now takes no request body; the proposal must not promise an option.
    payload = _invoke("propose_scheduler_run", {})
    assert payload["kind"] == "scheduler_run"
    assert payload["proposal"] == {}


def test_notification_test_envelope():
    payload = _invoke("propose_notification_test", {"channel": "feishu"})
    assert payload["kind"] == "notification_test"
    assert payload["proposal"]["channel"] == "feishu"


def test_notification_test_normalizes_and_bounds_text():
    payload = _invoke("propose_notification_test", {
        "channel": " Feishu ",
        "title": "t" * 100,
        "content": "c" * 1200,
    })
    assert payload["proposal"]["channel"] == "feishu"
    assert len(payload["proposal"]["title"]) == 80
    assert len(payload["proposal"]["content"]) == 1000


def test_notification_test_rejects_unknown_channel():
    payload = _invoke("propose_notification_test", {"channel": "nope"})
    assert payload["error"]


def test_new_write_proposal_tools_declare_proposal_policy():
    from src.agent.tools.action_tools import (
        propose_config_update,
        propose_history_deletion,
        propose_intelligence_source_change,
        propose_notification_test,
        propose_scheduler_run,
    )

    for tool in (
        propose_history_deletion,
        propose_intelligence_source_change,
        propose_config_update,
        propose_scheduler_run,
        propose_notification_test,
    ):
        assert tool.category == "action"
        assert tool.policy.read_only is False
        assert tool.policy.side_effects == ["write"]
        assert tool.policy.execution_mode == "proposal"
        assert tool.policy.risk_level == "high"
        assert tool.policy.cancellation_safe is False

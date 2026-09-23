"""Tests for the confirmable write-action proposal tools."""

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from src.agent.runner import _maybe_emit_action_proposal
from src.agent.tools.action_tools import (
    SUPPORTED_MARKETS,
    SUPPORTED_SIDES,
    _handle_propose_portfolio_trade,
    propose_portfolio_trade_tool,
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


# ============================================================
# propose_watchlist_change
# ============================================================

from src.agent.tools.action_tools import (  # noqa: E402
    ALL_ACTION_TOOLS,
    SUPPORTED_WATCHLIST_ACTIONS,
    _ACTION_KINDS,
    _KIND_VERBS,
    _handle_propose_watchlist_change,
    propose_watchlist_change_tool,
)


def _patch_named_lists(names=("短线池",), keys=None):
    """``keys`` 直接给出配置里的原始 key，用于构造非规范 key（如 ``WATCHLIST_MY-LIST``，注意
    必须仍以大写 ``WATCHLIST_`` 开头，否则会被 ``list_named_watchlists`` 直接滤掉）；
    省略时按 ``WATCHLIST_<NAME>`` 生成（``list_named_watchlists`` 的 name 即 key 后缀小写）。
    """
    service = mock.MagicMock()
    if keys is None:
        keys = [f"WATCHLIST_{n.upper()}" for n in names]

    def _get_config(include_schema=True, mask_token="******"):
        # 断言侧会检查 get_config 的调用参数：真实 get_config(include_schema=True) 返回的是带
        # schema/掩码的结构而不是裸 items，若实现里漏传 False，只有断言过这个参数才抓得到。
        return {
            "config_version": "v1",
            "items": [{"key": key, "value": ""} for key in keys],
        }

    service.get_config.side_effect = _get_config
    return mock.patch(
        "src.services.system_config_service.SystemConfigService", return_value=service
    )


def test_watchlist_tool_parameter_is_named_symbol_not_stock_code():
    """参数名必须是 symbol。

    叫 stock_code 会被 `_is_stock_scoped_tool`（`src/agent/tools/execution.py:189-193`）视为受股票
    范围约束的工具，`_guard_tool_stock_scope` 会在单股会话里对跨标的请求硬阻断
    （`retriable: False`，模型无法绕过），从而拒绝用户明确点名的标的。这条断言把该决定钉住，
    防止将来有人「为了和请求体字段名对齐」把它改回去。
    """
    declared = {param.name for param in propose_watchlist_change_tool.parameters}
    assert "symbol" in declared
    assert "stock_code" not in declared


def test_watchlist_tool_registered_in_all_action_tools():
    names = [t.name for t in ALL_ACTION_TOOLS]
    assert names == ["propose_portfolio_trade", "propose_watchlist_change"]
    assert propose_watchlist_change_tool.category == "action"
    assert propose_watchlist_change_tool.policy.read_only is True
    assert propose_watchlist_change_tool.policy.cancellation_safe is False


def test_watchlist_add_proposal_shape():
    result = _handle_propose_watchlist_change(action="add", symbol=" 600519 ")
    assert "error" not in result
    assert result["kind"] == "watchlist_add"
    # proposal 用的是请求体字段名 stock_code，与工具参数名 symbol 刻意不同
    assert result["proposal"] == {"stock_code": "600519", "list_name": None}
    assert result["summary"] == "把「600519」加入自选"


def test_watchlist_remove_proposal_shape():
    result = _handle_propose_watchlist_change(action="remove", symbol="AAPL")
    assert result["kind"] == "watchlist_remove"
    assert result["summary"] == "把「AAPL」移出自选"


def test_watchlist_named_list_is_echoed_in_summary():
    with _patch_named_lists():
        result = _handle_propose_watchlist_change(
            action="add", symbol="600519", list_name="短线池"
        )
    assert "error" not in result
    assert result["proposal"] == {"stock_code": "600519", "list_name": "短线池"}
    assert result["summary"] == "把「600519」加入自选列表「短线池」"


def test_watchlist_helpers_request_config_without_schema():
    """list_named_watchlists 必须传 include_schema=False，否则拿到的是 schema 结构而非裸 items。"""
    with _patch_named_lists() as patched:
        _handle_propose_watchlist_change(action="add", symbol="600519", list_name="短线池")
    service = patched.return_value
    assert service.get_config.call_args_list, "get_config 从未被调用"
    assert all(
        call.kwargs.get("include_schema") is False for call in service.get_config.call_args_list
    )


def test_watchlist_rejects_unknown_named_list_and_lists_valid_names():
    with _patch_named_lists(names=("短线池", "长线池")):
        result = _handle_propose_watchlist_change(
            action="add", symbol="600519", list_name="编的池子"
        )
    assert "不存在" in result["error"]
    assert "短线池" in result["error"]
    assert "长线池" in result["error"]


def test_watchlist_named_list_matching_uses_resolved_key():
    """"My List" 应能命中 WATCHLIST_MY_LIST，而不是按小写名比对时被判为不存在。"""
    with _patch_named_lists(names=("my_list",)):
        result = _handle_propose_watchlist_change(
            action="add", symbol="600519", list_name="My List"
        )
    assert "error" not in result
    assert result["proposal"]["list_name"] == "My List"


def test_watchlist_rejects_bad_action_and_bad_code():
    assert "action" in _handle_propose_watchlist_change(action="toggle", symbol="600519")["error"]
    assert "不是合法的股票代码格式" in _handle_propose_watchlist_change(
        action="add", symbol="600519;;;"
    )["error"]


def test_watchlist_appends_reason():
    result = _handle_propose_watchlist_change(
        action="add", symbol="600519", reason="突破前高"
    )
    assert result["summary"].endswith("（突破前高）")


def test_watchlist_envelope_is_json_serializable():
    result = _handle_propose_watchlist_change(action="add", symbol="600519")
    json.dumps(result, ensure_ascii=False)


def test_watchlist_action_kinds_cover_the_supported_actions():
    """kind 必须与 action 集合一一对应：漏一个就会在确认卡片上标错动作。

    ``SUPPORTED_WATCHLIST_ACTIONS = tuple(_ACTION_KINDS)``，所以第一条断言其实是恒等式
    （``set(tuple(d)) == set(d)`` 对任何 dict 都成立），只钉住推导关系；真正的守卫是第二条。
    """
    assert set(_ACTION_KINDS) == set(SUPPORTED_WATCHLIST_ACTIONS)
    # 这条等式是唯一守卫：去掉它，往 _ACTION_KINDS 一边加成员不会有任何测试变红
    # （新增 action 若无测试覆盖，_KIND_VERBS 的 KeyError 只在运行时才炸）。
    assert set(_KIND_VERBS) == set(_ACTION_KINDS.values())


def test_watchlist_hint_skips_names_that_cannot_round_trip():
    """非规范 key ``WATCHLIST_MY-LIST`` 的 name 是「my-list」，但
    ``resolve_watchlist_key("my-list")`` 会把 ``-`` 折成 ``_`` 得到 ``WATCHLIST_MY_LIST``：
    把它列进「可用列表」等于推荐一个随后必然被拒的名字，模型会照着自己的提示原地重试。

    注意不能用小写 ``watchlist_foo`` 当夹具：``list_named_watchlists`` 自己会按
    ``startswith("WATCHLIST_")`` 把它整个滤掉，被测的往返过滤根本不会执行。
    """
    with _patch_named_lists(keys=["WATCHLIST_MY-LIST"]):
        result = _handle_propose_watchlist_change(
            action="add", symbol="600519", list_name="短线池"
        )
    assert "my-list" not in result["error"]
    assert "list_name" in result["error"]
    # 不能教模型「省略 list_name 用默认自选」：用户点名了某个列表，静默改用默认等于替用户
    # 改写目标；必须先取到用户同意，或给出新建/重命名这类替代方案。
    assert "省略 list_name" not in result["error"]
    assert "用户同意" in result["error"]


def test_reason_is_length_bounded_in_the_action_tools():
    """reason 会流进 summary → SSE 事件 → 确认卡片，模型不能往里塞几 KB 文本。"""
    watchlist = _handle_propose_watchlist_change(
        action="add", symbol="600519", reason="很" * 5000
    )
    assert watchlist["summary"].endswith("（" + "很" * 200 + "）")
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
    for name in ("propose_portfolio_trade", "propose_watchlist_change"):
        tool = registry.resolve(name)
        assert tool is not None, f"{name} 未注册"
        assert _THINKING_TOOL_LABELS.get(name), f"{name} 缺少思考过程标签"
        assert TOOL_DISPLAY_NAMES.get(name), f"{name} 缺少中文展示名"


def test_emitter_rejects_unknown_kind():
    events = []
    tc = SimpleNamespace(name="propose_portfolio_trade")
    raw = json.dumps({"kind": "drop_table", "summary": "x", "proposal": {"a": 1}})
    out = _maybe_emit_action_proposal(tc, raw, events.append, step=1)
    assert out == raw
    assert events == []


def test_emitter_rejects_malformed_summary_and_proposal():
    events = []
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
        assert _maybe_emit_action_proposal(tc, raw, events.append, step=1) == raw
    assert events == []


@pytest.mark.parametrize(
    ("tool_name", "envelope", "expected_kind"),
    [
        (
            "propose_alert",
            {"kind": "alert", "summary": "「600519」价格上穿 ¥1800",
             "proposal": {"target": "600519", "alert_type": "price_cross"}},
            "alert",
        ),
        (
            "propose_portfolio_trade",
            {"kind": "portfolio_trade", "summary": "在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706",
             "proposal": {"account_id": 1, "symbol": "005827", "side": "buy"}},
            "portfolio_trade",
        ),
        (
            "propose_watchlist_change",
            {"kind": "watchlist_add", "summary": "把「600519」加入自选",
             "proposal": {"stock_code": "600519", "list_name": None}},
            "watchlist_add",
        ),
    ],
)
def test_emitter_accepts_every_proposal_tool_envelope(tool_name, envelope, expected_kind):
    """三个提案工具的每种信封都必须被发射。

    漏掉任一工具名（`_PROPOSAL_TOOL_NAMES`）或任一 kind（`_ALLOWED_PROPOSAL_KINDS`），
    该动作的提案会被静默丢弃、前端永远等不到卡片，而旧版只用 watchlist 一个工具的写法
    对 `propose_portfolio_trade` 缺失是空转的（实测：删掉它 46 个测试仍全绿）。
    """
    events = []
    tc = SimpleNamespace(name=tool_name)
    raw = json.dumps(envelope, ensure_ascii=False)
    out = _maybe_emit_action_proposal(tc, raw, events.append, step=2)

    assert events[0]["type"] == "action_proposal"
    assert events[0]["kind"] == expected_kind
    assert json.loads(out) == {"message": envelope["summary"]}


def test_every_proposable_kind_is_accepted_by_the_runner():
    """工具能产出的 kind 必须恰好在 runner 白名单内。

    这条把 runner 侧的白名单与工具侧的实际产出一致性钉住：漏一个，该动作的提案会被
    `_maybe_emit_action_proposal` 静默丢弃（工具结果按原样放行），前端永远等不到卡片，
    而且没有任何测试会红。白名单里多一个死值同样会被这条抓出来。
    """
    from src.agent.runner import _ALLOWED_PROPOSAL_KINDS
    from src.agent.tools.action_tools import _ACTION_KINDS

    producible = {"alert", "portfolio_trade", *set(_ACTION_KINDS.values())}
    assert _ALLOWED_PROPOSAL_KINDS == producible


def test_proposal_tool_names_match_the_registry():
    """runner 的提案工具集合必须与注册表里所有 propose_* 工具一致。

    硬编码的工具名列表无法发现「新注册了一个提案工具但没加进 runner 集合」——
    那种情况下该工具的提案会被静默丢弃，且现有测试全绿。这条从注册表反查，
    把那个方向也堵上。
    """
    from src.agent.factory import get_tool_registry
    from src.agent.runner import _PROPOSAL_TOOL_NAMES

    registered = {n for n in get_tool_registry().list_names() if n.startswith("propose_")}
    runner_names = set(_PROPOSAL_TOOL_NAMES)
    assert registered == runner_names, (
        f"提案工具集合不一致：仅注册表有 {sorted(registered - runner_names)}；"
        f"仅 runner 有 {sorted(runner_names - registered)}"
    )

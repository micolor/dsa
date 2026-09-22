# -*- coding: utf-8 -*-
"""Agent 工具：把需要用户确认的写操作包装成「提案」。

与 ``alert_tools.propose_alert`` 同一套约定：工具本身**只读**，只做校验并返回
``{"kind", "summary", "proposal"}`` 信封，其中 ``proposal`` 就是对应写接口的请求体。
runner 读到该信封后发出 ``action_proposal`` SSE 事件，真正的写入由用户在 Web 端确认后、
用浏览器会话调用既有 REST 接口完成。工具从不持久化任何东西、从不调用写接口，因此 AI
始终不持有写权限。

``cancellation_safe=False`` 让本模块的工具被 ``tool_surface`` 挡在 Codex 只读面外
（与 ``propose_alert`` 一致）。
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
from src.services.watchlist_service import (
    list_named_watchlists,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
)

logger = logging.getLogger(__name__)

_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=[],
    permissions=[],
    scope_dimensions=[],
    # 与 propose_alert 一致：交互式确认流工具，不适用于 Codex 只读面。
    cancellation_safe=False,
)

SUPPORTED_SIDES = ("buy", "sell")
SUPPORTED_MARKETS = ("cn", "hk", "us", "jp", "kr", "tw")


def _fmt_num(value: Any) -> str:
    """Format a number without a trailing ``.0`` for whole values."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _fmt_amount(value: float) -> str:
    """Format a cash amount with two decimals."""
    return f"{value:.2f}"


def _with_currency(text: str, currency: str) -> str:
    """Prefix a rendered number with an explicit currency code (``CNY 1.6706``).

    用代码而不是 ``¥``：``¥`` 在 CNY 与 JPY 之间有歧义（``market`` 枚举里确实有 ``jp``）。
    """
    return f"{currency} {text}" if currency else text


def _check_number(
    value: Any, field: str, *, allow_zero: bool
) -> Tuple[Optional[float], Optional[str]]:
    """Validate a numeric field; returns (value, error)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{field} 必须是数字，收到 {value!r}"
    # JSON 允许 1e999 这种字面量，float() 会得到 inf；而 inf/nan 对任何比较都返回
    # False，下面的范围守卫拦不住它们，必须先用 isfinite 挡掉。
    if not math.isfinite(number):
        return None, f"{field} 必须是有限数字，收到 {value!r}"
    if allow_zero and number < 0:
        return None, f"{field} 必须 >= 0，收到 {number}"
    if not allow_zero and number <= 0:
        return None, f"{field} 必须 > 0，收到 {number}"
    return number, None


# 提案理由长度上限：reason 会进入 summary → SSE 事件 → 确认卡片，避免模型塞长文本。
_REASON_LIMIT = 200


def _normalize_reason(reason: Any) -> str:
    """收敛提案理由：去空白并限制长度，避免模型把长文本塞进卡片与 SSE 事件。"""
    return str(reason or "").strip()[:_REASON_LIMIT]


# ============================================================
# propose_portfolio_trade
# ============================================================

def _format_account_choices(accounts: List[Dict[str, Any]]) -> str:
    """Render the account list as a one-line hint so the model can self-correct."""
    active = [a for a in accounts if a.get("is_active")]
    if not active:
        return "当前没有已激活的账户，请先在持仓页创建账户"
    # 本函数在 _resolve_account 的 try 块外被调用，缺 id 时不能抛 KeyError（否则错误
    # 处理路径自己会崩），故一律用 .get 且不做 int() 转换。
    return "；".join(f"{a.get('id', '?')}: {a.get('name') or '未命名'}" for a in active)


def _resolve_account(account_id: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve ``account_id`` to an active account; returns (account, error)."""
    try:
        parsed_id = int(account_id)
    except (TypeError, ValueError):
        return None, f"account_id 必须是整数，收到 {account_id!r}"

    try:
        from src.services.portfolio_service import PortfolioService
    except Exception as exc:
        logger.warning("propose_portfolio_trade unavailable: %s", exc)
        return None, f"持仓模块不可用: {exc}"

    try:
        accounts = PortfolioService().list_accounts(include_inactive=True)
    except Exception as exc:
        logger.warning("propose_portfolio_trade list_accounts failed: %s", exc)
        return None, f"读取账户失败: {exc}"

    account = next((a for a in accounts if int(a.get("id", -1)) == parsed_id), None)
    if account is None:
        return None, (
            f"account_id {parsed_id} 不存在；可用账户：{_format_account_choices(accounts)}"
        )
    if not account.get("is_active"):
        return None, (
            f"账户「{account.get('name') or parsed_id}」({parsed_id}) 已停用，不能录入交易；"
            f"可用账户：{_format_account_choices(accounts)}"
        )
    return account, None


def _handle_propose_portfolio_trade(
    account_id: Any,
    symbol: str,
    side: str,
    quantity: Any,
    price: Any,
    trade_date: str = "",
    fee: Any = 0,
    tax: Any = 0,
    market: str = "",
    currency: str = "",
    note: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """Validate one trade entry and return a proposal; persists nothing.

    校验逐条对齐 ``PortfolioTradeCreateRequest``（``api/v1/schemas/portfolio.py:45-57``），
    因此「提案通过」意味着确认时的 ``POST /api/v1/portfolio/trades`` 不会因参数被拒。
    """
    account, err = _resolve_account(account_id)
    if err:
        return {"error": err}

    norm_side = str(side or "").strip().lower()
    if norm_side not in SUPPORTED_SIDES:
        return {"error": f"side 必须是 {'/'.join(SUPPORTED_SIDES)}，收到 {side!r}"}

    norm_symbol = str(symbol or "").strip()
    if not norm_symbol:
        return {"error": "symbol 不能为空"}
    if len(norm_symbol) > 16:
        return {"error": f"symbol 过长（最多 16 字符）: {norm_symbol!r}"}

    norm_quantity, err = _check_number(quantity, "quantity", allow_zero=False)
    if err:
        return {"error": err}
    norm_price, err = _check_number(price, "price", allow_zero=False)
    if err:
        return {"error": err}
    norm_fee, err = _check_number(fee, "fee", allow_zero=True)
    if err:
        return {"error": err}
    norm_tax, err = _check_number(tax, "tax", allow_zero=True)
    if err:
        return {"error": err}

    raw_date = str(trade_date or "").strip()
    if raw_date:
        try:
            norm_date = date.fromisoformat(raw_date)
        except ValueError:
            return {"error": f"trade_date 必须是 YYYY-MM-DD，收到 {raw_date!r}"}
    else:
        # 记账默认今天；用户要记历史交易必须显式给出日期，且日期会显示在卡片上。
        norm_date = date.today()

    norm_market = str(market or "").strip().lower() or None
    if norm_market and norm_market not in SUPPORTED_MARKETS:
        return {"error": f"market 必须是 {'/'.join(SUPPORTED_MARKETS)} 之一，收到 {market!r}"}

    norm_currency = str(currency or "").strip().upper()
    if norm_currency and not 3 <= len(norm_currency) <= 8:
        return {"error": f"currency 必须是 3-8 位货币代码（如 CNY/USD/HKD），收到 {currency!r}"}

    proposal: Dict[str, Any] = {
        "account_id": int(account["id"]),
        "symbol": norm_symbol,
        "trade_date": norm_date.isoformat(),
        "side": norm_side,
        "quantity": norm_quantity,
        "price": norm_price,
        "fee": norm_fee,
        "tax": norm_tax,
    }
    if norm_market:
        proposal["market"] = norm_market
    if norm_currency:
        proposal["currency"] = norm_currency
    norm_note = str(note or "").strip()
    if norm_note:
        proposal["note"] = norm_note[:255]

    # 成交价币种优先用模型显式给出的（标的计价币种），其次退到账户本位币；后者只用于
    # 文案展示，不写进 proposal —— 美元账户买 A 股时价仍是 CNY，不能混为一谈。
    display_currency = norm_currency or str(account.get("base_currency") or "").strip().upper()

    action_label = "买入" if norm_side == "buy" else "卖出"
    if norm_side == "buy":
        # 买入是现金流出：成交额 + 手续费 + 税费
        amount = norm_quantity * norm_price + norm_fee + norm_tax
        amount_label = "约支出"
    else:
        # 卖出是现金流入：成交额 - 手续费 - 税费
        amount = norm_quantity * norm_price - norm_fee - norm_tax
        amount_label = "约收入"
    price_text = _with_currency(_fmt_num(norm_price), display_currency)
    amount_text = _with_currency(_fmt_amount(amount), display_currency)
    summary = (
        f"在「{account.get('name') or '未命名账户'}」记一笔{action_label} "
        f"{norm_symbol} {_fmt_num(norm_quantity)} @ {price_text}"
        f"（{norm_date.isoformat()}，{amount_label} {amount_text}）"
    )
    norm_reason = _normalize_reason(reason)
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"

    return {"kind": "portfolio_trade", "summary": summary, "proposal": proposal}


propose_portfolio_trade_tool = ToolDefinition(
    name="propose_portfolio_trade",
    description=(
        "当用户要求把某笔买入/卖出记入持仓账户，或对话中已确认份额、成本、账户时，"
        "用本工具生成一条持仓录入提案交给用户确认。只生成提案，不写入任何数据。\n"
        "account_id 必填：先用 get_portfolio_snapshot 读取账户的 account_id / account_name；"
        "多个账户而用户未指明时必须先追问用户，不要猜。\n"
        "trade_date 省略时按今天记账；用户要补记历史交易必须显式给出 YYYY-MM-DD。"
    ),
    parameters=[
        ToolParameter(
            name="account_id",
            type="integer",
            description="目标账户 id，取自 get_portfolio_snapshot 返回的 accounts[].account_id",
            required=True,
        ),
        ToolParameter(name="symbol", type="string", description="股票/基金代码，如 600519 / 005827 / HK00700 / AAPL", required=True),
        ToolParameter(
            name="side",
            type="string",
            description="交易方向",
            required=True,
            enum=list(SUPPORTED_SIDES),
        ),
        ToolParameter(name="quantity", type="number", description="成交数量（股或份），必须大于 0", required=True),
        ToolParameter(name="price", type="number", description="成交单价/净值，必须大于 0", required=True),
        ToolParameter(
            name="trade_date",
            type="string",
            description="成交日期 YYYY-MM-DD；省略则按今天记账",
            required=False,
        ),
        ToolParameter(name="fee", type="number", description="手续费，默认 0", required=False, default=0),
        ToolParameter(name="tax", type="number", description="税费，默认 0", required=False, default=0),
        ToolParameter(
            name="market",
            type="string",
            description="市场；省略时由后端按账户与代码推断",
            required=False,
            enum=list(SUPPORTED_MARKETS),
        ),
        ToolParameter(
            name="currency",
            type="string",
            description=(
                "成交价计价币种代码（如 CNY/USD/HKD）；仅当标的计价币种与账户本位币不同时才给出"
            ),
            required=False,
        ),
        ToolParameter(name="note", type="string", description="可选备注，写入交易记录", required=False),
        ToolParameter(name="reason", type="string", description="可选提案理由，用于在卡片上向用户说明", required=False),
    ],
    handler=_handle_propose_portfolio_trade,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_watchlist_change
# ============================================================

# action → 提案 kind。用映射而不是三元，避免将来新增 action 时被静默归到 remove。
_ACTION_KINDS: Dict[str, str] = {
    "add": "watchlist_add",
    "remove": "watchlist_remove",
}

# 摘要动词按 kind 取，与 _ACTION_KINDS 同源：否则新增 action 时 summary 会把动作说成「移出」
# 而 kind 是别的值，卡片文案与 kind 自相矛盾。查不到直接 KeyError，不静默兜底。
_KIND_VERBS: Dict[str, str] = {
    "watchlist_add": "加入",
    "watchlist_remove": "移出",
}

SUPPORTED_WATCHLIST_ACTIONS = tuple(_ACTION_KINDS)


def _resolve_named_list(list_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a named watchlist; returns (raw_name, error)."""
    raw = str(list_name or "").strip()
    if not raw:
        return None, None
    try:
        from src.services.system_config_service import SystemConfigService
    except Exception as exc:
        logger.warning("propose_watchlist_change unavailable: %s", exc)
        return None, f"配置模块不可用: {exc}"

    try:
        named = list_named_watchlists(SystemConfigService())
    except Exception as exc:
        logger.warning("propose_watchlist_change list_named_watchlists failed: %s", exc)
        return None, f"读取自选列表失败: {exc}"

    # 用与写接口相同的 key 解析规则比对：否则「My List」这类名字会因大小写/分隔符差异被误判为不存在。
    wanted_key = resolve_watchlist_key(raw)
    if any(n["key"] == wanted_key for n in named):
        return raw, None

    # 提示里只列能往返的名字：非规范 key（如 WATCHLIST_MY-LIST）的 name 是「my-list」，
    # resolve_watchlist_key 会把它折成 WATCHLIST_MY_LIST，模型照着提示重试仍会被拒，只会原地打转。
    available = [
        n["name"] for n in named if resolve_watchlist_key(n["name"]) == n["key"]
    ]
    if not available:
        return None, (
            f"自选列表「{raw}」不存在；没有可用的命名自选列表"
            "（未配置，或名字不规范、无法通过 list_name 指定），"
            "请让用户确认改用默认自选（需用户同意），或建议用户新建/重命名该列表"
        )
    return None, f"自选列表「{raw}」不存在；可用列表：{'、'.join(available)}"


def _handle_propose_watchlist_change(
    action: str,
    symbol: str,
    list_name: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """Validate one watchlist add/remove and return a proposal; persists nothing.

    股票代码格式与写接口共享同一份校验（``src/services/watchlist_service.py`` 的
    ``validate_and_normalize_stock_code`` 与同一条 ``STOCK_CODE_RE``），因此「提案通过」
    意味着确认时的 ``POST /api/v1/stocks/watchlist/add``（或 ``/remove``）不会因代码格式被拒。

    列表名是本工具**额外**加的、严于端点的检查：端点并不校验 ``list_name``，未知名字会被
    直接解析成新 key 从而创建出新的命名列表；不在这里挡住的话，模型随手编一个列表名就会
    静默产生配置写入副作用。
    """
    norm_action = str(action or "").strip().lower()
    if norm_action not in SUPPORTED_WATCHLIST_ACTIONS:
        return {
            "error": f"action 必须是 {'/'.join(SUPPORTED_WATCHLIST_ACTIONS)}，收到 {action!r}"
        }

    try:
        norm_code = validate_and_normalize_stock_code(str(symbol or ""))
    except ValueError as exc:
        return {"error": str(exc)}

    norm_list_name, err = _resolve_named_list(list_name)
    if err:
        return {"error": err}

    target = f"自选列表「{norm_list_name}」" if norm_list_name else "自选"
    kind = _ACTION_KINDS[norm_action]
    summary = f"把「{norm_code}」{_KIND_VERBS[kind]}{target}"
    norm_reason = _normalize_reason(reason)
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"

    return {
        "kind": kind,
        "summary": summary,
        "proposal": {"stock_code": norm_code, "list_name": norm_list_name},
    }


propose_watchlist_change_tool = ToolDefinition(
    name="propose_watchlist_change",
    description=(
        "当用户要求把某只股票加入自选或从自选移除时，用本工具生成提案交给用户确认。"
        "只生成提案，不写入任何配置。\n"
        "list_name 省略时作用于默认自选（STOCK_LIST）；指定命名列表时必须命中已存在的列表，"
        "否则会报错并列出可用列表名。"
    ),
    parameters=[
        ToolParameter(
            name="action",
            type="string",
            description="add 加入自选 / remove 移出自选",
            required=True,
            enum=list(SUPPORTED_WATCHLIST_ACTIONS),
        ),
        ToolParameter(
            # 参数名必须是 symbol 而不是 stock_code：叫 stock_code 会让 _is_stock_scoped_tool
            # 把它当成受股票范围约束的工具，从而硬阻断跨标的请求（retriable: False）。
            name="symbol",
            type="string",
            description="股票代码，支持 600519 / HK00700 / AAPL 等格式",
            required=True,
        ),
        ToolParameter(
            name="list_name",
            type="string",
            description="可选命名自选列表名；省略作用于默认 STOCK_LIST",
            required=False,
        ),
        ToolParameter(name="reason", type="string", description="可选提案理由，用于在卡片上向用户说明", required=False),
    ],
    handler=_handle_propose_watchlist_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)


ALL_ACTION_TOOLS: List[ToolDefinition] = [
    propose_portfolio_trade_tool,
    propose_watchlist_change_tool,
]

# -*- coding: utf-8 -*-
"""Agent 工具：把需要用户确认的写操作包装成「提案」。

与 ``alert_tools.propose_alert_rule_change`` 同一套约定：工具本身**不持久化**，只做校验
并返回 ``{"kind", "summary", "proposal"}`` 信封，其中 ``proposal`` 就是对应写接口的请求体。
runner 读到该信封后发出 ``action_proposal`` SSE 事件，真正的写入由用户在 Web 端确认后、
用浏览器会话调用既有 REST 接口完成。工具从不持久化任何东西、从不调用写接口，因此 AI
始终不持有写权限。

``cancellation_safe=False`` 让本模块的工具被 ``tool_surface`` 挡在 Codex 只读面外
（与 ``alert_tools`` 的提案工具一致）。
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

logger = logging.getLogger(__name__)

_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="proposal",
    risk_level="high",
    # 与 alert_tools 的提案工具一致：交互式确认流工具，不适用于 Codex 只读面。
    cancellation_safe=False,
)

# 接受 ``stock_code`` 参数的提案工具必须声明 stock scope：否则 strict registry
# validation 直接拒绝，且 runner 的 ``_guard_tool_stock_scope`` 不会运行——某个
# 股票作用域下的会话就能提案删除另一只股票的全部历史。
_STOCK_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    scope_dimensions=["stock"],
    execution_mode="proposal",
    risk_level="high",
    cancellation_safe=False,
)

SUPPORTED_SIDES = ("buy", "sell")
SUPPORTED_MARKETS = ("cn", "hk", "us", "jp", "kr", "tw")

# 以下枚举与 API schema 逐字对齐（`api/v1/schemas/portfolio.py`、
# `api/v1/schemas/decision_signals.py`、`api/v1/schemas/paper.py`）。工具的
# proposal 会原样成为确认时的请求体，接受 schema 之外的值只会让确认步骤 422。
_CASH_DIRECTIONS = ("in", "out")
_CASH_DIRECTION_ALIASES = {"in": "in", "out": "out", "deposit": "in", "withdraw": "out"}
_CORPORATE_ACTION_TYPES = ("cash_dividend", "split_adjustment")
_SIGNAL_SOURCE_TYPES = ("analysis", "agent", "alert", "market_review", "manual")
_SIGNAL_STATUSES = ("active", "expired", "invalidated", "closed", "archived")
_SIGNAL_ACTIONS = ("buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert")
_SIGNAL_FEEDBACK_VALUES = ("useful", "not_useful")
_DECISION_PROFILES = ("conservative", "balanced", "aggressive")


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
    """收敛提案理由：去空白并限制长度，避免模型把长文本塞进卡片与 SSE 事件。

    四个提案工具（含 ``alert_tools._handle_propose_alert_rule_change``）共用这一份：它们的 reason 走
    同一条 summary → SSE → 卡片通道，各写一份长度上限就会漂移。
    """
    return str(reason or "").strip()[:_REASON_LIMIT]


def _resolve_display_currency(
    norm_currency: str, norm_market: Optional[str], account: Dict[str, Any]
) -> str:
    """Return the currency the confirmed trade will actually be stored under.

    与 ``PortfolioService.record_trade``（``src/services/portfolio_service.py:252``）**同源**：
    那里把 ``currency or default_currency_for_market(market or account.market)`` 写进
    ``portfolio_trades.currency``。卡片上的金额是用户唯一要确认的量级，币种必须等于落库币种，
    否则会出现「卡片说约支出 CNY …、账本里是 USD」这种单位错误。``market`` 与 ``base_currency``
    是彼此独立的两个字段，港美股账户（``market="us"``）配人民币本位币是真实存在的组合，
    此时**账户本位币不是落库币种**。

    因此这里直接调用服务里那个模块级函数，而不是在工具侧再抄一份 ``{"hk": HKD, "us": USD, ...}``：
    抄一份就等于在同一仓库里放第二条规则（服务侧也用它，见 ``default_currency_for_market``）。
    延迟 import 是刻意的——本模块的调用方包含轻量入口，模块级 import 会把服务侧的依赖拖进工具面；
    而走到这里时 ``_resolve_account`` 已经在同一调用路径上 import 过 ``PortfolioService``
    （import 失败会在那一步返回 ``{"error": ...}``），所以这一行的 import 不会失败、也不会多付一次代价。
    """
    market = norm_market or str(account.get("market") or "").strip().lower()
    from src.services.portfolio_service import default_currency_for_market

    return norm_currency or default_currency_for_market(market)


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

    # 展示币种按服务侧的落库规则解析（成交价币种优先用模型显式给出的，否则按市场取默认币种），
    # 但它只用于文案展示，不写进 proposal —— 美元账户买 A 股时价仍是 CNY，不能混为一谈。
    display_currency = _resolve_display_currency(norm_currency, norm_market, account)

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
        "account_id 必填：先用 get_portfolio_snapshot 读取 snapshot.accounts[] 里的"
        "account_id / account_name（账户信息在 snapshot 下，没有顶层 accounts 键）；"
        "多个账户而用户未指明时必须先追问用户，不要猜。\n"
        "trade_date 省略时按今天记账；用户要补记历史交易必须显式给出 YYYY-MM-DD。"
    ),
    parameters=[
        ToolParameter(
            name="account_id",
            type="integer",
            description="目标账户 id，取自 get_portfolio_snapshot 返回的 snapshot.accounts[].account_id",
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
# shared helpers for the write-proposal handlers
# ============================================================

def _proposal(kind: str, summary: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": kind, "summary": summary, "proposal": proposal}


def _require_positive_int(value: Any, field: str) -> Tuple[Optional[int], Optional[str]]:
    """Validate an integer ID that must be > 0; returns (value, error)."""
    if isinstance(value, bool):
        # ``True`` 会被 int() 静默转成 1，模型误传布尔值时应当报错而不是命中 #1。
        return None, f"{field} 必须是整数，收到 {value!r}"
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, f"{field} 必须是整数，收到 {value!r}"
    if parsed <= 0:
        return None, f"{field} 必须 > 0，收到 {parsed}"
    return parsed, None


def _normalize_iso_date(value: Any, field: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate an ISO date string that will be sent to a Pydantic ``date`` field."""
    raw = str(value or "").strip()
    if not raw:
        return None, f"{field} 不能为空"
    try:
        return date.fromisoformat(raw).isoformat(), None
    except ValueError:
        return None, f"{field} 必须是 YYYY-MM-DD，收到 {raw!r}"


def _normalize_currency_code(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Match ``min_length=3, max_length=8`` on the portfolio request schemas."""
    raw = str(value or "").strip().upper()
    if not raw:
        return None, None
    if not 3 <= len(raw) <= 8:
        return None, f"currency 必须是 3-8 位货币代码（如 CNY/USD/HKD），收到 {value!r}"
    return raw, None


# ============================================================
# propose_portfolio_ledger_entry
# ============================================================

def _handle_propose_portfolio_ledger_entry(
    action: str,
    account_id: Any = None,
    event_date: str = "",
    direction: str = "",
    amount: Any = None,
    currency: str = "",
    note: str = "",
    entry_id: Any = None,
    symbol: str = "",
    effective_date: str = "",
    action_type: str = "",
    market: str = "",
    cash_dividend_per_share: Any = None,
    split_ratio: Any = None,
    action_id: Any = None,
) -> Dict[str, Any]:
    """Validate a cash-ledger / corporate-action proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("cash", "corporate", "delete_cash", "delete_corporate"):
        return {"error": f"action 必须是 cash/corporate/delete_cash/delete_corporate，收到 {action!r}"}
    if norm_action in ("delete_cash", "delete_corporate"):
        id_value = entry_id if norm_action == "delete_cash" else action_id
        parsed, err = _require_positive_int(id_value, "entry_id" if norm_action == "delete_cash" else "action_id")
        if err:
            return {"error": err}
        if norm_action == "delete_cash":
            return _proposal(
                "portfolio_cash_delete", f"删除资金流水 #{parsed}", {"entry_id": parsed}
            )
        return _proposal(
            "portfolio_corporate_delete", f"删除公司行动 #{parsed}", {"action_id": parsed}
        )

    account, err = _resolve_account(account_id)
    if err:
        return {"error": err}
    resolved_account_id = int(account["id"])

    if norm_action == "cash":
        norm_date, err = _normalize_iso_date(event_date, "event_date")
        if err:
            return {"error": err}
        norm_direction = _CASH_DIRECTION_ALIASES.get(str(direction or "").strip().lower())
        if norm_direction is None:
            return {
                "error": (
                    f"direction 必须是 {'/'.join(_CASH_DIRECTIONS)}"
                    f"（也接受 deposit/withdraw 别名），收到 {direction!r}"
                )
            }
        amount_value, err = _check_number(amount, "amount", allow_zero=False)
        if err:
            return {"error": err}
        norm_currency, err = _normalize_currency_code(currency)
        if err:
            return {"error": err}
        return _proposal(
            "portfolio_cash_create",
            f"录入资金流水 {norm_direction} {_fmt_amount(amount_value)}",
            {
                "account_id": resolved_account_id,
                "event_date": norm_date,
                "direction": norm_direction,
                "amount": amount_value,
                "currency": norm_currency,
                "note": (str(note or "").strip()[:255] or None),
            },
        )

    if norm_action == "corporate":
        norm_symbol = str(symbol or "").strip()
        if not norm_symbol or not str(effective_date or "").strip() or not str(action_type or "").strip():
            return {"error": "symbol/effective_date/action_type 不能为空"}
        if len(norm_symbol) > 16:
            return {"error": f"symbol 过长（最多 16 字符）: {norm_symbol!r}"}
        norm_date, err = _normalize_iso_date(effective_date, "effective_date")
        if err:
            return {"error": err}
        norm_action_type = str(action_type or "").strip().lower()
        if norm_action_type not in _CORPORATE_ACTION_TYPES:
            return {"error": f"action_type 必须是 {'/'.join(_CORPORATE_ACTION_TYPES)}，收到 {action_type!r}"}
        norm_market = str(market or "").strip().lower() or None
        if norm_market and norm_market not in SUPPORTED_MARKETS:
            return {"error": f"market 必须是 {'/'.join(SUPPORTED_MARKETS)} 之一，收到 {market!r}"}
        norm_currency, err = _normalize_currency_code(currency)
        if err:
            return {"error": err}
        cash, err = _check_number(
            cash_dividend_per_share, "cash_dividend_per_share", allow_zero=True
        ) if cash_dividend_per_share not in (None, "") else (None, None)
        if err:
            return {"error": err}
        split, err = _check_number(
            split_ratio, "split_ratio", allow_zero=False
        ) if split_ratio not in (None, "") else (None, None)
        if err:
            return {"error": err}
        # 服务端按 action_type 要求对应字段（portfolio_service.record_corporate_action）：
        # cash_dividend 必须有分红，split_adjustment 必须有拆合股比例。
        if norm_action_type == "cash_dividend":
            if cash is None:
                return {"error": "cash_dividend 需要 cash_dividend_per_share"}
        elif split is None:
            return {"error": "split_adjustment 需要 split_ratio"}
        return _proposal(
            "portfolio_corporate_create",
            f"录入公司行动 {norm_action_type} {norm_symbol}",
            {
                "account_id": resolved_account_id,
                "symbol": norm_symbol,
                "effective_date": norm_date,
                "action_type": norm_action_type,
                "market": norm_market,
                "currency": norm_currency,
                "cash_dividend_per_share": cash,
                "split_ratio": split,
                "note": (str(note or "").strip()[:255] or None),
            },
        )
    # ``norm_action`` was validated up-front, so no trailing fallback is needed.


propose_portfolio_ledger_entry = ToolDefinition(
    name="propose_portfolio_ledger_entry",
    description=(
        "生成资金流水或公司行动的录入/删除提案，交给用户确认。"
        "action=cash/corporate/delete_cash/delete_corporate。"
        "cash 需要 account_id/event_date/direction/amount（direction: in/out，"
        "也接受 deposit/withdraw 别名）；corporate 需要 "
        "account_id/symbol/effective_date/action_type 及对应数量字段。"
        "删除只需 entry_id / action_id。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="cash|corporate|delete_cash|delete_corporate", required=True,
                      enum=["cash", "corporate", "delete_cash", "delete_corporate"]),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="event_date", type="string", description="资金流水日期 YYYY-MM-DD", required=False),
        ToolParameter(name="direction", type="string", description="in/out（也接受 deposit/withdraw）", required=False),
        ToolParameter(name="amount", type="number", description="金额，必须大于 0", required=False),
        ToolParameter(name="currency", type="string", description="币种，如 CNY/USD/HKD", required=False),
        ToolParameter(name="note", type="string", description="备注", required=False),
        ToolParameter(name="entry_id", type="integer", description="delete_cash 的资金流水 ID", required=False),
        ToolParameter(name="symbol", type="string", description="公司行动标的", required=False),
        ToolParameter(name="effective_date", type="string", description="公司行动生效日 YYYY-MM-DD", required=False),
        ToolParameter(name="action_type", type="string", description="cash_dividend|split_adjustment", required=False,
                      enum=list(_CORPORATE_ACTION_TYPES)),
        ToolParameter(name="market", type="string", description="市场", required=False,
                      enum=list(SUPPORTED_MARKETS)),
        ToolParameter(name="cash_dividend_per_share", type="number", description="每股现金分红，>= 0", required=False),
        ToolParameter(name="split_ratio", type="number", description="拆合股比例，> 0", required=False),
        ToolParameter(name="action_id", type="integer", description="delete_corporate 的公司行动 ID", required=False),
    ],
    handler=_handle_propose_portfolio_ledger_entry,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_portfolio_account_change
# ============================================================

def _handle_propose_portfolio_account_change(
    action: str,
    account_id: Any = None,
    name: str = "",
    broker: str = "",
    market: str = "",
    base_currency: str = "",
    owner_id: str = "",
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate a portfolio account create/update/deactivate proposal."""
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        norm_name = str(name or "").strip()
        if not norm_name:
            return {"error": "name 不能为空"}
        if len(norm_name) > 64:
            return {"error": f"name 过长（最多 64 字符）: {norm_name!r}"}
        norm_market = str(market or "").strip().lower() or "cn"
        if norm_market not in SUPPORTED_MARKETS:
            return {"error": f"market 必须是 {'/'.join(SUPPORTED_MARKETS)}"}
        norm_currency, err = _normalize_currency_code(base_currency)
        if err:
            return {"error": err}
        proposal = {
            "name": norm_name,
            "broker": (str(broker or "").strip()[:64] or None),
            "market": norm_market,
            "base_currency": norm_currency or "CNY",
            "owner_id": (str(owner_id or "").strip()[:64] or None),
        }
        return _proposal("portfolio_account_create", f"创建持仓账户「{norm_name}」", proposal)

    parsed, err = _require_positive_int(account_id, "account_id")
    if err:
        return {"error": err}
    if norm_action == "deactivate":
        return _proposal(
            "portfolio_account_deactivate", f"停用持仓账户 #{parsed}", {"account_id": parsed}
        )
    if norm_action == "update":
        payload: Dict[str, Any] = {"account_id": parsed}
        for key, value in (
            ("name", name), ("broker", broker), ("market", market),
            ("base_currency", base_currency), ("owner_id", owner_id),
        ):
            norm_value = str(value or "").strip()
            if norm_value:
                payload[key] = norm_value
        if "market" in payload:
            payload["market"] = payload["market"].lower()
            if payload["market"] not in SUPPORTED_MARKETS:
                return {"error": f"market 必须是 {'/'.join(SUPPORTED_MARKETS)} 之一，收到 {market!r}"}
        if "base_currency" in payload:
            norm_currency, err = _normalize_currency_code(payload["base_currency"])
            if err:
                return {"error": err}
            payload["base_currency"] = norm_currency
        if "name" in payload and len(payload["name"]) > 64:
            return {"error": "name 过长（最多 64 字符）"}
        if is_active is not None:
            if not isinstance(is_active, bool):
                return {"error": f"is_active 必须是布尔值，收到 {is_active!r}"}
            payload["is_active"] = is_active
        if len(payload) == 1:
            return {"error": "update 至少需要一个可修改字段"}
        return _proposal("portfolio_account_update", f"更新持仓账户 #{parsed}", payload)
    return {"error": f"action 必须是 create/update/deactivate，收到 {action!r}"}


propose_portfolio_account_change = ToolDefinition(
    name="propose_portfolio_account_change",
    description=(
        "生成持仓账户创建/更新/停用提案，交给用户确认。"
        "create 需要 name（market 可选，默认 cn）；update 需要 account_id 和至少一个可改字段；"
        "deactivate 需要 account_id。只生成提案，不写任何数据。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="create|update|deactivate", required=True,
                      enum=["create", "update", "deactivate"]),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="name", type="string", description="账户名称", required=False),
        ToolParameter(name="broker", type="string", description="券商", required=False),
        ToolParameter(name="market", type="string", description="市场", required=False,
                      enum=list(SUPPORTED_MARKETS)),
        ToolParameter(name="base_currency", type="string", description="本位币，如 CNY/USD/HKD", required=False),
        ToolParameter(name="owner_id", type="string", description="所有人标识", required=False),
        ToolParameter(name="is_active", type="boolean", description="更新时是否启用", required=False),
    ],
    handler=_handle_propose_portfolio_account_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_signal_change
# ============================================================

def _handle_propose_signal_change(
    action: str,
    signal_id: Any = None,
    status: str = "",
    feedback_value: str = "",
    reason_code: str = "",
    note: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a decision-signal proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        if not isinstance(payload, dict) or not payload:
            return {"error": "create 需要 payload（DecisionSignalCreateRequest 字段）"}
        required = ("stock_code", "market", "source_type", "trigger_source", "action")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            return {"error": f"payload 缺少字段: {', '.join(missing)}"}
        stock_code = str(payload["stock_code"]).strip()
        if len(stock_code) > 32:
            return {"error": "payload.stock_code 过长（最多 32 字符）"}
        market = str(payload["market"]).strip().lower()
        if market not in SUPPORTED_MARKETS:
            return {"error": f"payload.market 必须是 {'/'.join(SUPPORTED_MARKETS)} 之一"}
        source_type = str(payload["source_type"]).strip().lower()
        if source_type not in _SIGNAL_SOURCE_TYPES:
            return {"error": f"payload.source_type 必须是 {'/'.join(_SIGNAL_SOURCE_TYPES)} 之一"}
        signal_action = str(payload["action"]).strip().lower()
        if signal_action not in _SIGNAL_ACTIONS:
            return {"error": f"payload.action 必须是 {'/'.join(_SIGNAL_ACTIONS)} 之一"}
        if len(str(payload["trigger_source"])) > 64:
            return {"error": "payload.trigger_source 过长（最多 64 字符）"}
        proposal = dict(payload)
        proposal.update(
            stock_code=stock_code, market=market, source_type=source_type, action=signal_action
        )
        if proposal.get("status"):
            norm_status = str(proposal["status"]).strip().lower()
            if norm_status not in _SIGNAL_STATUSES:
                return {"error": f"payload.status 必须是 {'/'.join(_SIGNAL_STATUSES)} 之一"}
            proposal["status"] = norm_status
        if proposal.get("decision_profile"):
            profile = str(proposal["decision_profile"]).strip().lower()
            if profile not in _DECISION_PROFILES:
                return {"error": f"payload.decision_profile 必须是 {'/'.join(_DECISION_PROFILES)} 之一"}
            proposal["decision_profile"] = profile
        return _proposal(
            "signal_create",
            f"创建决策信号 {stock_code} {signal_action}",
            proposal,
        )
    if norm_action == "reassess":
        if not isinstance(payload, dict) or not payload:
            return {"error": "reassess 需要 payload（source_report_id/decision_profile/persist）"}
        report_id, err = _require_positive_int(payload.get("source_report_id"), "source_report_id")
        if err:
            return {"error": err}
        profile = str(payload.get("decision_profile") or "").strip().lower()
        if profile not in _DECISION_PROFILES:
            return {"error": f"decision_profile 必须是 {'/'.join(_DECISION_PROFILES)} 之一"}
        persist = payload.get("persist", False)
        if not isinstance(persist, bool):
            return {"error": f"persist 必须是布尔值，收到 {persist!r}"}
        # `DecisionSignalReassessRequest` forbids extra keys; build the proposal from
        # the schema fields only instead of forwarding arbitrary model-supplied ones.
        proposal = {
            "source_report_id": report_id,
            "decision_profile": profile,
            "persist": persist,
        }
        return _proposal("signal_reassess", "重算信号建议", proposal)

    parsed, err = _require_positive_int(signal_id, "signal_id")
    if err:
        return {"error": err}
    if norm_action == "status":
        norm_status = str(status or "").strip().lower()
        if norm_status not in _SIGNAL_STATUSES:
            return {"error": f"status 必须是 {'/'.join(_SIGNAL_STATUSES)}"}
        return _proposal(
            "signal_status",
            f"把信号 #{parsed} 状态改为 {norm_status}",
            {"signal_id": parsed, "status": norm_status},
        )
    if norm_action == "feedback":
        norm_value = str(feedback_value or "").strip().lower()
        if norm_value not in _SIGNAL_FEEDBACK_VALUES:
            return {"error": f"feedback_value 必须是 {'/'.join(_SIGNAL_FEEDBACK_VALUES)}"}
        proposal = {"signal_id": parsed, "feedback_value": norm_value}
        if reason_code:
            proposal["reason_code"] = str(reason_code)[:64]
        if note:
            proposal["note"] = str(note)[:1000]
        return _proposal("signal_feedback", f"提交信号 #{parsed} 反馈：{norm_value}", proposal)
    return {"error": f"action 必须是 create/status/feedback/reassess，收到 {action!r}"}


propose_signal_change = ToolDefinition(
    name="propose_signal_change",
    description=(
        "生成决策信号创建/状态更新/反馈/重算提案，交给用户确认。"
        "create 与 reassess 使用 payload 传完整请求体；status 用 signal_id+status；"
        "feedback 用 signal_id+feedback_value(+reason_code/note)。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="create|status|feedback|reassess", required=True,
                      enum=["create", "status", "feedback", "reassess"]),
        ToolParameter(name="signal_id", type="integer", description="信号 ID", required=False),
        ToolParameter(name="status", type="string", description="新状态", required=False,
                      enum=list(_SIGNAL_STATUSES)),
        ToolParameter(name="feedback_value", type="string", description="反馈值", required=False,
                      enum=list(_SIGNAL_FEEDBACK_VALUES)),
        ToolParameter(name="reason_code", type="string", description="反馈原因码", required=False),
        ToolParameter(name="note", type="string", description="反馈备注", required=False),
        ToolParameter(name="payload", type="object", description="create/reassess 的完整请求体", required=False),
    ],
    handler=_handle_propose_signal_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_paper_reset
# ============================================================

def _handle_propose_paper_reset(initial_capital: Any = None) -> Dict[str, Any]:
    """Validate a paper-account reset proposal; persists nothing."""
    proposal: Dict[str, Any] = {}
    if initial_capital not in (None, ""):
        value, err = _check_number(initial_capital, "initial_capital", allow_zero=False)
        if err:
            return {"error": err}
        proposal["initial_capital"] = value
    return _proposal(
        "paper_reset",
        "重置模拟盘账户（归档当前账户，清空持仓与信号）",
        proposal,
    )


propose_paper_reset = ToolDefinition(
    name="propose_paper_reset",
    description=(
        "生成模拟盘重置提案（破坏性操作，必须用户确认）。"
        "省略 initial_capital 时使用 PAPER_INITIAL_CAPITAL 配置值。"
    ),
    parameters=[
        ToolParameter(name="initial_capital", type="number", description="重置后的初始资金，必须大于 0", required=False),
    ],
    handler=_handle_propose_paper_reset,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# shared service accessors for the site-operation proposals
# ============================================================

# Provider hook: tests (and callers) may replace this with a callable returning
# a service double.  ``None`` means "lazily import the real service class".
_system_config_service = None


def _system_config_service_instance():
    global _system_config_service
    if _system_config_service is None:
        from src.services.system_config_service import SystemConfigService

        _system_config_service = SystemConfigService
    return _system_config_service()


# ============================================================
# propose_history_deletion
# ============================================================

def _handle_propose_history_deletion(
    action: str,
    record_ids: Optional[List[int]] = None,
    stock_code: str = "",
) -> Dict[str, Any]:
    """Build a history-deletion proposal; deletes nothing.

    Targets mirror the two REST endpoints: ``DELETE /api/v1/history`` takes
    ``{"record_ids": [...]}`` and ``DELETE /api/v1/history/by-code/{code}``.
    ``stock_code`` is the declared parameter (not an undeclared ``code`` alias)
    so the runner's stock-scope guard can see and check it.
    """
    norm_action = str(action or "").strip().lower()
    if norm_action == "records":
        if not isinstance(record_ids, (list, tuple)):
            return {"error": "record_ids 必须是整数列表"}
        ids: List[int] = []
        for raw_id in record_ids:
            parsed, err = _require_positive_int(raw_id, "record_ids")
            if err:
                return {"error": err}
            ids.append(parsed)
        if not ids:
            return {"error": "record_ids 不能为空"}
        return _proposal(
            "history_delete_records",
            f"删除 {len(ids)} 条分析历史记录（ID: {', '.join(str(i) for i in ids)}）",
            {"record_ids": ids},
        )
    if norm_action == "by_code":
        norm_code = str(stock_code or "").strip()
        if not norm_code:
            return {"error": "stock_code 不能为空"}
        return _proposal(
            "history_delete_by_code",
            f"删除「{norm_code}」的全部分析历史记录",
            {"stock_code": norm_code},
        )
    return {"error": f"action 必须是 records/by_code，收到 {action!r}"}


propose_history_deletion = ToolDefinition(
    name="propose_history_deletion",
    description=(
        "生成删除分析历史的提案：按记录 ID（records）或按股票代码（by_code）。"
        "删除属破坏性操作，必须由用户确认，工具本身不删除任何数据。"
        "按代码删除会清空该股票的全部分析历史，summary 会明确写出目标与范围。"
        "在单只股票作用域的会话中，stock_code 只能与该股票一致。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="records|by_code", required=True,
                      enum=["records", "by_code"]),
        ToolParameter(name="record_ids", type="array", description="action=records 时要删除的记录 ID 列表", required=False),
        ToolParameter(name="stock_code", type="string", description="action=by_code 时要删除全部历史的股票代码", required=False),
    ],
    handler=_handle_propose_history_deletion,
    category="action",
    policy=_STOCK_PROPOSAL_POLICY,
)


# ============================================================
# propose_intelligence_source_change
# ============================================================

# Enums copied verbatim from ``api/v1/schemas/intelligence.py``.
_INTELLIGENCE_SOURCE_TYPES = ("rss", "atom", "newsnow")
_INTELLIGENCE_SCOPE_TYPES = ("symbol", "market", "sector")
_INTELLIGENCE_MARKETS = ("cn", "hk", "us", "jp", "kr", "tw", "global")


def _intelligence_source_url_error(url: str) -> Optional[str]:
    """Return an error when the API's own source-URL validator would reject ``url``.

    Reusing ``IntelligenceService._validate_url`` keeps the proposal-side check
    from drifting from ``create_source`` (scheme, credentials, private/local
    hosts, DNS-resolved private addresses).
    """
    from src.services.intelligence_service import (
        IntelligenceService,
        IntelligenceServiceError,
    )

    try:
        # ``_validate_url`` only relies on the ``_is_blocked_ip`` staticmethod, so
        # bypass ``__init__`` and avoid constructing a repository/config object.
        IntelligenceService._validate_url(object.__new__(IntelligenceService), url)
    except IntelligenceServiceError as exc:
        return str(exc)
    return None


def _handle_propose_intelligence_source_change(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate an intelligence-source create proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        if not isinstance(payload, dict) or not payload:
            return {"error": "create 需要 payload（IntelligenceSourceCreateRequest 字段）"}

        name = str(payload.get("name") or "").strip()
        if not name:
            return {"error": "payload.name 不能为空"}
        if len(name) > 100:
            return {"error": "payload.name 过长（最多 100 字符）"}

        url = str(payload.get("url") or "").strip()
        if not url:
            return {"error": "payload.url 不能为空"}
        if len(url) > 1000:
            return {"error": "payload.url 过长（最多 1000 字符）"}

        source_type = str(payload.get("source_type") or "rss").strip().lower()
        if source_type not in _INTELLIGENCE_SOURCE_TYPES:
            return {"error": f"payload.source_type 必须是 {'/'.join(_INTELLIGENCE_SOURCE_TYPES)} 之一"}

        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            return {"error": f"payload.enabled 必须是布尔值，收到 {enabled!r}"}

        scope_type = str(payload.get("scope_type") or "market").strip().lower()
        if scope_type not in _INTELLIGENCE_SCOPE_TYPES:
            return {"error": f"payload.scope_type 必须是 {'/'.join(_INTELLIGENCE_SCOPE_TYPES)} 之一"}

        scope_value = payload.get("scope_value")
        if scope_value is not None:
            scope_value = str(scope_value).strip()
            if len(scope_value) > 64:
                return {"error": "payload.scope_value 过长（最多 64 字符）"}
            scope_value = scope_value or None
        # Mirrors IntelligenceService._normalize_source_fields.
        if scope_type in ("symbol", "sector") and not scope_value:
            return {"error": f"scope_type={scope_type} 时 payload.scope_value 必填"}

        market = str(payload.get("market") or "cn").strip().lower()
        if market not in _INTELLIGENCE_MARKETS:
            return {"error": f"payload.market 必须是 {'/'.join(_INTELLIGENCE_MARKETS)} 之一"}

        # Run the exact validator ``IntelligenceService.create_source`` uses, so a
        # proposal cannot be created for a URL the confirm request will 400 on.
        url_error = _intelligence_source_url_error(url)
        if url_error:
            return {"error": f"payload.url 不合法：{url_error}"}

        # Build from schema fields only: forwarding arbitrary model keys would let a
        # proposal look valid while the real create request silently drops them.
        proposal: Dict[str, Any] = {
            "name": name,
            "url": url,
            "source_type": source_type,
            "enabled": enabled,
            "scope_type": scope_type,
            "scope_value": scope_value,
            "market": market,
        }
        if payload.get("description") is not None:
            proposal["description"] = str(payload["description"])

        return _proposal(
            "intelligence_source_create",
            f"创建情报源「{name}」（{source_type}，{market}）",
            proposal,
        )
    if norm_action == "defaults":
        return _proposal(
            "intelligence_defaults_create",
            "创建内置默认情报源",
            {},
        )
    return {"error": f"action 必须是 create/defaults，收到 {action!r}"}


propose_intelligence_source_change = ToolDefinition(
    name="propose_intelligence_source_change",
    description=(
        "生成创建情报源（create）或创建内置默认情报源（defaults）的提案。"
        "create 的 payload 需包含 name 与 url；source_type/scope_type/market 使用接口枚举。"
        "url 必须通过情报源接口的校验（绝对 http(s)、无凭据、非内网/本机地址）。"
        "只生成提案，不写入任何数据。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="create|defaults", required=True,
                      enum=["create", "defaults"]),
        ToolParameter(name="payload", type="object", description="create 的完整请求体", required=False),
    ],
    handler=_handle_propose_intelligence_source_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_config_update
# ============================================================

def _config_schema_fields() -> Dict[str, Dict[str, Any]]:
    """Return the settings schema fields keyed by upper-case config key.

    ``get_schema()`` is the same source the settings page and the config
    endpoint use; a local allow-list or ``KEY``/``TOKEN`` substring check would
    drift from it.
    """
    schema = _system_config_service_instance().get_schema()
    return {
        str(field.get("key") or "").upper(): field
        for category in schema.get("categories", [])
        for field in category.get("fields", [])
        if field.get("key")
    }


def _handle_propose_config_update(
    config_version: str,
    items: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Validate a system-config update proposal; never includes secret values."""
    norm_version = str(config_version or "").strip()
    if not norm_version:
        return {"error": "config_version 不能为空"}
    if not isinstance(items, (list, tuple)) or not items:
        return {"error": "items 不能为空"}

    normalized: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            return {"error": "items 中的每一项必须是 {key, value} 对象"}
        key = str(item.get("key") or "").strip()
        if not key:
            return {"error": "items 中存在空 key"}
        if "value" not in item or item.get("value") is None:
            return {"error": f"items 中 {key} 缺少 value"}
        normalized.append({"key": key, "value": str(item["value"])})

    try:
        schema_fields = _config_schema_fields()
    except Exception as exc:
        logger.warning("propose_config_update schema lookup failed: %s", exc)
        return {"error": f"读取配置 schema 失败，无法校验配置项: {exc}"}

    # Only keys the settings page exposes are acceptable; the endpoint resolves
    # values against this same schema and rejects anything else.
    unknown = [item["key"] for item in normalized if item["key"].upper() not in schema_fields]
    if unknown:
        return {
            "error": (
                "以下配置项不在系统配置 schema 中，不能通过问股助手修改："
                + "、".join(unknown)
                + "。请使用 get_config_view 返回的配置键。"
            )
        }

    # Error text deliberately names only the keys: the submitted value may be the
    # secret itself and must never reach the SSE event or the confirmation card.
    blocked = [
        item["key"]
        for item in normalized
        if schema_fields[item["key"].upper()].get("is_sensitive")
    ]
    if blocked:
        return {
            "error": (
                "以下配置属于密钥/敏感字段，问股助手不能修改："
                + "、".join(blocked)
                + "。请到设置页手动修改。"
            )
        }

    keys = [item["key"] for item in normalized]
    return _proposal(
        "config_update",
        f"修改 {len(normalized)} 项系统配置：{'、'.join(keys)}",
        {"config_version": norm_version, "items": normalized},
    )


propose_config_update = ToolDefinition(
    name="propose_config_update",
    description=(
        "生成系统配置修改提案。只接受设置页 schema 中的配置键；"
        "密钥、Token、密码等敏感字段一律拒绝；"
        "config_version 必须来自 get_config_view 的返回。"
        "只生成提案，不写入任何配置。"
    ),
    parameters=[
        ToolParameter(name="config_version", type="string", description="当前配置版本号（来自 get_config_view）", required=True),
        ToolParameter(name="items", type="array", description="[{key, value}] 列表", required=True),
    ],
    handler=_handle_propose_config_update,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_scheduler_run
# ============================================================

def _handle_propose_scheduler_run() -> Dict[str, Any]:
    # POST /api/v1/system/scheduler/run-now takes no body and run_now() takes no
    # arguments; any extra field here would be silently unhonorable.
    return _proposal(
        "scheduler_run",
        "立即执行一次调度分析",
        {},
    )


propose_scheduler_run = ToolDefinition(
    name="propose_scheduler_run",
    description=(
        "生成立即执行一次调度分析的提案（对应 POST /api/v1/system/scheduler/run-now）。"
        "该接口没有请求参数；只生成提案，不触发任何任务。"
    ),
    parameters=[],
    handler=_handle_propose_scheduler_run,
    category="action",
    policy=_PROPOSAL_POLICY,
)


# ============================================================
# propose_notification_test
# ============================================================

# Mirrors ``NotificationTestChannel`` in ``api/v1/schemas/system_config.py``.
_NOTIFICATION_TEST_CHANNELS = (
    "wechat", "feishu", "dingtalk", "telegram", "email", "pushover",
    "ntfy", "gotify", "pushplus", "serverchan3", "custom", "discord",
    "slack", "astrbot",
)


def _handle_propose_notification_test(
    channel: str,
    title: str = "",
    content: str = "",
) -> Dict[str, Any]:
    """Validate a notification-channel test proposal; sends nothing."""
    norm_channel = str(channel or "").strip().lower()
    if not norm_channel:
        return {"error": "channel 不能为空"}
    if norm_channel not in _NOTIFICATION_TEST_CHANNELS:
        return {
            "error": (
                f"channel 必须是 {'/'.join(_NOTIFICATION_TEST_CHANNELS)} 之一，"
                f"收到 {channel!r}"
            )
        }
    proposal: Dict[str, Any] = {"channel": norm_channel}
    # ``TestNotificationChannelRequest`` enforces 1..80 / 1..1000; omitted fields
    # fall back to the endpoint defaults.
    norm_title = str(title or "")[:80]
    if norm_title:
        proposal["title"] = norm_title
    norm_content = str(content or "")[:1000]
    if norm_content:
        proposal["content"] = norm_content
    return _proposal(
        "notification_test",
        f"向通知渠道「{norm_channel}」发送测试消息",
        proposal,
    )


propose_notification_test = ToolDefinition(
    name="propose_notification_test",
    description=(
        "生成向某个通知渠道发送测试消息的提案。channel 使用设置页支持的渠道名。"
        "只生成提案，不实际发送。"
    ),
    parameters=[
        ToolParameter(name="channel", type="string", description="通知渠道名", required=True,
                      enum=list(_NOTIFICATION_TEST_CHANNELS)),
        ToolParameter(name="title", type="string", description="测试标题（最长 80 字符）", required=False),
        ToolParameter(name="content", type="string", description="测试内容（最长 1000 字符）", required=False),
    ],
    handler=_handle_propose_notification_test,
    category="action",
    policy=_PROPOSAL_POLICY,
)


ALL_ACTION_TOOLS: List[ToolDefinition] = [
    propose_portfolio_trade_tool,
    propose_portfolio_ledger_entry,
    propose_portfolio_account_change,
    propose_signal_change,
    propose_paper_reset,
    propose_history_deletion,
    propose_intelligence_source_change,
    propose_config_update,
    propose_scheduler_run,
    propose_notification_test,
]

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
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

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


def _check_number(
    value: Any, field: str, *, allow_zero: bool
) -> Tuple[Optional[float], Optional[str]]:
    """Validate a numeric field; returns (value, error)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{field} 必须是数字，收到 {value!r}"
    if allow_zero and number < 0:
        return None, f"{field} 必须 >= 0，收到 {number}"
    if not allow_zero and number <= 0:
        return None, f"{field} 必须 > 0，收到 {number}"
    return number, None


# ============================================================
# propose_portfolio_trade
# ============================================================

def _format_account_choices(accounts: List[Dict[str, Any]]) -> str:
    """Render the account list as a one-line hint so the model can self-correct."""
    active = [a for a in accounts if a.get("is_active")]
    if not active:
        return "当前没有已激活的账户，请先在持仓页创建账户"
    return "；".join(f"{int(a['id'])}: {a.get('name') or '未命名'}" for a in active)


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
        return {"error": f"market 必须是 {list(SUPPORTED_MARKETS)} 之一，收到 {market!r}"}

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
    norm_note = str(note or "").strip()
    if norm_note:
        proposal["note"] = norm_note[:255]

    action_label = "买入" if norm_side == "buy" else "卖出"
    amount = norm_quantity * norm_price + norm_fee + norm_tax
    summary = (
        f"在「{account.get('name') or '未命名账户'}」记一笔{action_label} "
        f"{norm_symbol} {_fmt_num(norm_quantity)} @ ¥{_fmt_num(norm_price)}"
        f"（{norm_date.isoformat()}，约 ¥{_fmt_amount(amount)}）"
    )
    norm_reason = str(reason or "").strip()
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
        ToolParameter(name="note", type="string", description="可选备注，写入交易记录", required=False),
        ToolParameter(name="reason", type="string", description="可选提案理由，用于在卡片上向用户说明", required=False),
    ],
    handler=_handle_propose_portfolio_trade,
    category="action",
    policy=_PROPOSAL_POLICY,
)


ALL_ACTION_TOOLS: List[ToolDefinition] = [propose_portfolio_trade_tool]

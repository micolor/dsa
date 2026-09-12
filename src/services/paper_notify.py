# -*- coding: utf-8 -*-
"""模拟盘成交通知：把实时产生的模拟成交推给已配置的通知渠道。

只在**实时**路径调用（信号消费、盘后估值触发的止损/止盈）。历史回填是重放，
一次可能产生几百笔成交，逐笔推送会把渠道刷屏，因此 `backfill_history` 全程
`notify=False`，不经过这里。

复用 event 路由（`NOTIFICATION_EVENT_CHANNELS`）——它本来就是给「龙虎榜 / 主力
资金 / 重要公告」这类事件型通知用的，模拟成交同属事件，不再单开一条路由。
发送失败只记日志，绝不抛回交易路径：一条通知发不出去不能影响模拟盘记账。
"""
import logging
from typing import Any, Optional

from src.config import get_config
from src.notification import NotificationBuilder, NotificationService

logger = logging.getLogger(__name__)

# 平仓原因 -> 展示文案 / emoji 语气。
_EXIT_LABELS = {
    "stop_loss": ("止损平仓", "warning"),
    "ambiguous_stop_loss": ("止损平仓（同日先触及止损）", "warning"),
    "take_profit": ("止盈平仓", "success"),
}


def _action_label(trade: Any, disposition: Optional[str]) -> tuple:
    """Return (label, alert_type) for a fill, from its side/reason/disposition."""
    reason = str(getattr(trade, "reason", "") or "")
    if reason in _EXIT_LABELS:
        return _EXIT_LABELS[reason]

    if str(getattr(trade, "side", "") or "") == "buy":
        # 加仓与首次开仓都用 side=buy 落库，靠 disposition 区分。
        return ("加仓", "info") if disposition == "added" else ("买入开仓", "info")
    return ("清仓卖出", "info") if disposition == "closed" else ("减仓卖出", "info")


def build_paper_fill_message(trade: Any, *, cash_after: float, disposition: Optional[str] = None) -> str:
    """Render one fill as a markdown notification body."""
    label, alert_type = _action_label(trade, disposition)
    code = str(getattr(trade, "stock_code", "") or "")
    name = str(getattr(trade, "stock_name", "") or "").strip()
    title = f"模拟盘成交 | {code} {name}".rstrip()

    quantity = float(getattr(trade, "quantity", 0) or 0)
    price = float(getattr(trade, "price", 0) or 0)
    amount = float(getattr(trade, "amount", 0) or 0)
    fee = float(getattr(trade, "fee", 0) or 0)
    trade_date = getattr(trade, "trade_date", None)
    content = "\n".join(
        [
            f"**动作**：{label}",
            f"**成交**：{quantity:g} @ {price:.4f}",
            f"**金额**：{amount:,.2f}",
            f"**手续费**：{fee:,.2f}",
            f"**成交后现金**：{float(cash_after):,.2f}",
            f"**日期**：{trade_date}",
        ]
    )
    return NotificationBuilder.build_simple_alert(title=title, content=content, alert_type=alert_type)


def send_paper_fill_notification(
    trade: Any,
    *,
    cash_after: float,
    disposition: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> bool:
    """Push one live paper fill. Returns True on successful dispatch.

    Never raises: a notification outage must not break paper-trading bookkeeping.
    """
    if enabled is None:
        try:
            enabled = bool(getattr(get_config(), "paper_notify_enabled", False))
        except Exception:  # pragma: no cover - defensive
            enabled = False
    if not enabled:
        return False

    # 不在这里做进程内去重：模拟成交天然不会重复（process_signal 按信号落已消费
    # 记录，_valuate 只对仍 open 的持仓触发离场），而按 trade id 攒去重集合会在
    # 长期运行的进程里单调增长。dedup_key 仍传给下游用于投递回执归类。
    dedup_key = f"paper-fill:{getattr(trade, 'account_id', None)}:{getattr(trade, 'id', None)}"
    try:
        content = build_paper_fill_message(trade, cash_after=cash_after, disposition=disposition)
        result = NotificationService().send_with_results(
            content, route_type="event", dedup_key=dedup_key
        )
        if bool(result.success):
            return True
        logger.warning("模拟盘成交通知未发送成功: %s result=%s", dedup_key, getattr(result, "status", None))
        return False
    except Exception as exc:  # noqa: BLE001 - must never break the trading path
        logger.warning("模拟盘成交通知发送失败: %s err=%s", dedup_key, exc)
        return False

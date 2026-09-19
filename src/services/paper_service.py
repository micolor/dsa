# -*- coding: utf-8 -*-
"""Paper-trading service: drives a virtual account from AI decision signals.

Tracks how well the AI's buy/add/sell/reduce recommendations would have
performed, producing positions, a daily equity curve and trade records.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.trading_calendar import (
    get_effective_trading_date,
    get_market_for_stock,
    resolve_fill_session,
)
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.paper_repo import PaperRepository
from src.services.paper_notify import send_paper_fill_notification
from src.storage import DatabaseManager, DecisionSignalRecord

logger = logging.getLogger(__name__)

# Default target weight of total assets allocated per opened position. Overridable
# per service instance (e.g. from the `PAPER_POSITION_WEIGHT` config).
POSITION_WEIGHT = 0.20
# Starting cash for a newly created paper account, unless `PAPER_INITIAL_CAPITAL`
# or an explicit argument overrides it. Only consulted at creation time: an
# existing account keeps the capital it was opened with.
INITIAL_CAPITAL = 1000000.0
# Lookback used when loading price bars for live valuation.
DEFAULT_LOOKBACK_DAYS = 365
# Commission rate applied to every market unless `PAPER_FEE_COMMISSION_RATE`
# overrides it. The broker-negotiable part of the cost, hence configurable.
COMMISSION_RATE = 0.00025
# Adverse slippage per fill, in basis points. Defaults to 0: slippage is an
# assumption about execution quality, not an observable fee, so the simulation
# does not invent it unless the user asks for it.
SLIPPAGE_BPS = 0.0

# Statutory / exchange fees per market. These are set by the venue rather than
# negotiated, so they live here instead of in config. `stamp_duty_sell_only`
# captures the A股 rule where 印花税 is charged on the sell side alone, while
# 港股 charges it both ways. Unlisted markets fall back to the A股 profile, which
# is the account's home market — assuming zero statutory cost would understate.
_MARKET_FEE_PROFILE: Dict[str, Dict[str, Any]] = {
    "cn": {
        "min_commission": 5.0,
        "stamp_duty_rate": 0.0005,
        "stamp_duty_sell_only": True,
        "transfer_fee_rate": 0.00001,
    },
    "hk": {
        "min_commission": 0.0,
        "stamp_duty_rate": 0.001,
        "stamp_duty_sell_only": False,
        # 交易费 + 交易征费 + 结算费 + 财务汇报局交易征费 合计约 0.0105%。
        "transfer_fee_rate": 0.000105,
    },
    "us": {
        "min_commission": 0.0,
        "stamp_duty_rate": 0.0,
        "stamp_duty_sell_only": False,
        "transfer_fee_rate": 0.0,
    },
}

# Actions that open or add to a position.
_OPEN_ACTIONS = ("buy", "add")
# Actions that close or reduce a position.
_CLOSE_ACTIONS = ("sell", "reduce")
# Dispositions that actually change the portfolio; only these need a follow-up
# valuation (hold/ignored leave positions untouched, so a daily mark-to-market
# adds nothing and only pollutes the equity curve with redundant snapshots).
_PORTFOLIO_CHANGING_DISPOSITIONS = ("opened", "added", "reduced", "closed")

# Dispositions recorded when a data-source failure prevented pricing; these are
# *not* persisted as consumed so the signal can be retried on a later run rather
# than being permanently dropped (single data-source failures must not silently
# discard a trade that should have filled).
_DATA_UNAVAILABLE = "data_unavailable"

# Disposition recorded when the session's range never reached the plan's limit
# price. The order is consumed (it is not a data gap), but no position is opened.
_NO_FILL = "no_fill"

# Fill resolution for a buy/add limit order, see `_entry_fill_price`.
_FILLED = "filled"
_FILL_UNAVAILABLE = "unavailable"

# 明确「想做但没做成」的三种成因，与 `ignored`（信号本身就是 hold/watch/avoid/alert，
# 无事可做）区分开。此前这几种一律记成 `ignored`（无持仓时买不进）或 `hold`（有持仓
# 时加不动），于是「账户没现金了」在页面上显示成「维持」——正好把话说反：用户看到
# 「维持」会以为系统认为无需动作，实际是买入被现金挡住了。
#
# 只改记录值、不改行为：这三种都照旧被消费（不重试、不触发重新估值，见
# `_PORTFOLIO_CHANGING_DISPOSITIONS`），也与 `_DATA_UNAVAILABLE` 的「可重试」正交。
# `disposition` 是 String(16) 的自由字符串，历史行保持旧值、不迁移；Web 对未知值
# 原样回退（`PaperRecordsList` 的 `disposition?.label ?? record.disposition`），
# 因此新旧值可以并存。
_NO_CASH = "no_cash"  # 目标权重内仍有空间，但账户可用现金已耗尽
_LOT_TOO_SMALL = "lot_too_small"  # 有现金/有持仓，但按最小交易单位取整后为 0
_NO_POSITION = "no_position"  # 收到 sell/reduce 信号，但没有可减的持仓

# Per-account serialization locks. Paper writes (signal consumption, daily
# valuation, backfill, manual refresh) mutate cash/positions/trades across
# several independent commits. Without serialization, concurrent threads would
# lose cash/position updates (read-modify-write) or double-execute the same
# signal (the idempotency check + execution + record are separate commits). The
# lock registry is module-level, not per-instance, because callers create fresh
# PaperService() instances (e.g. the background valuation task) that must still
# share one lock per account.
_account_locks: Dict[int, threading.RLock] = {}
_account_locks_guard = threading.Lock()


def _account_lock(account_id: int) -> threading.RLock:
    with _account_locks_guard:
        lock = _account_locks.get(account_id)
        if lock is None:
            lock = threading.RLock()
            _account_locks[account_id] = lock
        return lock


# Per-stock bar cache shared across ALL PaperService instances, so the daily
# valuation task, signal-consumption pipeline, backfill and manual refresh reuse
# one window instead of re-pulling DEFAULT_LOOKBACK_DAYS bars per run. This is
# module-level (not per-instance) for the same reason as _account_locks: callers
# create fresh PaperService() instances. Freshness is guarded by a window check
# (start <= as_of <= cached_end): a request past the loaded window (e.g. a new
# trading day) reloads, so bars never go stale across days. The cached upper
# bound is the *latest bar actually loaded*, not the ``date.today()`` used to
# query: the two differ whenever the newest session's bar has not landed yet,
# and a calendar-only bound would then mark that session as already covered and
# serve ``None`` for it for the rest of the process' life. An LRU cap bounds
# memory in a long-lived process.
_BAR_CACHE: Dict[str, Tuple[date, date, Dict[date, Dict[str, float]]]] = {}
_BAR_CACHE_LOCK = threading.Lock()
_BAR_CACHE_MAX_ENTRIES = 1024


def clear_bar_cache_for_tests() -> None:
    """Clear the shared module-level bar cache (test isolation)."""
    with _BAR_CACHE_LOCK:
        _BAR_CACHE.clear()


class PaperService:
    """Core paper-trading engine (signal consumption + daily valuation + backfill)."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        paper_repo: Optional[PaperRepository] = None,
        decision_repo: Optional[DecisionSignalRepository] = None,
        position_weight: Optional[float] = None,
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self.paper_repo = paper_repo or PaperRepository(self.db)
        self.decision_repo = decision_repo or DecisionSignalRepository(self.db)
        # Position weight as a fraction of total assets (e.g. 0.20 == 20%).
        self.position_weight = position_weight if position_weight is not None else self._default_position_weight()
        # Cost model: charging nothing books every fill at an unreachable price.
        fee_enabled, commission_rate, slippage_bps = self._fee_settings()
        self.fee_enabled = fee_enabled
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps

    @staticmethod
    def _default_position_weight() -> float:
        """Resolve the default position weight from config, falling back to the constant."""
        try:
            from src.config import Config

            cfg = Config.get_instance()
            weight = float(getattr(cfg, "paper_position_weight", POSITION_WEIGHT) or POSITION_WEIGHT)
            return weight if 0 < weight <= 1.0 else POSITION_WEIGHT
        except Exception:  # pragma: no cover - defensive fallback
            return POSITION_WEIGHT

    @staticmethod
    def _default_initial_capital() -> float:
        """Resolve the starting cash for a new account from config.

        Only new accounts consult this; an account that already exists keeps the
        capital it was opened with, so editing the config never rewrites live
        account data behind the user's back.
        """
        try:
            from src.config import Config

            cfg = Config.get_instance()
            capital = float(getattr(cfg, "paper_initial_capital", INITIAL_CAPITAL) or INITIAL_CAPITAL)
            return capital if capital > 0 else INITIAL_CAPITAL
        except Exception:  # pragma: no cover - defensive fallback
            return INITIAL_CAPITAL

    @staticmethod
    def _fee_settings() -> Tuple[bool, float, float]:
        """Resolve (fee_enabled, commission_rate, slippage_bps) from config."""
        try:
            from src.config import Config

            cfg = Config.get_instance()
            return (
                bool(getattr(cfg, "paper_fee_enabled", True)),
                float(getattr(cfg, "paper_commission_rate", COMMISSION_RATE) or 0.0),
                float(getattr(cfg, "paper_slippage_bps", SLIPPAGE_BPS) or 0.0),
            )
        except Exception:  # pragma: no cover - defensive fallback
            return True, COMMISSION_RATE, SLIPPAGE_BPS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_or_create_account(self, initial_capital: Optional[float] = None) -> Dict[str, Any]:
        """Return the active account, creating it on first use.

        ``initial_capital`` only matters at creation time — an account that
        already exists is returned unchanged. Defaults to the configured
        ``PAPER_INITIAL_CAPITAL``.
        """
        if initial_capital is None:
            initial_capital = self._default_initial_capital()
        account = self.paper_repo.ensure_account(initial_capital=initial_capital)
        return self._account_payload(account)

    def reset_account(self, initial_capital: Optional[float] = None) -> Dict[str, Any]:
        """Archive every active account and open a fresh one, returning the new one.

        The old accounts are only marked `archived`: their positions, trades and
        equity snapshots stay in the database keyed by their own account id, so a
        reset never destroys history — it just moves it out of the way.
        ``ensure_account`` picks the lowest-id *active* account, so the successor
        becomes the one every other entry point reads.

        Nothing replays automatically: the new account is empty until signals are
        consumed live or ``backfill_history`` re-runs them over a chosen range.
        """
        if initial_capital is None:
            initial_capital = self._default_initial_capital()
        capital = float(initial_capital)
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("初始资金必须是大于 0 的有限数值")

        current = self.paper_repo.ensure_account()
        # Serialize on the account being replaced so two concurrent resets cannot
        # both archive the same row and each open a successor.
        with _account_lock(current.id):
            for stale in self.paper_repo.list_accounts():
                if stale.status == "active":
                    self.paper_repo.update_account(stale.id, {"status": "archived"})
            account = self.paper_repo.ensure_account(initial_capital=capital)

        logger.info(
            "paper account reset: archived active account(s) up to %s, opened %s with initial capital %s",
            current.id,
            account.id,
            capital,
        )
        return self._account_payload(account)

    def process_signal(self, signal_id: int) -> Dict[str, Any]:
        """Consume a single decision signal into the paper account (idempotent)."""
        signal = self.decision_repo.get(signal_id)
        if signal is None:
            return {"status": "not_found"}
        account = self.paper_repo.ensure_account()

        # Serialize the whole consume (dedup-check -> execute -> record) per
        # account so concurrent consumers of the same signal cannot both buy.
        with _account_lock(account.id):
            if self.paper_repo.has_signal_record(account.id, signal.id):
                return {"status": "skipped", "signal_id": signal.id}

            as_of = self._signal_trade_date(signal)
            disposition = self._handle_signal(account, signal, as_of)

            # Data-source failure (no price): do not mark the signal consumed, so
            # a later run can retry instead of permanently dropping a fill.
            if disposition == _DATA_UNAVAILABLE:
                return {
                    "status": _DATA_UNAVAILABLE,
                    "signal_id": signal.id,
                    "action": signal.action,
                    "disposition": disposition,
                }

            self.paper_repo.add_signal_record(account.id, signal.id, signal.action, disposition)

            # Only re-value when the position actually changed; hold/ignored leave
            # the portfolio untouched.
            if disposition in _PORTFOLIO_CHANGING_DISPOSITIONS:
                self._valuate(account, as_of)
            return {
                "status": "processed",
                "signal_id": signal.id,
                "action": signal.action,
                "disposition": disposition,
            }

    def run_daily_valuation(
        self,
        account_id: int,
        as_of_date: Optional[date] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Mark-to-market all open positions for a date and record a snapshot.

        By default the valuation is idempotent per day: an existing snapshot for
        ``as_of`` short-circuits. Pass ``force=True`` (e.g. from the manual refresh
        button) to re-price today even if a snapshot already exists.
        """
        account = self.paper_repo.get_account(account_id) or self.paper_repo.ensure_account()
        # Serialize valuation against signal consumption / backfill on the same
        # account so a stop-loss exit and a concurrent signal cannot interleave.
        with _account_lock(account.id):
            as_of = as_of_date or self.resolve_valuation_date(account)
            if not force and self.paper_repo.has_snapshot(account.id, as_of):
                # 已有当日快照时复用其数据，但补齐 trade_date 以满足 PaperValuationResponse。
                snap = self.get_snapshot(account.id)
                return {
                    "account_id": snap["account_id"],
                    "trade_date": as_of.isoformat(),
                    "cash": snap["cash"],
                    "market_value": snap["market_value"],
                    "net_value": snap["net_value"],
                    "return_pct": snap["return_pct"],
                }

            return self._valuate(account, as_of)

    def resolve_valuation_date(self, account) -> date:
        """Resolve the latest completed (finalized) daily-bar date to value at.

        Uses the market of the account's first open position so the date reflects
        that market's completed session (before close -> previous session, after
        close -> current session). This avoids recording a premature "today"
        snapshot priced against a not-yet-final daily bar. Falls back to today
        when the account has no open position or a calendar is unavailable.
        """
        positions = self.paper_repo.list_open_positions(account.id)
        if not positions:
            return date.today()
        market = get_market_for_stock(positions[0].stock_code)
        try:
            return get_effective_trading_date(market)
        except Exception:  # pragma: no cover - calendar failure; fall back to local today
            logger.warning("paper: resolve_valuation_date fallback to today", exc_info=True)
            return date.today()

    def latest_snapshot_date(self, account_id: int) -> Optional[date]:
        """Return the most recent equity-snapshot date, or None if none exists."""
        return self.paper_repo.latest_snapshot_date(account_id)

    def backfill_history(
        self,
        account_id: int,
        from_date: date,
        to_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Replay past decision signals chronologically to reconstruct history."""
        account = self.paper_repo.get_account(account_id) or self.paper_repo.ensure_account()
        to = to_date or date.today()
        signals = self._signals_in_range(from_date, to)
        logger.info("paper backfill: replaying %d signals in %s..%s", len(signals), from_date, to)

        processed = 0
        unavailable = 0
        # Serialize the whole replay per account so it never interleaves with live
        # signal consumption or the valuation task on the same account.
        with _account_lock(account.id):
            for signal in signals:
                if self.paper_repo.has_signal_record(account.id, signal.id):
                    continue
                as_of = self._signal_trade_date(signal)
                # 回填是重放，一次可能落几百笔成交，全程静音（notify=False）。
                disposition = self._handle_signal(account, signal, as_of, notify=False)
                if disposition == _DATA_UNAVAILABLE:
                    # 不记已消费，留给后续回填/运行重试，避免瞬时数据缺失永久丢单。
                    unavailable += 1
                    continue
                self.paper_repo.add_signal_record(account.id, signal.id, signal.action, disposition)
                if disposition in _PORTFOLIO_CHANGING_DISPOSITIONS:
                    self._valuate(account, as_of, notify=False)
                processed += 1

        return {
            "signals_unavailable": unavailable,

            "account_id": account.id,
            "from_date": from_date.isoformat(),
            "to_date": to.isoformat(),
            "signals_replayed": processed,
            "snapshot": self.get_snapshot(account.id),
        }

    def get_snapshot(self, account_id: int) -> Dict[str, Any]:
        account = self.paper_repo.get_account(account_id) or self.paper_repo.ensure_account()
        positions = self.paper_repo.list_open_positions(account.id)
        market_value = sum(float(p.market_value or 0) for p in positions)
        net_value = float(account.cash or 0) + market_value
        return_pct = (
            (net_value / account.initial_capital - 1.0) * 100
            if account.initial_capital
            else 0.0
        )
        return {
            "account_id": account.id,
            "cash": float(account.cash or 0),
            "market_value": market_value,
            "net_value": net_value,
            "return_pct": round(return_pct, 4),
            "initial_capital": float(account.initial_capital),
            "open_position_count": len(positions),
        }

    def get_equity_curve(
        self, account_id: int, start: Optional[date] = None, end: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        snaps = self.paper_repo.list_snapshots(account_id, start=start, end=end)
        return [
            {
                "trade_date": s.trade_date.isoformat(),
                "net_value": round(float(s.net_value or 0), 4),
                "return_pct": s.return_pct,
            }
            for s in snaps
        ]

    def get_positions(self, account_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        return [
            {
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "market": p.market,
                "quantity": float(p.quantity or 0),
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "entry_date": p.entry_date.isoformat() if p.entry_date else None,
                "stop_loss": p.stop_loss,
                "target_price": p.target_price,
                "status": p.status,
            }
            for p in self.paper_repo.list_positions(account_id, limit=limit)
        ]

    def get_trades(self, account_id: int, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        trades = self.paper_repo.list_trades(account_id, page=page, limit=limit)
        items = [
            {
                "stock_code": t.stock_code,
                "stock_name": t.stock_name,
                "side": t.side,
                "quantity": float(t.quantity or 0),
                "price": t.price,
                "amount": t.amount,
                "fee": t.fee or 0.0,
                "trade_date": t.trade_date.isoformat(),
                "reason": t.reason,
            }
            for t in trades
        ]
        return {"items": items, "total": self.paper_repo.count_trades(account_id)}

    def get_signals(self, account_id: int, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        records = self.paper_repo.list_signal_records(account_id, page=page, limit=limit)
        signal_ids = [r.signal_id for r in records if r.signal_id]
        signals_by_id = self.decision_repo.get_by_ids(signal_ids)
        items = [
            {
                "signal_id": r.signal_id,
                "action": r.action,
                "disposition": r.disposition,
                "processed_at": r.processed_at.isoformat(),
                "stock_code": (
                    signals_by_id[r.signal_id].stock_code if r.signal_id in signals_by_id else None
                ),
                "stock_name": (
                    signals_by_id[r.signal_id].stock_name if r.signal_id in signals_by_id else None
                ),
            }
            for r in records
        ]
        return {"items": items, "total": self.paper_repo.count_signal_records(account_id)}

    # ------------------------------------------------------------------
    # Core signal handling
    # ------------------------------------------------------------------
    def _notify_fill(self, account, trade, disposition: str, notify: bool) -> None:
        """Push one live fill to the configured channels.

        `notify=False` is the backfill path: a replay can emit hundreds of fills
        and pushing each one would flood the channels. The notifier itself never
        raises, and its own enabled/disabled gate lives in `paper_notify`.
        """
        if not notify or trade is None:
            return
        send_paper_fill_notification(
            trade,
            cash_after=float(getattr(account, "cash", 0.0) or 0.0),
            disposition=disposition,
        )

    def _handle_signal(
        self, account, signal: DecisionSignalRecord, as_of: date, notify: bool = True
    ) -> str:
        action = signal.action
        if action in _OPEN_ACTIONS:
            return self._open_or_add(account, signal, as_of, notify=notify)
        if action == "sell":
            return self._reduce_position(account, signal, as_of, fraction=1.0, notify=notify)
        if action == "reduce":
            return self._reduce_position(account, signal, as_of, fraction=0.5, notify=notify)
        # hold / watch / avoid / alert -> no position change
        return "ignored"

    def _open_or_add(
        self, account, signal: DecisionSignalRecord, as_of: date, notify: bool = True
    ) -> str:
        code = signal.stock_code
        bar = self._bar_for(code, as_of)
        if bar is None:
            # 拿不到当日行情 bar（数据源尚未落库）属瞬时不可用，区别于真正的观望：
            # 不落已消费，留给后续轮次重试（见 process_signal / backfill_history）。
            return _DATA_UNAVAILABLE
        fill_status, buy_price = self._entry_fill_price(signal, bar)
        if fill_status == _FILL_UNAVAILABLE:
            # bar 存在但缺 open/low，无法判定限价单是否成交，按数据不可用重试。
            return _DATA_UNAVAILABLE
        if fill_status != _FILLED or not buy_price or buy_price <= 0:
            return _NO_FILL
        buy_price = self._apply_slippage(buy_price, "buy")
        if signal.entry_high and float(signal.entry_high) > 0:
            # 限价买单不会成交在限价之上：滑点最多吃掉成交价优于限价的那部分。
            buy_price = min(buy_price, float(signal.entry_high))

        position = self.paper_repo.get_open_position(account.id, code)
        total = self._net_value(account)
        target_value = total * self.position_weight
        available_cash = max(float(account.cash or 0), 0.0)
        spendable = self._spendable_cash(available_cash, signal.market)

        if position is None:
            # Cap the buy to what cash actually covers; lot rounding never exceeds this.
            spend = min(target_value, spendable)
            if spend <= 0:
                return _NO_CASH
            quantity = self._buy_quantity(spend, buy_price, market=signal.market)
            if quantity <= 0:
                return _LOT_TOO_SMALL
            amount = buy_price * quantity
            fee = self._fee_for(amount, "buy", signal.market)
            self.paper_repo.upsert_position(
                account.id,
                code,
                {
                    "stock_name": signal.stock_name,
                    "market": signal.market,
                    "quantity": quantity,
                    # 成本价含买入费用，与券商展示口径一致；否则已实现盈亏对不上现金变动。
                    "avg_cost": (amount + fee) / quantity,
                    "current_price": buy_price,
                    "market_value": amount,
                    "open_signal_id": signal.id,
                    "entry_date": as_of,
                    "stop_loss": signal.stop_loss,
                    "target_price": signal.target_price,
                    "status": "open",
                },
            )
            self._apply_cash(account, -(amount + fee))
            trade = self.paper_repo.add_trade(
                account.id,
                signal_id=signal.id,
                stock_code=code,
                stock_name=signal.stock_name,
                side="buy",
                quantity=quantity,
                price=buy_price,
                amount=amount,
                fee=fee,
                trade_date=as_of,
                reason="signal_action",
            )
            self._notify_fill(account, trade, "opened", notify)
            return "opened"

        # Add to an existing position: never push past the target weight, and never
        # spend more than the cash on hand. Both bound runaway `add` accumulation.
        current_value = float(position.market_value or 0)
        if current_value >= target_value:
            return "hold"
        spend = min(target_value - current_value, spendable)
        if spend <= 0:
            # 防御分支：`position_weight` 被 `_default_position_weight` 夹在 (0, 1]，
            # 且现金不会被花成负数，所以「现金耗尽」通常已经先被上面的目标权重闸门
            # 拦成 `hold`（现金为 0 时 `target_value = weight * current_value <= current_value`）。
            # 只有持仓行的 `market_value` 落在退化状态（本仓为 0 而净值仍来自别处）时才会走到这里。
            return _NO_CASH
        quantity = self._buy_quantity(spend, buy_price, market=signal.market)
        if quantity <= 0:
            return _LOT_TOO_SMALL
        prev_qty = float(position.quantity or 0)
        prev_cost = float(position.avg_cost or buy_price)
        new_qty = prev_qty + quantity
        amount = buy_price * quantity
        fee = self._fee_for(amount, "buy", signal.market)
        # 原成本已含费，本次也在摊薄里带上本次费用，保持加权口径一致。
        new_cost = (prev_cost * prev_qty + amount + fee) / new_qty
        fields: Dict[str, Any] = {
            "quantity": new_qty,
            "avg_cost": new_cost,
            "current_price": buy_price,
            "market_value": buy_price * new_qty,
        }
        # 加仓信号可能带来新的风控线；有则更新，避免止损/目标一直钉死在开仓那天。
        if signal.stop_loss is not None:
            fields["stop_loss"] = signal.stop_loss
        if signal.target_price is not None:
            fields["target_price"] = signal.target_price
        self.paper_repo.upsert_position(account.id, code, fields)
        self._apply_cash(account, -(amount + fee))
        trade = self.paper_repo.add_trade(
            account.id,
            signal_id=signal.id,
            stock_code=code,
            stock_name=signal.stock_name,
            side="buy",
            quantity=quantity,
            price=buy_price,
            amount=amount,
            fee=fee,
            trade_date=as_of,
            reason="signal_action",
        )
        self._notify_fill(account, trade, "added", notify)
        return "added"

    def _reduce_position(
        self,
        account,
        signal: DecisionSignalRecord,
        as_of: date,
        fraction: float,
        notify: bool = True,
    ) -> str:
        code = signal.stock_code
        position = self.paper_repo.get_open_position(account.id, code)
        if position is None:
            return _NO_POSITION

        sell_price = self._close_price(code, as_of) or position.current_price
        if not sell_price or sell_price <= 0:
            return _DATA_UNAVAILABLE
        market = getattr(position, "market", None) or signal.market
        sell_price = self._apply_slippage(sell_price, "sell")

        quantity = float(position.quantity or 0) * fraction
        quantity = self._round_lot(quantity, market=signal.market)
        if quantity <= 0:
            return _LOT_TOO_SMALL

        remaining = float(position.quantity or 0) - quantity
        if remaining <= 0:
            self.paper_repo.close_position(
                account.id,
                code,
                {"current_price": sell_price, "market_value": sell_price * quantity},
            )
            side = "sell"
            disposition = "closed"
        else:
            self.paper_repo.upsert_position(
                account.id,
                code,
                {
                    "quantity": remaining,
                    "current_price": sell_price,
                    "market_value": sell_price * remaining,
                },
            )
            side = "sell"
            disposition = "reduced"

        amount = sell_price * quantity
        fee = self._fee_for(amount, "sell", market)
        self._apply_cash(account, amount - fee)
        trade = self.paper_repo.add_trade(
            account.id,
            signal_id=signal.id,
            stock_code=code,
            stock_name=signal.stock_name,
            side=side,
            quantity=quantity,
            price=sell_price,
            amount=amount,
            fee=fee,
            trade_date=as_of,
            reason="signal_action",
        )
        self._notify_fill(account, trade, disposition, notify)
        return disposition

    # ------------------------------------------------------------------
    # Daily valuation & exits
    # ------------------------------------------------------------------
    def _valuate(self, account, as_of: date, notify: bool = True) -> Dict[str, Any]:
        """Mark-to-market open positions, trigger stop-loss/take-profit, snapshot."""
        positions = self.paper_repo.list_open_positions(account.id)
        for position in positions:
            bar = self._bar_for(position.stock_code, as_of)
            if bar is None:
                logger.warning(
                    "paper: 无 %s 在 %s 的行情 bar，估值跳过该标的", position.stock_code, as_of
                )
                continue
            close = bar.get("close")
            if close is None or close <= 0:
                continue

            # A position opened today is valued at today's close but does not also
            # check today's low/high for a stop-loss / take-profit exit: entry uses
            # the day's high, so checking the same day's range would systematically
            # stop out freshly opened positions on wide-range days.
            is_entry_day = position.entry_date is not None and position.entry_date == as_of
            if is_entry_day:
                self.paper_repo.upsert_position(
                    account.id,
                    position.stock_code,
                    {
                        "current_price": close,
                        "market_value": close * float(position.quantity or 0),
                    },
                )
                continue

            exit_price, exit_reason = self._daily_exit(position, bar)
            if exit_price is not None:
                self._close_by_exit(account, position, exit_price, as_of, exit_reason, notify=notify)
                continue

            self.paper_repo.upsert_position(
                account.id,
                position.stock_code,
                {
                    "current_price": close,
                    "market_value": close * float(position.quantity or 0),
                },
            )

        return self._record_snapshot(account, as_of)

    def _daily_exit(self, position, bar: Dict[str, float]) -> Tuple[Optional[float], str]:
        """Return (exit_price, reason) if today's bar triggers stop-loss or take-profit.

        A daily bar only says both levels were touched, not which came first. The
        open is the one ordering signal the bar does carry: an open at/below the
        stop gives the stop away at the bell, and an open at/above the target
        reaches the target first. Only a bar that opens *between* the two levels
        leaves the order genuinely unknown, and that residue is booked as
        `ambiguous_stop_loss` on the stop — pessimistically, so the simulated
        result never depends on the assumption that the good outcome came first.
        """
        low = bar.get("low")
        high = bar.get("high")
        stop_loss = position.stop_loss
        take_profit = position.target_price

        stop_hit = stop_loss is not None and low is not None and low <= stop_loss
        tp_hit = take_profit is not None and high is not None and high >= take_profit

        if stop_hit and tp_hit:
            open_price = bar.get("open")
            if open_price is not None and open_price > 0:
                open_price = float(open_price)
                if open_price <= float(stop_loss):
                    return self._exit_fill(stop_loss, bar, below=True), "stop_loss"
                if open_price >= float(take_profit):
                    return self._exit_fill(take_profit, bar, below=False), "take_profit"
            return self._exit_fill(stop_loss, bar, below=True), "ambiguous_stop_loss"
        if stop_hit:
            return self._exit_fill(stop_loss, bar, below=True), "stop_loss"
        if tp_hit:
            return self._exit_fill(take_profit, bar, below=False), "take_profit"
        return None, ""

    @staticmethod
    def _exit_fill(trigger: float, bar: Dict[str, float], below: bool) -> float:
        """Fill price for a triggered stop-loss / take-profit, gap-adjusted.

        A bar that opens beyond the trigger fills at the open — the level was
        already gone when the session started — while an intraday touch fills at
        the trigger itself. ``below`` marks a downside trigger. Booking the
        trigger price unconditionally overstated take-profits on gap-up opens and
        understated stop-losses on gap-down opens.
        """
        open_price = bar.get("open")
        if open_price is None or open_price <= 0:
            return float(trigger)
        open_price = float(open_price)
        return min(float(trigger), open_price) if below else max(float(trigger), open_price)

    def _close_by_exit(
        self, account, position, exit_price: float, as_of: date, reason: str, notify: bool = True
    ):
        quantity = float(position.quantity or 0)
        exit_price = self._apply_slippage(exit_price, "sell")
        market = getattr(position, "market", None)
        amount = exit_price * quantity
        fee = self._fee_for(amount, "sell", market)
        self._apply_cash(account, amount - fee)
        self.paper_repo.close_position(
            account.id,
            position.stock_code,
            {"current_price": exit_price, "market_value": exit_price * quantity},
        )
        trade = self.paper_repo.add_trade(
            account.id,
            signal_id=position.open_signal_id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            side="sell",
            quantity=quantity,
            price=exit_price,
            amount=amount,
            fee=fee,
            trade_date=as_of,
            reason=reason,
        )
        self._notify_fill(account, trade, "closed", notify)
        logger.info(
            "paper: closed %s %s @ %.2f (%s)", position.stock_code, quantity, exit_price, reason
        )

    def _record_snapshot(self, account, as_of: date) -> Dict[str, Any]:
        # Re-read open positions *after* the marking/exit loop above. The records
        # fetched at the start of `_valuate` are stale by then: upsert_position /
        # close_position write to the database, not to those objects. Summing them
        # priced every snapshot on the previous mark, and on an exit day it added
        # a position that no longer exists on top of the cash it was sold for.
        positions = self.paper_repo.list_open_positions(account.id)
        market_value = sum(float(p.market_value or 0) for p in positions)
        cash = float(account.cash or 0)
        net_value = cash + market_value
        return_pct = (
            (net_value / account.initial_capital - 1.0) * 100 if account.initial_capital else 0.0
        )
        self.paper_repo.add_snapshot(
            account.id, as_of, cash, market_value, net_value, round(return_pct, 4)
        )
        return {
            "account_id": account.id,
            "trade_date": as_of.isoformat(),
            "cash": round(cash, 4),
            "market_value": round(market_value, 4),
            "net_value": round(net_value, 4),
            "return_pct": round(return_pct, 4),
        }

    def _account_payload(self, account) -> Dict[str, Any]:
        return {
            "account_id": account.id,
            "name": account.name,
            "initial_capital": float(account.initial_capital or 0),
            "cash": float(account.cash or 0),
            "status": account.status,
            "snapshot": self.get_snapshot(account.id),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fee_for(self, amount: float, side: str, market: Optional[str]) -> float:
        """Commission + statutory fees charged on a fill of ``amount``.

        Rounding to 分 mirrors how brokers bill: each order's fee is a cent-level
        amount, so an unrounded total would leave cash off by fractions of a
        cent and make the trade log impossible to reconcile.
        """
        if not self.fee_enabled or amount <= 0:
            return 0.0
        profile = _MARKET_FEE_PROFILE.get(market or "", _MARKET_FEE_PROFILE["cn"])
        fee = max(amount * self.commission_rate, float(profile["min_commission"]))
        if side == "sell" or not profile["stamp_duty_sell_only"]:
            fee += amount * float(profile["stamp_duty_rate"])
        fee += amount * float(profile["transfer_fee_rate"])
        return round(fee, 2)

    def _apply_slippage(self, price: float, side: str) -> float:
        """Move a fill price against the order by ``slippage_bps``.

        A buy pays up and a sell gives up, so the simulated fill is never
        better than the level the plan assumed.
        """
        if self.slippage_bps <= 0 or price <= 0:
            return float(price)
        drift = float(price) * self.slippage_bps / 10000.0
        return float(price) + drift if side == "buy" else float(price) - drift

    def _spendable_cash(self, available_cash: float, market: Optional[str]) -> float:
        """Cash that can be spent on a fill once the fee it will incur is reserved.

        The fee is charged on top of the fill amount, so spending the whole cash
        balance would overdraw the account by the fee.
        """
        if not self.fee_enabled:
            return available_cash
        profile = _MARKET_FEE_PROFILE.get(market or "", _MARKET_FEE_PROFILE["cn"])
        # Upper bound on the fee rate; over-reserving by a sell-only 印花税 on a
        # buy is a few 万分之 that the order sizing can absorb, and never negative.
        max_rate = (
            self.commission_rate
            + float(profile["stamp_duty_rate"])
            + float(profile["transfer_fee_rate"])
        )
        return available_cash / (1.0 + max_rate) if max_rate > 0 else available_cash

    def _net_value(self, account) -> float:
        positions = self.paper_repo.list_open_positions(account.id)
        market_value = sum(float(p.market_value or 0) for p in positions)
        return float(account.cash or 0) + market_value

    def _apply_cash(self, account, delta: float):
        new_cash = float(account.cash or 0) + delta
        self.paper_repo.update_account(account.id, {"cash": new_cash})
        account.cash = new_cash

    @staticmethod
    def _signal_trade_date(signal: DecisionSignalRecord) -> date:
        """Resolve the session the signal's order can actually be worked on.

        ``created_at`` is stored UTC-naive (``utc_naive_now``), so it is
        converted to the stock's market timezone before picking a session: a
        signal produced after that market's close, or on a non-trading day, has
        no bar of its own and must roll to the next session. Fills are priced
        against a daily bar, so booking them on the timestamp date filled plans
        on days the market was shut.
        """
        created = getattr(signal, "created_at", None)
        if not isinstance(created, datetime):
            return created if isinstance(created, date) else date.today()

        market = getattr(signal, "market", None) or get_market_for_stock(
            getattr(signal, "stock_code", "") or ""
        )
        aware = created if created.tzinfo is not None else created.replace(tzinfo=timezone.utc)
        return resolve_fill_session(market, aware)

    @staticmethod
    def _lot_size(market: Optional[str]) -> int:
        """Board lot (整手) size per market.

        A 股 (cn) 和港股 (hk) 按整手交易，取常见默认 100 股；港股的整手随个股而异
        （100/500/1000…），这里用 100 作为保守近似。美股 (us) 无整手限制（可零股），
        按 1 股逐股买入。其他市场同样按 1 股处理。
        """
        if market in ("cn", "hk"):
            return 100
        return 1

    @staticmethod
    def _buy_quantity(target_value: float, price: float, market: Optional[str]) -> float:
        quantity = int(target_value / price) if price > 0 else 0
        lot = PaperService._lot_size(market)
        if lot > 1:
            quantity = int(quantity / lot) * lot
        return max(quantity, 0)

    @staticmethod
    def _round_lot(quantity: float, market: Optional[str]) -> float:
        lot = PaperService._lot_size(market)
        if lot > 1:
            return max(int(quantity / lot) * lot, 0)
        return quantity

    @staticmethod
    def _entry_fill_price(
        signal: DecisionSignalRecord, bar: Dict[str, float]
    ) -> Tuple[str, Optional[float]]:
        """Resolve the limit-order fill for a buy/add inside ``bar``.

        ``entry_high`` is the top of the plan's intended buy range, so the order
        is a limit at that price: it only fills when the session actually traded
        down to it, and then it fills at the session open (already inside the
        range) or at the limit itself. A session whose low stays above the limit
        never fills — the previous behaviour booked the limit price anyway, which
        filled plans the market never reached.

        Without a planned range the order is treated as a market order and fills
        at the session close.

        Returns ``(_FILLED, price)``, ``(_NO_FILL, None)`` when the range never
        reached the limit, or ``(_FILL_UNAVAILABLE, None)`` when the bar lacks the
        open/low needed to decide — a partial bar must not be read as a fill.
        """
        entry_high = signal.entry_high
        if not entry_high or float(entry_high) <= 0:
            close = bar.get("close")
            if close is None or close <= 0:
                return _FILL_UNAVAILABLE, None
            return _FILLED, float(close)

        limit = float(entry_high)
        low = bar.get("low")
        if low is None:
            return _FILL_UNAVAILABLE, None
        if float(low) > limit:
            return _NO_FILL, None

        open_price = bar.get("open")
        if open_price is None or open_price <= 0:
            return _FILLED, limit
        return _FILLED, min(float(open_price), limit)

    def _close_price(self, code: str, as_of: date) -> Optional[float]:
        bar = self._bar_for(code, as_of)
        return bar.get("close") if bar else None

    def _bar_for(self, code: str, as_of: date) -> Optional[Dict[str, float]]:
        bars = self._load_bars(code, as_of)
        return bars.get(as_of)

    def _load_bars(self, code: str, as_of: date) -> Dict[date, Dict[str, float]]:
        """Load (and cache) daily bars for a stock, covering as_of through today.

        The shared cache keys by stock and stores the loaded window
        ``(start, latest_bar, bars)``, where ``latest_bar`` is the newest date
        actually present in ``bars``. A request inside the window is served from
        cache; one asking earlier (a deep backfill replay) or later (a session
        whose bar has not been loaded yet) reloads a wider window. LRU eviction
        bounds memory.
        """

        end = date.today()
        with _BAR_CACHE_LOCK:
            entry = _BAR_CACHE.pop(code, None)
            if entry is not None:
                start, cached_end, bars = entry
                if start <= as_of <= cached_end:
                    _BAR_CACHE[code] = entry  # re-insert as most-recently-used
                    return bars

            start = as_of - timedelta(days=DEFAULT_LOOKBACK_DAYS)
            rows = self.db.get_data_range(code, start, end)
            bars: Dict[date, Dict[str, float]] = {}
            for row in rows:
                d = getattr(row, "date", None)
                if isinstance(d, datetime):
                    d = d.date()
                if d is None:
                    continue
                bars[d] = {
                    "open": getattr(row, "open", None),
                    "high": getattr(row, "high", None),
                    "low": getattr(row, "low", None),
                    "close": getattr(row, "close", None),
                }
            # Only cache a non-empty window. An empty (no price yet) result must not
            # be cached: a retryable signal waits for bars that may arrive later (e.g.
            # after a data fetch), and a fresh instance re-querying must see them.
            if bars:
                # 上界记 bars 里真实存在的最新日期，而不是查询用的 ``end``（今天）：
                # 盘中当天 bar 还没入库时，用 ``end`` 会把「没取到当天」缓存成
                # 「已覆盖当天」，之后同一进程里每次请求当天 bar 都命中并返回 None
                # ——当天止损/止盈不判定、快照按上一交易日市值落库且调度器视为
                # 「该日已算」不再重试，用户点刷新（force=True）也救不回来。
                _BAR_CACHE[code] = (start, max(bars), bars)
                # Bound memory: evict the least-recently-used entry on overflow.
                while len(_BAR_CACHE) > _BAR_CACHE_MAX_ENTRIES:
                    _BAR_CACHE.pop(next(iter(_BAR_CACHE)))
            return bars

    def _signals_in_range(self, from_date: date, to_date: date) -> List[DecisionSignalRecord]:
        """Fetch *all* signals in the date range, paginating past the repo's page cap."""
        from_dt = datetime.combine(from_date, datetime.min.time())
        to_dt = datetime.combine(to_date, datetime.max.time())
        rows: List[DecisionSignalRecord] = []
        page = 1
        page_size = 100
        while True:
            batch, _ = self.decision_repo.list(
                created_from=from_dt,
                created_to=to_dt,
                page=page,
                page_size=page_size,
            )
            rows.extend(batch)
            # The repo caps page_size at 100; a short page means we've reached the end.
            if len(batch) < page_size:
                break
            page += 1
        rows.sort(key=lambda s: (s.created_at or datetime.min))
        return rows

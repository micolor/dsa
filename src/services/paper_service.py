# -*- coding: utf-8 -*-
"""Paper-trading service: drives a virtual account from AI decision signals.

Tracks how well the AI's buy/add/sell/reduce recommendations would have
performed, producing positions, a daily equity curve and trade records.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.core.trading_calendar import get_effective_trading_date, get_market_for_stock
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.paper_repo import PaperRepository
from src.storage import DatabaseManager, DecisionSignalRecord

logger = logging.getLogger(__name__)

# Default target weight of total assets allocated per opened position. Overridable
# per service instance (e.g. from the `PAPER_POSITION_WEIGHT` config).
POSITION_WEIGHT = 0.20
# Default lookback used when loading price bars for live valuation.
DEFAULT_LOOKBACK_DAYS = 365

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
# trading day) reloads, so bars never go stale across days. An LRU cap bounds
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_or_create_account(self, initial_capital: float = 1000000.0) -> Dict[str, Any]:
        account = self.paper_repo.ensure_account(initial_capital=initial_capital)
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
                disposition = self._handle_signal(account, signal, as_of)
                if disposition == _DATA_UNAVAILABLE:
                    # 不记已消费，留给后续回填/运行重试，避免瞬时数据缺失永久丢单。
                    unavailable += 1
                    continue
                self.paper_repo.add_signal_record(account.id, signal.id, signal.action, disposition)
                if disposition in _PORTFOLIO_CHANGING_DISPOSITIONS:
                    self._valuate(account, as_of)
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
    def _handle_signal(self, account, signal: DecisionSignalRecord, as_of: date) -> str:
        action = signal.action
        if action in _OPEN_ACTIONS:
            return self._open_or_add(account, signal, as_of)
        if action == "sell":
            return self._reduce_position(account, signal, as_of, fraction=1.0)
        if action == "reduce":
            return self._reduce_position(account, signal, as_of, fraction=0.5)
        # hold / watch / avoid / alert -> no position change
        return "ignored"

    def _open_or_add(self, account, signal: DecisionSignalRecord, as_of: date) -> str:
        code = signal.stock_code
        buy_price = self._entry_price(signal, as_of)
        if not buy_price or buy_price <= 0:
            # 拿不到现价/入场价属数据源瞬时不可用，区别于真正的观望：不落已消费，
            # 留给后续轮次重试（见 process_signal / backfill_history）。
            return _DATA_UNAVAILABLE

        position = self.paper_repo.get_open_position(account.id, code)
        total = self._net_value(account)
        target_value = total * self.position_weight
        available_cash = max(float(account.cash or 0), 0.0)

        if position is None:
            # Cap the buy to what cash actually covers; lot rounding never exceeds this.
            spend = min(target_value, available_cash)
            if spend <= 0:
                return "ignored"
            quantity = self._buy_quantity(spend, buy_price, market=signal.market)
            if quantity <= 0:
                return "ignored"
            self.paper_repo.upsert_position(
                account.id,
                code,
                {
                    "stock_name": signal.stock_name,
                    "market": signal.market,
                    "quantity": quantity,
                    "avg_cost": buy_price,
                    "current_price": buy_price,
                    "market_value": buy_price * quantity,
                    "open_signal_id": signal.id,
                    "entry_date": as_of,
                    "stop_loss": signal.stop_loss,
                    "target_price": signal.target_price,
                    "status": "open",
                },
            )
            self._apply_cash(account, -buy_price * quantity)
            self.paper_repo.add_trade(
                account.id,
                signal_id=signal.id,
                stock_code=code,
                stock_name=signal.stock_name,
                side="buy",
                quantity=quantity,
                price=buy_price,
                amount=buy_price * quantity,
                trade_date=as_of,
                reason="signal_action",
            )
            return "opened"

        # Add to an existing position: never push past the target weight, and never
        # spend more than the cash on hand. Both bound runaway `add` accumulation.
        current_value = float(position.market_value or 0)
        if current_value >= target_value:
            return "hold"
        spend = min(target_value - current_value, available_cash)
        if spend <= 0:
            return "hold"
        quantity = self._buy_quantity(spend, buy_price, market=signal.market)
        if quantity <= 0:
            return "hold"
        prev_qty = float(position.quantity or 0)
        prev_cost = float(position.avg_cost or buy_price)
        new_qty = prev_qty + quantity
        new_cost = (prev_cost * prev_qty + buy_price * quantity) / new_qty
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
        self._apply_cash(account, -buy_price * quantity)
        self.paper_repo.add_trade(
            account.id,
            signal_id=signal.id,
            stock_code=code,
            stock_name=signal.stock_name,
            side="buy",
            quantity=quantity,
            price=buy_price,
            amount=buy_price * quantity,
            trade_date=as_of,
            reason="signal_action",
        )
        return "added"

    def _reduce_position(
        self, account, signal: DecisionSignalRecord, as_of: date, fraction: float
    ) -> str:
        code = signal.stock_code
        position = self.paper_repo.get_open_position(account.id, code)
        if position is None:
            return "ignored"

        sell_price = self._close_price(code, as_of) or position.current_price
        if not sell_price or sell_price <= 0:
            return _DATA_UNAVAILABLE

        quantity = float(position.quantity or 0) * fraction
        quantity = self._round_lot(quantity, market=signal.market)
        if quantity <= 0:
            return "ignored"

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

        self._apply_cash(account, sell_price * quantity)
        self.paper_repo.add_trade(
            account.id,
            signal_id=signal.id,
            stock_code=code,
            stock_name=signal.stock_name,
            side=side,
            quantity=quantity,
            price=sell_price,
            amount=sell_price * quantity,
            trade_date=as_of,
            reason="signal_action",
        )
        return disposition

    # ------------------------------------------------------------------
    # Daily valuation & exits
    # ------------------------------------------------------------------
    def _valuate(self, account, as_of: date) -> Dict[str, Any]:
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
                self._close_by_exit(account, position, exit_price, as_of, exit_reason)
                continue

            self.paper_repo.upsert_position(
                account.id,
                position.stock_code,
                {
                    "current_price": close,
                    "market_value": close * float(position.quantity or 0),
                },
            )

        return self._record_snapshot(account, as_of, positions)

    def _daily_exit(self, position, bar: Dict[str, float]) -> Tuple[Optional[float], str]:
        """Return (exit_price, reason) if today's bar triggers stop-loss or take-profit."""
        low = bar.get("low")
        high = bar.get("high")
        stop_loss = position.stop_loss
        take_profit = position.target_price

        stop_hit = stop_loss is not None and low is not None and low <= stop_loss
        tp_hit = take_profit is not None and high is not None and high >= take_profit

        if stop_hit and tp_hit:
            return stop_loss, "ambiguous_stop_loss"
        if stop_hit:
            return stop_loss, "stop_loss"
        if tp_hit:
            return take_profit, "take_profit"
        return None, ""

    def _close_by_exit(self, account, position, exit_price: float, as_of: date, reason: str):
        quantity = float(position.quantity or 0)
        self._apply_cash(account, exit_price * quantity)
        self.paper_repo.close_position(
            account.id,
            position.stock_code,
            {"current_price": exit_price, "market_value": exit_price * quantity},
        )
        self.paper_repo.add_trade(
            account.id,
            signal_id=position.open_signal_id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            side="sell",
            quantity=quantity,
            price=exit_price,
            amount=exit_price * quantity,
            trade_date=as_of,
            reason=reason,
        )
        logger.info(
            "paper: closed %s %s @ %.2f (%s)", position.stock_code, quantity, exit_price, reason
        )

    def _record_snapshot(self, account, as_of: date, positions=None) -> Dict[str, Any]:
        # `_valuate` already fetched open positions; pass them in to avoid a
        # second list_open_positions query for the same batch. Fall back to a
        # fresh query only when not provided (keeps other/legacy callers intact).
        if positions is None:
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
        created = getattr(signal, "created_at", None)
        if created is not None:
            if isinstance(created, datetime):
                return created.date()
            if isinstance(created, date):
                return created
        return date.today()

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

    def _entry_price(self, signal: DecisionSignalRecord, as_of: date) -> Optional[float]:
        if signal.entry_high:
            return float(signal.entry_high)
        return self._close_price(signal.stock_code, as_of)

    def _close_price(self, code: str, as_of: date) -> Optional[float]:
        bar = self._bar_for(code, as_of)
        return bar.get("close") if bar else None

    def _bar_for(self, code: str, as_of: date) -> Optional[Dict[str, float]]:
        bars = self._load_bars(code, as_of)
        return bars.get(as_of)

    def _load_bars(self, code: str, as_of: date) -> Dict[date, Dict[str, float]]:
        """Load (and cache) daily bars for a stock, covering as_of through today.

        The shared cache keys by stock and stores the loaded window
        ``(start, cached_end, bars)``. A request inside the window is served from
        cache; one asking earlier (a deep backfill replay) or later (a new trading
        day) reloads a wider window. LRU eviction bounds memory. The window check
        guards against stale bars: bars are loaded through ``today`` at fetch time,
        so a request for a date past ``cached_end`` must reload to pick up newer data.
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
                    "high": getattr(row, "high", None),
                    "low": getattr(row, "low", None),
                    "close": getattr(row, "close", None),
                }
            # Only cache a non-empty window. An empty (no price yet) result must not
            # be cached: a retryable signal waits for bars that may arrive later (e.g.
            # after a data fetch), and a fresh instance re-querying must see them.
            if bars:
                _BAR_CACHE[code] = (start, end, bars)
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

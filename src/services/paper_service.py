# -*- coding: utf-8 -*-
"""Paper-trading service: drives a virtual account from AI decision signals.

Tracks how well the AI's buy/add/sell/reduce recommendations would have
performed, producing positions, a daily equity curve and trade records.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.paper_repo import PaperRepository
from src.storage import DatabaseManager, DecisionSignalRecord

logger = logging.getLogger(__name__)

# Default target weight of total assets allocated per opened position.
POSITION_WEIGHT = 0.20
# Default lookback used when loading price bars for live valuation.
DEFAULT_LOOKBACK_DAYS = 365

# Actions that open or add to a position.
_OPEN_ACTIONS = ("buy", "add")
# Actions that close or reduce a position.
_CLOSE_ACTIONS = ("sell", "reduce")


class PaperService:
    """Core paper-trading engine (signal consumption + daily valuation + backfill)."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        paper_repo: Optional[PaperRepository] = None,
        decision_repo: Optional[DecisionSignalRepository] = None,
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self.paper_repo = paper_repo or PaperRepository(self.db)
        self.decision_repo = decision_repo or DecisionSignalRepository(self.db)
        self._bar_cache: Dict[str, Dict[date, Dict[str, float]]] = {}

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

        if self.paper_repo.has_signal_record(account.id, signal.id):
            return {"status": "skipped", "signal_id": signal.id}

        as_of = self._signal_trade_date(signal)
        disposition = self._handle_signal(account, signal, as_of)
        self.paper_repo.add_signal_record(account.id, signal.id, signal.action, disposition)

        # Reflect the resulting portfolio state for that day.
        self._valuate(account, as_of)
        return {
            "status": "processed",
            "signal_id": signal.id,
            "action": signal.action,
            "disposition": disposition,
        }

    def run_daily_valuation(self, account_id: int, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        """Mark-to-market all open positions for a date and record a snapshot."""
        account = self.paper_repo.get_account(account_id) or self.paper_repo.ensure_account()
        as_of = as_of_date or date.today()
        if self.paper_repo.has_snapshot(account.id, as_of):
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
        for signal in signals:
            if self.paper_repo.has_signal_record(account.id, signal.id):
                continue
            as_of = self._signal_trade_date(signal)
            disposition = self._handle_signal(account, signal, as_of)
            self.paper_repo.add_signal_record(account.id, signal.id, signal.action, disposition)
            self._valuate(account, as_of)
            processed += 1

        return {
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
            return "ignored"

        position = self.paper_repo.get_open_position(account.id, code)
        total = self._net_value(account)
        target_value = total * POSITION_WEIGHT

        if position is None:
            quantity = self._buy_quantity(target_value, buy_price, market=signal.market)
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

        # Add to existing position.
        quantity = self._buy_quantity(target_value, buy_price, market=signal.market)
        if quantity <= 0:
            return "hold"
        prev_qty = float(position.quantity or 0)
        prev_cost = float(position.avg_cost or buy_price)
        new_qty = prev_qty + quantity
        new_cost = (prev_cost * prev_qty + buy_price * quantity) / new_qty
        self.paper_repo.upsert_position(
            account.id,
            code,
            {
                "quantity": new_qty,
                "avg_cost": new_cost,
                "current_price": buy_price,
                "market_value": buy_price * new_qty,
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
            return "ignored"

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
                continue
            close = bar.get("close")
            if close is None or close <= 0:
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

        return self._record_snapshot(account, as_of)

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

    def _record_snapshot(self, account, as_of: date) -> Dict[str, Any]:
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
    def _buy_quantity(target_value: float, price: float, market: Optional[str]) -> float:
        quantity = int(target_value / price) if price > 0 else 0
        # A-share (cn) trades in lots of 100.
        if market == "cn":
            quantity = int(quantity / 100) * 100
        return max(quantity, 0)

    @staticmethod
    def _round_lot(quantity: float, market: Optional[str]) -> float:
        if market == "cn":
            return max(int(quantity / 100) * 100, 0)
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
        """Load (and cache) daily bars for a stock, covering as_of through today."""
        if code not in self._bar_cache:
            start = as_of - timedelta(days=DEFAULT_LOOKBACK_DAYS)
            # Load through today so later valuation dates stay within the cache.
            end = date.today()
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
            self._bar_cache[code] = bars
        return self._bar_cache[code]

    def _signals_in_range(self, from_date: date, to_date: date) -> List[DecisionSignalRecord]:
        rows, _ = self.decision_repo.list(
            created_from=datetime.combine(from_date, datetime.min.time()),
            created_to=datetime.combine(to_date, datetime.max.time()),
            page_size=100,
        )
        rows = list(rows)
        rows.sort(key=lambda s: (s.created_at or datetime.min))
        return rows

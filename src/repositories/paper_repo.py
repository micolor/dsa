# -*- coding: utf-8 -*-
"""Paper-trading repository.

Provides DB access helpers for the paper (virtual) trading account that tracks
how well AI decision-signal recommendations would have performed.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, select

from src.storage import (
    DatabaseManager,
    PaperAccountRecord,
    PaperEquitySnapshotRecord,
    PaperPositionRecord,
    PaperSignalRecord,
    PaperTradeRecord,
)

logger = logging.getLogger(__name__)


class PaperRepository:
    """Data access for paper_* tables."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def ensure_account(self, initial_capital: float = 1000000.0) -> PaperAccountRecord:
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(PaperAccountRecord)
                    .where(PaperAccountRecord.status == 'active')
                    .order_by(PaperAccountRecord.id.asc())
                    .limit(1)
                ).scalars().first()
            )
            if row is not None:
                return row
            account = PaperAccountRecord(
                name='模拟盘',
                initial_capital=initial_capital,
                cash=initial_capital,
                status='active',
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_account(self, account_id: int) -> Optional[PaperAccountRecord]:
        with self.db.get_session() as session:
            return (
                session.execute(
                    select(PaperAccountRecord).where(PaperAccountRecord.id == account_id).limit(1)
                ).scalars().first()
            )

    def list_accounts(self) -> List[PaperAccountRecord]:
        with self.db.get_session() as session:
            return list(session.execute(select(PaperAccountRecord)).scalars())

    def update_account(self, account_id: int, fields: Dict[str, Any]) -> Optional[PaperAccountRecord]:
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(PaperAccountRecord).where(PaperAccountRecord.id == account_id).limit(1)
                ).scalars().first()
            )
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return row

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def upsert_position(
        self,
        account_id: int,
        stock_code: str,
        fields: Dict[str, Any],
    ) -> PaperPositionRecord:
        """Merge into the single open-position row for (account, stock)."""
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(PaperPositionRecord)
                    .where(
                        PaperPositionRecord.account_id == account_id,
                        PaperPositionRecord.stock_code == stock_code,
                        PaperPositionRecord.status == 'open',
                    )
                    .limit(1)
                ).scalars().first()
            )
            if row is None:
                row = PaperPositionRecord(account_id=account_id, stock_code=stock_code)
                session.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return row

    def get_open_position(self, account_id: int, stock_code: str) -> Optional[PaperPositionRecord]:
        with self.db.get_session() as session:
            return (
                session.execute(
                    select(PaperPositionRecord)
                    .where(
                        PaperPositionRecord.account_id == account_id,
                        PaperPositionRecord.stock_code == stock_code,
                        PaperPositionRecord.status == 'open',
                    )
                    .limit(1)
                ).scalars().first()
            )

    def list_open_positions(self, account_id: int) -> List[PaperPositionRecord]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PaperPositionRecord)
                    .where(
                        PaperPositionRecord.account_id == account_id,
                        PaperPositionRecord.status == 'open',
                    )
                    .order_by(PaperPositionRecord.entry_date.asc())
                ).scalars()
            )

    def list_positions(self, account_id: int, limit: int = 200) -> List[PaperPositionRecord]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PaperPositionRecord)
                    .where(PaperPositionRecord.account_id == account_id)
                    .order_by(PaperPositionRecord.created_at.desc())
                    .limit(limit)
                ).scalars()
            )

    def close_position(
        self,
        account_id: int,
        stock_code: str,
        fields: Dict[str, Any],
    ) -> Optional[PaperPositionRecord]:
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(PaperPositionRecord)
                    .where(
                        PaperPositionRecord.account_id == account_id,
                        PaperPositionRecord.stock_code == stock_code,
                        PaperPositionRecord.status == 'open',
                    )
                    .limit(1)
                ).scalars().first()
            )
            if row is None:
                return None
            row.status = 'closed'
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return row

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def add_trade(self, account_id: int, **fields: Any) -> PaperTradeRecord:
        with self.db.get_session() as session:
            row = PaperTradeRecord(account_id=account_id, **fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_trades(self, account_id: int, page: int = 1, limit: int = 50) -> List[PaperTradeRecord]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PaperTradeRecord)
                    .where(PaperTradeRecord.account_id == account_id)
                    .order_by(PaperTradeRecord.created_at.desc())
                    .offset(max(0, page - 1) * limit)
                    .limit(limit)
                ).scalars()
            )

    def count_trades(self, account_id: int) -> int:
        with self.db.get_session() as session:
            return int(
                session.execute(
                    select(func.count()).select_from(PaperTradeRecord).where(
                        PaperTradeRecord.account_id == account_id
                    )
                ).scalar_one()
            )

    # ------------------------------------------------------------------
    # Signal consumption records
    # ------------------------------------------------------------------
    def has_signal_record(self, account_id: int, signal_id: int) -> bool:
        with self.db.get_session() as session:
            exists = session.execute(
                select(func.count()).select_from(PaperSignalRecord).where(
                    PaperSignalRecord.account_id == account_id,
                    PaperSignalRecord.signal_id == signal_id,
                )
            ).scalar_one()
            return int(exists) > 0

    def add_signal_record(
        self,
        account_id: int,
        signal_id: int,
        action: str,
        disposition: str,
    ) -> PaperSignalRecord:
        with self.db.get_session() as session:
            row = PaperSignalRecord(
                account_id=account_id,
                signal_id=signal_id,
                action=action,
                disposition=disposition,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_signal_records(self, account_id: int, page: int = 1, limit: int = 50) -> List[PaperSignalRecord]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PaperSignalRecord)
                    .where(PaperSignalRecord.account_id == account_id)
                    .order_by(PaperSignalRecord.processed_at.desc())
                    .offset(max(0, page - 1) * limit)
                    .limit(limit)
                ).scalars()
            )

    def count_signal_records(self, account_id: int) -> int:
        with self.db.get_session() as session:
            return int(
                session.execute(
                    select(func.count()).select_from(PaperSignalRecord).where(
                        PaperSignalRecord.account_id == account_id
                    )
                ).scalar_one()
            )

    # ------------------------------------------------------------------
    # Equity snapshots
    # ------------------------------------------------------------------
    def add_snapshot(
        self,
        account_id: int,
        trade_date: date,
        cash: float,
        market_value: float,
        net_value: float,
        return_pct: Optional[float],
    ) -> PaperEquitySnapshotRecord:
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(PaperEquitySnapshotRecord)
                    .where(
                        PaperEquitySnapshotRecord.account_id == account_id,
                        PaperEquitySnapshotRecord.trade_date == trade_date,
                    )
                    .limit(1)
                ).scalars().first()
            )
            if row is None:
                row = PaperEquitySnapshotRecord(account_id=account_id, trade_date=trade_date)
                session.add(row)
            row.cash = cash
            row.market_value = market_value
            row.net_value = net_value
            row.return_pct = return_pct
            session.commit()
            session.refresh(row)
            return row

    def has_snapshot(self, account_id: int, trade_date: date) -> bool:
        with self.db.get_session() as session:
            exists = session.execute(
                select(func.count()).select_from(PaperEquitySnapshotRecord).where(
                    PaperEquitySnapshotRecord.account_id == account_id,
                    PaperEquitySnapshotRecord.trade_date == trade_date,
                )
            ).scalar_one()
            return int(exists) > 0

    def latest_snapshot_date(self, account_id: int) -> Optional[date]:
        """Return the most recent equity-snapshot date, or None if none exists."""
        with self.db.get_session() as session:
            row = session.execute(
                select(PaperEquitySnapshotRecord.trade_date)
                .where(PaperEquitySnapshotRecord.account_id == account_id)
                .order_by(desc(PaperEquitySnapshotRecord.trade_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def list_snapshots(
        self,
        account_id: int,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[PaperEquitySnapshotRecord]:
        with self.db.get_session() as session:
            query = select(PaperEquitySnapshotRecord).where(
                PaperEquitySnapshotRecord.account_id == account_id
            )
            if start is not None:
                query = query.where(PaperEquitySnapshotRecord.trade_date >= start)
            if end is not None:
                query = query.where(PaperEquitySnapshotRecord.trade_date <= end)
            return list(
                session.execute(query.order_by(PaperEquitySnapshotRecord.trade_date.asc())).scalars()
            )

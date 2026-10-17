# -*- coding: utf-8 -*-
"""Repository for fund NAV review anchors and evaluated outcomes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, select

from src.storage import (
    AnalysisHistory,
    DatabaseManager,
    FundNavReviewRecord,
)


class FundNavReviewRepository:
    """DB access for the independent ``fund_nav_reviews`` ledger."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def list_existing_horizons(
        self,
        *,
        fund_code: str,
        anchor_date: date,
        engine_version: str,
    ) -> List[str]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(FundNavReviewRecord.horizon).where(
                    and_(
                        FundNavReviewRecord.fund_code == fund_code,
                        FundNavReviewRecord.anchor_date == anchor_date,
                        FundNavReviewRecord.engine_version == engine_version,
                    )
                )
            ).scalars().all()
            return [str(row) for row in rows]

    def insert_reviews(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        with self.db.get_session() as session:
            session.add_all([FundNavReviewRecord(**row) for row in rows])
            session.commit()
            return len(rows)

    def list_due_reviews(
        self,
        *,
        engine_version: str,
        as_of: date,
        horizons: Optional[List[str]] = None,
        fund_code: Optional[str] = None,
        force: bool = False,
        checked_before: Optional[datetime] = None,
        limit: int = 400,
    ) -> List[FundNavReviewRecord]:
        safe_limit = max(1, min(int(limit), 400))
        conditions = [FundNavReviewRecord.engine_version == engine_version]
        if horizons:
            conditions.append(FundNavReviewRecord.horizon.in_(horizons))
        if fund_code:
            conditions.append(FundNavReviewRecord.fund_code == fund_code)
        if not force:
            conditions.extend([
                FundNavReviewRecord.eval_status == 'pending',
                FundNavReviewRecord.due_date <= as_of,
            ])
            if checked_before is not None:
                conditions.append(
                    or_(
                        FundNavReviewRecord.checked_at.is_(None),
                        FundNavReviewRecord.checked_at <= checked_before,
                    )
                )
        where_clause = and_(*conditions)
        with self.db.get_session() as session:
            rows = session.execute(
                select(FundNavReviewRecord)
                .where(where_clause)
                .order_by(
                    FundNavReviewRecord.checked_at.asc().nullsfirst(),
                    FundNavReviewRecord.due_date,
                    FundNavReviewRecord.id,
                )
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def list_reviews(
        self,
        *,
        engine_version: str,
        fund_code: Optional[str] = None,
        horizon: Optional[str] = None,
        eval_status: Optional[str] = None,
        anchor_from: Optional[date] = None,
        anchor_to: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[FundNavReviewRecord], int]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        conditions = [FundNavReviewRecord.engine_version == engine_version]
        if fund_code:
            conditions.append(FundNavReviewRecord.fund_code == fund_code)
        if horizon:
            conditions.append(FundNavReviewRecord.horizon == horizon)
        if eval_status:
            conditions.append(FundNavReviewRecord.eval_status == eval_status)
        if anchor_from:
            conditions.append(FundNavReviewRecord.anchor_date >= anchor_from)
        if anchor_to:
            conditions.append(FundNavReviewRecord.anchor_date <= anchor_to)
        where_clause = and_(*conditions)
        offset = (safe_page - 1) * safe_page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(FundNavReviewRecord.id))
                .select_from(FundNavReviewRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(FundNavReviewRecord)
                .where(where_clause)
                .order_by(
                    desc(FundNavReviewRecord.anchor_date),
                    desc(FundNavReviewRecord.id),
                )
                .offset(offset)
                .limit(safe_page_size)
            ).scalars().all()
            return list(rows), int(total)

    def list_stats_rows(
        self,
        *,
        engine_version: str,
        horizons: Optional[List[str]] = None,
    ) -> List[FundNavReviewRecord]:
        conditions = [FundNavReviewRecord.engine_version == engine_version]
        if horizons:
            conditions.append(FundNavReviewRecord.horizon.in_(horizons))
        with self.db.get_session() as session:
            rows = session.execute(
                select(FundNavReviewRecord).where(and_(*conditions))
            ).scalars().all()
            return list(rows)

    def list_restore_backlog(
        self,
        *,
        engine_version: str,
        fund_code: Optional[str] = None,
        horizon: Optional[str] = None,
        anchor_from: Optional[date] = None,
        anchor_to: Optional[date] = None,
        force: bool = False,
        limit: int = 400,
    ) -> List[FundNavReviewRecord]:
        """Rows that still need restore metrics computed.

        Only already-evaluated rows are candidates: a row without an evaluation
        has no window to restore. ``force`` re-processes rows that already have
        ``restored_checked_at`` so operators can refresh after an algorithm or
        data-source fix.
        """
        safe_limit = max(1, min(int(limit), 400))
        conditions = [
            FundNavReviewRecord.engine_version == engine_version,
            FundNavReviewRecord.eval_status == 'evaluated',
        ]
        if fund_code:
            conditions.append(FundNavReviewRecord.fund_code == fund_code)
        if horizon:
            conditions.append(FundNavReviewRecord.horizon == horizon)
        if anchor_from:
            conditions.append(FundNavReviewRecord.anchor_date >= anchor_from)
        if anchor_to:
            conditions.append(FundNavReviewRecord.anchor_date <= anchor_to)
        if not force:
            conditions.append(FundNavReviewRecord.restored_checked_at.is_(None))
        with self.db.get_session() as session:
            rows = session.execute(
                select(FundNavReviewRecord)
                .where(and_(*conditions))
                .order_by(
                    FundNavReviewRecord.restored_checked_at.asc().nullsfirst(),
                    FundNavReviewRecord.anchor_date,
                    FundNavReviewRecord.id,
                )
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def update_review(self, *, review_id: int, fields: Dict[str, Any]) -> bool:
        allowed = {
            'fund_name',
            'eval_status',
            'unable_reason',
            'eval_date',
            'eval_nav',
            'nav_return_pct',
            'max_drawdown_pct',
            'eval_window_records',
            'attempts',
            'last_error',
            'checked_at',
            'evaluated_at',
            'anchor_acc_nav',
            'eval_acc_nav',
            'acc_nav_return_pct',
            'acc_max_drawdown_pct',
            'restored_return_pct',
            'restored_max_drawdown_pct',
            'restored_basis',
            'restored_reason',
            'restored_events',
            'restored_checked_at',
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return False
        with self.db.get_session() as session:
            row = session.get(FundNavReviewRecord, int(review_id))
            if row is None:
                return False
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()
            return True

    def list_fund_analysis_histories(
        self,
        *,
        fund_code: Optional[str] = None,
        since: Optional[date] = None,
        until: Optional[date] = None,
        limit: int = 200,
    ) -> List[AnalysisHistory]:
        safe_limit = max(1, min(int(limit), 500))
        conditions = [AnalysisHistory.report_type == 'fund']
        if fund_code:
            conditions.append(AnalysisHistory.code == fund_code)
        if since:
            conditions.append(AnalysisHistory.created_at >= datetime.combine(since, datetime.min.time()))
        if until:
            conditions.append(
                AnalysisHistory.created_at < datetime.combine(until, datetime.max.time())
            )
        with self.db.get_session() as session:
            rows = session.execute(
                select(AnalysisHistory)
                .where(and_(*conditions))
                .order_by(AnalysisHistory.created_at.asc(), AnalysisHistory.id.asc())
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

# -*- coding: utf-8 -*-
"""Data quality discrepancy repository.

DB access for cross-source reconciliation findings (``data_quality_discrepancies``).
Mirrors ``NotificationDeliveryRepository``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, DataQualityDiscrepancyRecord


class DataQualityDiscrepancyRepository:
    """DB access layer for cross-source data-quality discrepancy rows."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def record_discrepancy(self, fields: Dict[str, Any]) -> DataQualityDiscrepancyRecord:
        with self.db.get_session() as session:
            row = DataQualityDiscrepancyRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_discrepancies(
        self,
        *,
        market: Optional[str] = None,
        stock_code: Optional[str] = None,
        issue_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[DataQualityDiscrepancyRecord], int]:
        conditions = []
        if market:
            conditions.append(DataQualityDiscrepancyRecord.market == market)
        if stock_code:
            conditions.append(DataQualityDiscrepancyRecord.stock_code == stock_code)
        if issue_type:
            conditions.append(DataQualityDiscrepancyRecord.issue_type == issue_type)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DataQualityDiscrepancyRecord.id))
                .select_from(DataQualityDiscrepancyRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(DataQualityDiscrepancyRecord)
                .where(where_clause)
                .order_by(
                    desc(DataQualityDiscrepancyRecord.created_at),
                    desc(DataQualityDiscrepancyRecord.id),
                )
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)

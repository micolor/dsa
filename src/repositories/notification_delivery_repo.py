# -*- coding: utf-8 -*-
"""Notification delivery repository.

DB access for generic (non-alert) notification delivery receipts
(``notification_deliveries``). Mirrors ``AlertRepository``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, NotificationDeliveryRecord


class NotificationDeliveryRepository:
    """DB access layer for generic notification delivery receipts."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def record_delivery(self, fields: Dict[str, Any]) -> NotificationDeliveryRecord:
        with self.db.get_session() as session:
            row = NotificationDeliveryRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_deliveries(
        self,
        *,
        route_type: Optional[str] = None,
        channel: Optional[str] = None,
        success: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[NotificationDeliveryRecord], int]:
        conditions = []
        if route_type:
            conditions.append(NotificationDeliveryRecord.route_type == route_type)
        if channel:
            conditions.append(NotificationDeliveryRecord.channel == channel)
        if success is not None:
            conditions.append(NotificationDeliveryRecord.success.is_(success))

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(NotificationDeliveryRecord.id))
                .select_from(NotificationDeliveryRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(NotificationDeliveryRecord)
                .where(where_clause)
                .order_by(
                    desc(NotificationDeliveryRecord.created_at),
                    desc(NotificationDeliveryRecord.id),
                )
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)

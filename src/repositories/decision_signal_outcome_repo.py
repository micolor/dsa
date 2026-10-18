# -*- coding: utf-8 -*-
"""Repository for DecisionSignal feedback and forward outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, select, update

from src.storage import (
    DatabaseManager,
    DecisionSignalFeedbackRecord,
    DecisionSignalOutcomeRecord,
    DecisionSignalRecord,
    utc_naive_now,
)


@dataclass(frozen=True)
class OutcomeStatsRow:
    """Outcome row plus the live signal fields required by profile calibration."""

    outcome: DecisionSignalOutcomeRecord
    decision_profile: Optional[str]
    metadata_json: Optional[str]


@dataclass(frozen=True)
class OutcomeLedgerRow:
    """Outcome plus the signal identity shown in the review ledger."""

    outcome: DecisionSignalOutcomeRecord
    stock_code: Optional[str]
    stock_name: Optional[str]


class DecisionSignalOutcomeRepository:
    """DB access for signal-level outcome and feedback sidecar tables."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def list_candidate_signals(
        self,
        *,
        signal_id: Optional[int] = None,
        stock_codes: Optional[List[str]] = None,
        market: Optional[str] = None,
        action: Optional[str] = None,
        source_type: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        exclude_ids: Optional[List[int]] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[DecisionSignalRecord]:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        conditions = []
        if signal_id is not None:
            conditions.append(DecisionSignalRecord.id == signal_id)
        if exclude_ids:
            conditions.append(DecisionSignalRecord.id.not_in(list(exclude_ids)))
        if stock_codes:
            conditions.append(DecisionSignalRecord.stock_code.in_(stock_codes))
        if market:
            conditions.append(DecisionSignalRecord.market == market)
        if action:
            conditions.append(DecisionSignalRecord.action == action)
        if source_type:
            conditions.append(DecisionSignalRecord.source_type == source_type)
        if statuses:
            conditions.append(DecisionSignalRecord.status.in_(statuses))
        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            rows = session.execute(
                select(DecisionSignalRecord)
                .where(where_clause)
                .order_by(desc(DecisionSignalRecord.created_at), desc(DecisionSignalRecord.id))
                .offset(safe_offset)
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def list_outcomes_for_signals(
        self,
        *,
        signal_ids: List[int],
        engine_version: str,
    ) -> List[DecisionSignalOutcomeRecord]:
        if not signal_ids:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(DecisionSignalOutcomeRecord)
                .where(
                    DecisionSignalOutcomeRecord.signal_id.in_(signal_ids),
                    DecisionSignalOutcomeRecord.engine_version == engine_version,
                )
            ).scalars().all()
            return list(rows)

    def get_outcome(
        self,
        *,
        signal_id: int,
        horizon: str,
        engine_version: str,
    ) -> Optional[DecisionSignalOutcomeRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(DecisionSignalOutcomeRecord)
                .where(
                    DecisionSignalOutcomeRecord.signal_id == signal_id,
                    DecisionSignalOutcomeRecord.horizon == horizon,
                    DecisionSignalOutcomeRecord.engine_version == engine_version,
                )
                .limit(1)
            ).scalar_one_or_none()

    def upsert_outcome(self, fields: Dict[str, Any]) -> Tuple[DecisionSignalOutcomeRecord, bool]:
        now = utc_naive_now()
        with self.db.get_session() as session:
            existing = session.execute(
                select(DecisionSignalOutcomeRecord)
                .where(
                    DecisionSignalOutcomeRecord.signal_id == fields["signal_id"],
                    DecisionSignalOutcomeRecord.horizon == fields["horizon"],
                    DecisionSignalOutcomeRecord.engine_version == fields["engine_version"],
                )
                .limit(1)
            ).scalar_one_or_none()
            if existing is None:
                row = DecisionSignalOutcomeRecord(**fields)
                session.add(row)
                session.commit()
                session.refresh(row)
                return row, True

            for key, value in fields.items():
                if key in {"id", "created_at"}:
                    continue
                setattr(existing, key, value)
            existing.updated_at = now
            session.commit()
            session.refresh(existing)
            return existing, False

    def list_outcomes(
        self,
        *,
        signal_id: Optional[int] = None,
        horizon: Optional[str] = None,
        engine_version: Optional[str] = None,
        eval_status: Optional[str] = None,
        outcome: Optional[str] = None,
        market: Optional[str] = None,
        action: Optional[str] = None,
        anchor_from: Optional[date] = None,
        anchor_to: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[OutcomeLedgerRow], int]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        conditions = []
        if signal_id is not None:
            conditions.append(DecisionSignalOutcomeRecord.signal_id == signal_id)
        if horizon:
            conditions.append(DecisionSignalOutcomeRecord.horizon == horizon)
        if engine_version:
            conditions.append(DecisionSignalOutcomeRecord.engine_version == engine_version)
        if eval_status:
            conditions.append(DecisionSignalOutcomeRecord.eval_status == eval_status)
        if outcome:
            conditions.append(DecisionSignalOutcomeRecord.outcome == outcome)
        if market:
            conditions.append(DecisionSignalOutcomeRecord.market == market)
        if action:
            conditions.append(DecisionSignalOutcomeRecord.action == action)
        if anchor_from:
            conditions.append(DecisionSignalOutcomeRecord.anchor_date >= anchor_from)
        if anchor_to:
            conditions.append(DecisionSignalOutcomeRecord.anchor_date <= anchor_to)
        where_clause = and_(*conditions) if conditions else True
        offset = (safe_page - 1) * safe_page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DecisionSignalOutcomeRecord.id))
                .select_from(DecisionSignalOutcomeRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(
                    DecisionSignalOutcomeRecord,
                    DecisionSignalRecord.stock_code,
                    DecisionSignalRecord.stock_name,
                )
                .outerjoin(
                    DecisionSignalRecord,
                    DecisionSignalRecord.id == DecisionSignalOutcomeRecord.signal_id,
                )
                .where(where_clause)
                .order_by(desc(DecisionSignalOutcomeRecord.updated_at), desc(DecisionSignalOutcomeRecord.id))
                .offset(offset)
                .limit(safe_page_size)
            ).all()
            return [
                OutcomeLedgerRow(
                    outcome=outcome_row,
                    stock_code=stock_code,
                    stock_name=stock_name,
                )
                for outcome_row, stock_code, stock_name in rows
            ], int(total)

    def list_outcomes_needing_benchmark(
        self,
        *,
        engine_version: str,
        checked_before: datetime,
        limit: int = 200,
    ) -> List[OutcomeLedgerRow]:
        """Completed outcomes whose benchmark might still be recoverable.

        `benchmark_checked_at IS NULL` means "never attempted". `unsupported`
        and `available` are terminal and never returned.
        """
        safe_limit = max(1, min(int(limit), 500))
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    DecisionSignalOutcomeRecord,
                    DecisionSignalRecord.stock_code,
                    DecisionSignalRecord.stock_name,
                )
                .outerjoin(
                    DecisionSignalRecord,
                    DecisionSignalRecord.id == DecisionSignalOutcomeRecord.signal_id,
                )
                .where(
                    and_(
                        DecisionSignalOutcomeRecord.engine_version == engine_version,
                        DecisionSignalOutcomeRecord.eval_status == "completed",
                        or_(
                            DecisionSignalOutcomeRecord.benchmark_status.is_(None),
                            DecisionSignalOutcomeRecord.benchmark_status.in_(
                                ("missing", "error")
                            ),
                        ),
                        or_(
                            DecisionSignalOutcomeRecord.benchmark_checked_at.is_(None),
                            DecisionSignalOutcomeRecord.benchmark_checked_at <= checked_before,
                        ),
                    )
                )
                .order_by(
                    DecisionSignalOutcomeRecord.benchmark_checked_at.asc().nullsfirst(),
                    DecisionSignalOutcomeRecord.id,
                )
                .limit(safe_limit)
            ).all()
            return [
                OutcomeLedgerRow(
                    outcome=outcome_row,
                    stock_code=stock_code,
                    stock_name=stock_name,
                )
                for outcome_row, stock_code, stock_name in rows
            ]

    def update_benchmark_fields(
        self,
        *,
        outcome_id: int,
        fields: Dict[str, Any],
    ) -> bool:
        """Update benchmark columns only, keeping `updated_at` untouched."""
        allowed = {
            "benchmark_code",
            "benchmark_return_pct",
            "excess_return_pct",
            "benchmark_status",
            "benchmark_checked_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return False
        values["updated_at"] = DecisionSignalOutcomeRecord.updated_at
        with self.db.get_session() as session:
            result = session.execute(
                update(DecisionSignalOutcomeRecord)
                .where(DecisionSignalOutcomeRecord.id == int(outcome_id))
                .values(**values)
            )
            session.commit()
            return bool(result.rowcount)

    def list_stats_rows(
        self,
        *,
        engine_version: str,
        horizons: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[OutcomeStatsRow]:
        conditions = [DecisionSignalOutcomeRecord.engine_version == engine_version]
        if horizons:
            conditions.append(DecisionSignalOutcomeRecord.horizon.in_(horizons))
        if statuses:
            conditions.append(DecisionSignalRecord.status.in_(statuses))
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    DecisionSignalOutcomeRecord,
                    DecisionSignalRecord.decision_profile,
                    DecisionSignalRecord.metadata_json,
                )
                .join(DecisionSignalRecord, DecisionSignalRecord.id == DecisionSignalOutcomeRecord.signal_id)
                .where(and_(*conditions))
            ).all()
            return [
                OutcomeStatsRow(
                    outcome=outcome,
                    decision_profile=decision_profile,
                    metadata_json=metadata_json,
                )
                for outcome, decision_profile, metadata_json in rows
            ]

    def get_feedback(self, *, signal_id: int) -> Optional[DecisionSignalFeedbackRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(DecisionSignalFeedbackRecord)
                .where(DecisionSignalFeedbackRecord.signal_id == signal_id)
                .limit(1)
            ).scalar_one_or_none()

    def upsert_feedback(self, fields: Dict[str, Any]) -> DecisionSignalFeedbackRecord:
        now = utc_naive_now()
        with self.db.get_session() as session:
            existing = session.execute(
                select(DecisionSignalFeedbackRecord)
                .where(DecisionSignalFeedbackRecord.signal_id == fields["signal_id"])
                .limit(1)
            ).scalar_one_or_none()
            if existing is None:
                row = DecisionSignalFeedbackRecord(**fields)
                session.add(row)
                session.commit()
                session.refresh(row)
                return row

            for key, value in fields.items():
                if key in {"id", "created_at"}:
                    continue
                setattr(existing, key, value)
            existing.updated_at = now
            session.commit()
            session.refresh(existing)
            return existing

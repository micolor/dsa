# -*- coding: utf-8 -*-
"""Forward NAV review for deterministic fund checks.

A fund check (``fund:<6 digits>``) never produces a decision signal. Instead,
the latest NAV observed during the check becomes an anchor; this service later
evaluates the 22/66/126/252nd NAV record after that anchor. The windows match
``data_provider.fund_fetcher.compute_metrics`` so the review uses the same
definition as the fund report's 1m/3m/6m/1y metrics.

The module is intentionally independent from ``decision_signals``: no buy/sell
signal, no hit/miss classification, no benchmark.
"""

from __future__ import annotations

import bisect
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from data_provider.fund_fetcher import (
    FundFetcher,
    FundProfile,
    NavRecord,
    _parse_nav_date,
    strip_fund_prefix,
)
from src.repositories.fund_nav_review_repo import FundNavReviewRepository
from src.storage import DatabaseManager, FundNavReviewRecord, utc_naive_now

logger = logging.getLogger(__name__)

FUND_NAV_REVIEW_ENGINE_VERSION = "fund-nav-v1"
FUND_NAV_REVIEW_HORIZONS = {"1m": 22, "3m": 66, "6m": 126, "1y": 252}
FUND_NAV_REVIEW_HORIZON_ORDER = ("1m", "3m", "6m", "1y")
# Calendar lower bounds used only to decide when it is worth fetching NAV
# history. A-share funds have ~243 NAV records per year, so 252 records often
# need more than 365 days; the record count, not this date, decides evaluation.
FUND_NAV_REVIEW_DUE_DAYS = {"1m": 35, "3m": 100, "6m": 190, "1y": 380}
CHECK_COOLDOWN = timedelta(hours=6)
STALE_GRACE = timedelta(days=30)
MAX_FUNDS_PER_RUN = 50
MAX_ROWS_PER_RUN = 400
MAX_BACKFILL_FUNDS = 50
BACKFILL_NAV_TOLERANCE = 0.00005

_FUND_CODE_RE = re.compile(r"^\d{6}$")


def _normalize_fund_code(value: Any) -> str:
    """Return a bare 6-digit fund code, or ``""`` when it is not a fund code."""
    raw = strip_fund_prefix(str(value or "").strip()).strip()
    return raw if _FUND_CODE_RE.fullmatch(raw) else ""


def _parse_date(value: Any) -> Optional[date]:
    candidate = _parse_nav_date(str(value or ""))
    return candidate


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [float(value) for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


class FundNavReviewService:
    """Record anchors from fund checks and evaluate their forward NAV outcomes."""

    def __init__(
        self,
        *,
        repo: Optional[FundNavReviewRepository] = None,
        fetcher: Optional[FundFetcher] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.repo = repo or FundNavReviewRepository(db_manager)
        self.fetcher = fetcher or FundFetcher()

    # ------------------------------------------------------------------
    # Anchors
    # ------------------------------------------------------------------
    def record_anchor(
        self,
        profile: FundProfile,
        *,
        analysis_history_id: Optional[int] = None,
        source: str = "pipeline",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Persist one anchor per horizon for the latest NAV in ``profile``.

        The call is idempotent per ``(fund_code, anchor_date, horizon)`` so a
        second check on the same NAV date keeps the first history reference.
        It never performs network IO.
        """
        code = _normalize_fund_code(getattr(profile, "code", ""))
        result = {"created": 0, "skipped": 0, "fund_code": code, "anchor_date": None}
        if not code or not profile.nav_history:
            return result

        latest = profile.nav_history[-1]
        anchor_date = _parse_date(latest.date)
        anchor_nav = float(latest.unit_nav or 0)
        if anchor_date is None or anchor_nav <= 0:
            return result

        result["anchor_date"] = anchor_date
        existing = set(
            self.repo.list_existing_horizons(
                fund_code=code,
                anchor_date=anchor_date,
                engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
            )
        )
        rows = self._build_anchor_rows(
            code=code,
            fund_name=profile.name or code,
            anchor_date=anchor_date,
            anchor_nav=anchor_nav,
            analysis_history_id=analysis_history_id,
            source=source,
            horizons=[h for h in FUND_NAV_REVIEW_HORIZON_ORDER if h not in existing],
            now=now,
        )
        result["created"] = self.repo.insert_reviews(rows)
        result["skipped"] = len(existing)
        return result

    @staticmethod
    def _build_anchor_rows(
        *,
        code: str,
        fund_name: str,
        anchor_date: date,
        anchor_nav: float,
        analysis_history_id: Optional[int],
        source: str,
        horizons: List[str],
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        created_at = now or utc_naive_now()
        return [
            {
                "fund_code": code,
                "fund_name": fund_name or code,
                "horizon": horizon,
                "engine_version": FUND_NAV_REVIEW_ENGINE_VERSION,
                "anchor_date": anchor_date,
                "anchor_nav": float(anchor_nav),
                "due_date": anchor_date + timedelta(days=FUND_NAV_REVIEW_DUE_DAYS[horizon]),
                "eval_status": "pending",
                "analysis_history_id": analysis_history_id,
                "anchor_source": source,
                "attempts": 0,
                "created_at": created_at,
            }
            for horizon in horizons
        ]

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def run_reviews(
        self,
        *,
        fund_code: Optional[str] = None,
        horizons: Optional[List[str]] = None,
        force: bool = False,
        limit: int = 100,
        as_of: Optional[date] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Evaluate due anchors, fetching each fund's NAV range once per run."""
        as_of = as_of or date.today()
        now = now or utc_naive_now()
        normalized_code = _normalize_fund_code(fund_code) if fund_code else None
        if fund_code and not normalized_code:
            raise ValueError("fund_code must be a 6-digit fund code")

        rows = self.repo.list_due_reviews(
            engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
            as_of=as_of,
            horizons=horizons,
            fund_code=normalized_code,
            force=force,
            checked_before=now - CHECK_COOLDOWN,
            limit=min(max(int(limit), 1), MAX_ROWS_PER_RUN),
        )

        counts = {"evaluated": 0, "pending": 0, "unable": 0, "skipped": 0}
        items: List[Dict[str, Any]] = []
        for code, fund_rows in self._group_rows_by_fund(rows):
            fund_rows = fund_rows[:MAX_ROWS_PER_RUN]
            try:
                navs = self.fetcher.get_nav_range(
                    code,
                    min(row.anchor_date for row in fund_rows),
                    as_of,
                )
            except Exception as exc:
                logger.warning("[%s] 基金净值区间抓取失败: %s", code, exc)
                for row in fund_rows:
                    self._update_row(
                        row,
                        {
                            "attempts": int(row.attempts or 0) + 1,
                            "checked_at": now,
                            "last_error": "fetch_error",
                        },
                    )
                    counts["skipped"] += 1
                continue

            for row in fund_rows:
                status = self._evaluate_row(
                    row,
                    navs,
                    as_of=as_of,
                    now=now,
                    force=force,
                )
                counts[status] = counts.get(status, 0) + 1
                items.append(self._to_item(row))

        return {
            "items": items,
            "evaluated": counts["evaluated"],
            "pending": counts["pending"],
            "unable": counts["unable"],
            "skipped": counts["skipped"],
            "engine_version": FUND_NAV_REVIEW_ENGINE_VERSION,
        }

    def _evaluate_row(
        self,
        row: FundNavReviewRecord,
        navs: List[NavRecord],
        *,
        as_of: date,
        now: datetime,
        force: bool,
    ) -> str:
        if row.anchor_nav is None or float(row.anchor_nav) <= 0:
            self._mark_unable(row, "invalid_anchor_nav", now)
            return "unable"

        base_fields: Dict[str, Any] = {
            "attempts": int(row.attempts or 0) + 1,
            "checked_at": now,
        }
        if not navs:
            if (as_of - row.anchor_date) > STALE_GRACE:
                self._mark_unable(row, "fund_nav_unavailable", now)
                return "unable"
            self._update_row(
                row,
                {**base_fields, "last_error": "nav_range_empty"},
            )
            return "pending"

        dates = [(record.date or "")[:10] for record in navs]
        anchor_key = row.anchor_date.isoformat()
        anchor_idx = bisect.bisect_left(dates, anchor_key)
        if anchor_idx >= len(navs) or dates[anchor_idx] != anchor_key:
            self._update_row(
                row,
                {**base_fields, "last_error": "anchor_not_found"},
            )
            return "pending"

        horizon_records = FUND_NAV_REVIEW_HORIZONS[row.horizon]
        target_idx = anchor_idx + horizon_records
        if target_idx >= len(navs):
            latest_date = _parse_date(navs[-1].date)
            if latest_date is not None and (as_of - latest_date) > STALE_GRACE:
                self._mark_unable(row, "fund_delisted_or_suspended", now)
                return "unable"
            self._update_row(
                row,
                {**base_fields, "last_error": "waiting_for_records"},
            )
            return "pending"

        eval_record = navs[target_idx]
        eval_nav = float(eval_record.unit_nav or 0)
        if eval_nav <= 0:
            self._mark_unable(row, "invalid_eval_nav", now)
            return "unable"

        anchor_nav = float(row.anchor_nav)
        nav_return_pct = round((eval_nav / anchor_nav - 1.0) * 100.0, 4)
        peak = anchor_nav
        mdd = 0.0
        for record in navs[anchor_idx:target_idx + 1]:
            value = float(record.unit_nav or 0)
            if value <= 0:
                continue
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)

        self._update_row(
            row,
            {
                **base_fields,
                "eval_status": "evaluated",
                "unable_reason": None,
                "last_error": None,
                "eval_date": _parse_date(eval_record.date),
                "eval_nav": eval_nav,
                "nav_return_pct": nav_return_pct,
                "max_drawdown_pct": round(mdd * 100.0, 4),
                "eval_window_records": horizon_records,
                "evaluated_at": now,
            },
        )
        return "evaluated"

    def _mark_unable(self, row: FundNavReviewRecord, reason: str, now: datetime) -> None:
        self._update_row(
            row,
            {
                "eval_status": "unable",
                "unable_reason": reason,
                "last_error": None,
                "attempts": int(row.attempts or 0) + 1,
                "checked_at": now,
            },
        )

    def _update_row(self, row: FundNavReviewRecord, fields: Dict[str, Any]) -> None:
        """Persist fields and mirror them onto the in-memory row.

        ``run_reviews`` serializes the same ORM instances it just evaluated; a
        separate session update would otherwise leave the response stale.
        """
        for key, value in fields.items():
            setattr(row, key, value)
        self.repo.update_review(review_id=row.id, fields=fields)

    @staticmethod
    def _group_rows_by_fund(rows: List[FundNavReviewRecord]):
        groups: Dict[str, List[FundNavReviewRecord]] = defaultdict(list)
        for row in rows:
            groups[row.fund_code].append(row)
        for code in list(groups)[:MAX_FUNDS_PER_RUN]:
            yield code, groups[code]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_reviews(
        self,
        *,
        fund_code: Optional[str] = None,
        horizon: Optional[str] = None,
        eval_status: Optional[str] = None,
        anchor_from: Optional[date] = None,
        anchor_to: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_code = _normalize_fund_code(fund_code) if fund_code else None
        if fund_code and not normalized_code:
            raise ValueError("fund_code must be a 6-digit fund code")
        rows, total = self.repo.list_reviews(
            engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
            fund_code=normalized_code,
            horizon=horizon,
            eval_status=eval_status,
            anchor_from=anchor_from,
            anchor_to=anchor_to,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._to_item(row) for row in rows],
            "total": total,
            "page": max(1, int(page)),
            "page_size": max(1, min(int(page_size), 100)),
        }

    def get_stats(self, *, horizons: Optional[List[str]] = None) -> Dict[str, Any]:
        rows = self.repo.list_stats_rows(
            engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
            horizons=horizons,
        )
        horizon_buckets = []
        for horizon in FUND_NAV_REVIEW_HORIZON_ORDER:
            bucket_rows = [row for row in rows if row.horizon == horizon]
            if bucket_rows:
                horizon_buckets.append(self._aggregate_bucket("horizon", horizon, bucket_rows))

        month_groups: Dict[str, List[FundNavReviewRecord]] = defaultdict(list)
        for row in rows:
            month_groups[row.anchor_date.strftime("%Y-%m")].append(row)
        month_buckets = [
            self._aggregate_bucket("month", month, month_groups[month])
            for month in sorted(month_groups)
        ]

        return {
            "engine_version": FUND_NAV_REVIEW_ENGINE_VERSION,
            "horizons": [bucket["value"] for bucket in horizon_buckets],
            **self._aggregate(rows),
            "breakdowns": {"horizon": horizon_buckets, "month": month_buckets},
        }

    def _aggregate(self, rows: List[FundNavReviewRecord]) -> Dict[str, Any]:
        evaluated = [row for row in rows if row.eval_status == "evaluated"]
        returns = [row.nav_return_pct for row in evaluated if row.nav_return_pct is not None]
        return {
            "total": len(rows),
            "anchor_count": len({(row.fund_code, row.anchor_date) for row in rows}),
            "pending": sum(1 for row in rows if row.eval_status == "pending"),
            "evaluated": len(evaluated),
            "unable": sum(1 for row in rows if row.eval_status == "unable"),
            "positive_rate_pct": (
                round(sum(1 for value in returns if value > 0) / len(returns) * 100.0, 4)
                if returns else None
            ),
            "avg_nav_return_pct": _mean(returns),
            "avg_max_drawdown_pct": _mean(
                row.max_drawdown_pct for row in evaluated if row.max_drawdown_pct is not None
            ),
            "unable_reasons": dict(
                Counter(row.unable_reason for row in rows if row.eval_status == "unable")
            ),
        }

    def _aggregate_bucket(
        self,
        dimension: str,
        value: str,
        rows: List[FundNavReviewRecord],
    ) -> Dict[str, Any]:
        return {"dimension": dimension, "value": value, **self._aggregate(rows)}

    @staticmethod
    def _to_item(row: FundNavReviewRecord) -> Dict[str, Any]:
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    # ------------------------------------------------------------------
    # Historical backfill
    # ------------------------------------------------------------------
    def backfill_from_history(
        self,
        *,
        fund_code: Optional[str] = None,
        since: Optional[date] = None,
        until: Optional[date] = None,
        dry_run: bool = True,
        resolve_missing: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Create anchors from existing fund analysis history.

        New reports carry ``dashboard.latest_nav_date`` and backfill offline.
        Legacy reports only have ``latest_nav`` (no date); they are matched
        against a fetched NAV range only when ``resolve_missing=True``.
        """
        normalized_code = _normalize_fund_code(fund_code) if fund_code else None
        if fund_code and not normalized_code:
            raise ValueError("fund_code must be a 6-digit fund code")

        histories = self.repo.list_fund_analysis_histories(
            fund_code=normalized_code,
            since=since,
            until=until,
            limit=limit,
        )
        groups: Dict[str, List[Any]] = defaultdict(list)
        for history in histories:
            code = _normalize_fund_code(history.code)
            if code:
                groups[code].append(history)

        result = {
            "scanned": len(histories),
            "created": 0,
            "skipped": 0,
            "unmatched": 0,
            "funds": 0,
            "dry_run": bool(dry_run),
            "engine_version": FUND_NAV_REVIEW_ENGINE_VERSION,
        }
        for code, fund_histories in list(groups.items())[:MAX_BACKFILL_FUNDS]:
            result["funds"] += 1
            counts = self._backfill_fund(code, fund_histories, dry_run, resolve_missing)
            for key in ("created", "skipped", "unmatched"):
                result[key] += counts[key]
        return result

    def _backfill_fund(
        self,
        code: str,
        histories: List[Any],
        dry_run: bool,
        resolve_missing: bool,
    ) -> Dict[str, int]:
        counts = {"created": 0, "skipped": 0, "unmatched": 0}
        navs: List[NavRecord] = []
        if resolve_missing:
            created_dates = [history.created_at.date() for history in histories if history.created_at]
            if created_dates:
                try:
                    navs = self.fetcher.get_nav_range(
                        code,
                        min(created_dates) - timedelta(days=14),
                        max(created_dates) + timedelta(days=2),
                    )
                except Exception as exc:
                    logger.warning("[%s] 基金历史净值抓取失败: %s", code, exc)
                    navs = []

        seen_dates = set()
        for history in histories:
            anchor = self._history_anchor(history, navs)
            if anchor is None:
                counts["unmatched"] += 1
                continue
            anchor_date, anchor_nav = anchor
            if (code, anchor_date) in seen_dates:
                continue
            seen_dates.add((code, anchor_date))
            existing = set(
                self.repo.list_existing_horizons(
                    fund_code=code,
                    anchor_date=anchor_date,
                    engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
                )
            )
            missing = [h for h in FUND_NAV_REVIEW_HORIZON_ORDER if h not in existing]
            counts["skipped"] += len(existing)
            if not missing:
                continue
            rows = self._build_anchor_rows(
                code=code,
                fund_name=history.name or code,
                anchor_date=anchor_date,
                anchor_nav=anchor_nav,
                analysis_history_id=history.id,
                source="backfill",
                horizons=missing,
            )
            if dry_run:
                counts["created"] += len(rows)
            else:
                counts["created"] += self.repo.insert_reviews(rows)
        return counts

    @staticmethod
    def _history_anchor(history: Any, navs: List[NavRecord]):
        try:
            payload = json.loads(history.raw_result or "{}")
        except (TypeError, ValueError):
            return None
        dashboard = payload.get("dashboard") if isinstance(payload, dict) else None
        if not isinstance(dashboard, dict):
            return None
        latest_nav = dashboard.get("latest_nav")
        try:
            latest_nav = float(latest_nav)
        except (TypeError, ValueError):
            return None
        if latest_nav <= 0:
            return None

        anchor_date = _parse_date(dashboard.get("latest_nav_date"))
        if anchor_date is not None:
            return anchor_date, latest_nav

        if not navs or history.created_at is None:
            return None
        created_date = history.created_at.date()
        candidates = [
            record
            for record in navs
            if _parse_date(record.date) is not None
            and _parse_date(record.date) <= created_date
            and abs(float(record.unit_nav) - latest_nav) <= BACKFILL_NAV_TOLERANCE
        ]
        if not candidates:
            return None
        matched = max(candidates, key=lambda record: record.date)
        matched_date = _parse_date(matched.date)
        if matched_date is None:
            return None
        return matched_date, float(matched.unit_nav)

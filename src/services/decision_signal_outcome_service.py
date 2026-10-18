# -*- coding: utf-8 -*-
"""DecisionSignal feedback, forward outcome, and stats service."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.core.backtest_engine import BacktestEngine, EvaluationConfig
from src.repositories.decision_signal_outcome_repo import (
    DecisionSignalOutcomeRepository,
    OutcomeStatsRow,
)
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.stock_repo import StockRepository
from src.schemas.decision_profile import VALID_DECISION_PROFILES
from src.services.decision_signal_benchmark_service import (
    BENCHMARK_STATUS_AVAILABLE,
    BENCHMARK_STATUS_ERROR,
    BENCHMARK_STATUS_MISSING,
    BENCHMARK_STATUS_UNSUPPORTED,
    BenchmarkFetcher,
    benchmark_code_for_market,
    compute_benchmark_return,
    ensure_benchmark_bars,
)
from src.services.decision_signal_data_quality import normalize_decision_signal_data_quality
from src.services.decision_signal_service import (
    HORIZONS,
    SIGNAL_STATUSES,
    SOURCE_TYPES,
    DecisionSignalNotFoundError,
    DecisionSignalService,
)
from src.storage import (
    DatabaseManager,
    DecisionSignalFeedbackRecord,
    DecisionSignalOutcomeRecord,
    DecisionSignalRecord,
    utc_naive_now,
)
from src.utils.sanitize import sanitize_decision_signal_text


logger = logging.getLogger(__name__)

DECISION_SIGNAL_OUTCOME_ENGINE_VERSION = "decision-signal-v1"
SUPPORTED_OUTCOME_HORIZONS = {
    "1d": 1,
    "3d": 3,
    "5d": 5,
    "10d": 10,
}
DEFAULT_STATS_STATUSES = ("active", "expired", "invalidated", "closed")
OUTCOME_VALUES = frozenset({"hit", "miss", "neutral"})
EVAL_STATUSES = frozenset({"completed", "unable"})
FEEDBACK_VALUES = frozenset({"useful", "not_useful"})
FEEDBACK_SOURCES = frozenset({"web", "api"})
HOLDING_STATES = frozenset({"holding", "empty", "unknown"})
RETRYABLE_UNABLE_REASONS = frozenset({
    "missing_anchor_price",
    "invalid_anchor_price",
    "insufficient_forward_bars",
    "missing_end_close",
    "invalid_end_close",
})
BATCH_CANDIDATE_SCAN_PAGE_SIZE = 500
MIN_PROFILE_CALIBRATION_SAMPLE_SIZE = 30
BENCHMARK_BACKFILL_BATCH_SIZE = 200
BENCHMARK_BACKFILL_COOLDOWN = timedelta(hours=6)
STATS_BREAKDOWN_DIMENSIONS = (
    "action",
    "market",
    "month",
    "market_phase",
    "source_type",
    "source_agent",
    "plan_quality",
    "data_quality_level",
    "holding_state",
)
PROFILE_SOURCES = frozenset({
    "auto_default",
    "backfill_defaulted",
    "legacy_unknown",
    "user_selected",
})
PROFILE_CALIBRATION_BREAKDOWN_DIMENSIONS = (
    ("decision_profile", ("decision_profile",)),
    ("decision_profile_action", ("decision_profile", "action")),
    ("decision_profile_horizon", ("decision_profile", "horizon")),
    ("decision_profile_market_phase", ("decision_profile", "market_phase")),
    (
        "decision_profile_data_quality_level",
        ("decision_profile", "data_quality_level"),
    ),
    ("profile_source", ("profile_source",)),
)


class DecisionSignalOutcomeService:
    """Business logic for signal outcomes, stats, and feedback."""

    def __init__(
        self,
        *,
        repo: Optional[DecisionSignalOutcomeRepository] = None,
        signal_repo: Optional[DecisionSignalRepository] = None,
        stock_repo: Optional[StockRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
        fetch_missing_benchmark: bool = False,
        benchmark_fetcher: Optional[BenchmarkFetcher] = None,
    ):
        self.repo = repo or DecisionSignalOutcomeRepository(db_manager)
        self.signal_repo = signal_repo or DecisionSignalRepository(db_manager)
        self.stock_repo = stock_repo or StockRepository(db_manager)
        self.db_manager = db_manager or self.repo.db
        self.fetch_missing_benchmark = bool(fetch_missing_benchmark)
        self.benchmark_fetcher = benchmark_fetcher

    def run_outcomes(
        self,
        *,
        signal_id: Optional[int] = None,
        horizons: Optional[List[str]] = None,
        force: bool = False,
        market: Optional[str] = None,
        stock_code: Optional[str] = None,
        action: Optional[str] = None,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        fetch_missing_benchmark: Optional[bool] = None,
        exclude_signal_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        signal_id_norm = self._optional_positive_int(signal_id, "signal_id")
        market_norm = DecisionSignalService._normalize_optional_market(market)
        action_norm = DecisionSignalService._normalize_optional_action(action)
        source_type_norm = self._normalize_optional_enum(source_type, SOURCE_TYPES, "source_type")
        status_norm = self._normalize_optional_enum(status, SIGNAL_STATUSES, "status")
        stock_codes_norm = DecisionSignalService._stock_filter_codes(stock_code, market=market_norm)
        horizons_norm = self._normalize_horizons(horizons)
        safe_limit = max(1, min(int(limit), 500))

        statuses = [status_norm] if status_norm else None
        if signal_id_norm is None and statuses is None:
            statuses = list(DEFAULT_STATS_STATUSES)

        if signal_id_norm is None and not force:
            signals = self._list_actionable_candidate_signals(
                stock_codes=stock_codes_norm,
                market=market_norm,
                action=action_norm,
                source_type=source_type_norm,
                statuses=statuses,
                requested_horizons=horizons_norm,
                limit=safe_limit,
                exclude_signal_ids=exclude_signal_ids,
            )
        else:
            signals = self.repo.list_candidate_signals(
                signal_id=signal_id_norm,
                stock_codes=stock_codes_norm,
                market=market_norm,
                action=action_norm,
                source_type=source_type_norm,
                statuses=statuses,
                exclude_ids=exclude_signal_ids,
                limit=safe_limit,
            )
        if signal_id_norm is not None and not signals:
            raise DecisionSignalNotFoundError(f"Decision signal not found: {signal_id_norm}")

        items: List[Dict[str, Any]] = []
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for signal in signals:
            for horizon in self._horizons_for_signal(signal, horizons_norm):
                existing = self.repo.get_outcome(
                    signal_id=signal.id,
                    horizon=horizon,
                    engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
                )
                if existing is not None and not force and not self._should_recompute_outcome(existing):
                    skipped_count += 1
                    items.append(
                        self._serialize_outcome(
                            existing,
                            stock_code=signal.stock_code,
                            stock_name=signal.stock_name,
                        )
                    )
                    continue

                fields = self._evaluate_signal_horizon(signal, horizon)
                row, created = self.repo.upsert_outcome(fields)
                if created:
                    created_count += 1
                else:
                    updated_count += 1
                items.append(
                    self._serialize_outcome(
                        row,
                        stock_code=signal.stock_code,
                        stock_name=signal.stock_name,
                    )
                )

        should_backfill_benchmarks = (
            self.fetch_missing_benchmark
            if fetch_missing_benchmark is None
            else bool(fetch_missing_benchmark)
        )
        if should_backfill_benchmarks:
            self._backfill_benchmarks()

        return {
            "items": items,
            "evaluated": created_count + updated_count,
            "created": created_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "engine_version": DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            # Signals selected by this call, including ones whose outcomes were
            # skipped or whose evaluation remained unable. Backlog drainers can
            # pass these back through ``exclude_signal_ids`` so retryable rows
            # are not selected again in the same run.
            "processed_signal_ids": [int(signal.id) for signal in signals],
        }

    def _backfill_benchmarks(self) -> None:
        """Fill benchmark components for completed outcomes, outside candidate scan.

        Historical completed rows are not made retryable in
        `_should_recompute_outcome`: doing so would crowd recoverable unable
        rows out of the candidate scan and rewrite `updated_at`.
        """
        now = utc_naive_now()
        checked_before = now - BENCHMARK_BACKFILL_COOLDOWN
        ledger_rows = self.repo.list_outcomes_needing_benchmark(
            engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            checked_before=checked_before,
            limit=BENCHMARK_BACKFILL_BATCH_SIZE,
        )
        attempted: set = set()
        for ledger in ledger_rows:
            outcome = ledger.outcome
            benchmark_code = benchmark_code_for_market(outcome.market)
            if benchmark_code is None:
                self.repo.update_benchmark_fields(
                    outcome_id=outcome.id,
                    fields={
                        "benchmark_code": None,
                        "benchmark_return_pct": None,
                        "excess_return_pct": None,
                        "benchmark_status": BENCHMARK_STATUS_UNSUPPORTED,
                        "benchmark_checked_at": now,
                    },
                )
                continue

            window_end_date = self._stock_window_end_date(ledger, outcome)
            if window_end_date is None:
                self.repo.update_benchmark_fields(
                    outcome_id=outcome.id,
                    fields={
                        "benchmark_code": benchmark_code,
                        "benchmark_return_pct": None,
                        "excess_return_pct": None,
                        "benchmark_status": BENCHMARK_STATUS_MISSING,
                        "benchmark_checked_at": now,
                    },
                )
                continue

            key = (benchmark_code, outcome.anchor_date, window_end_date)
            fetch_failed = False
            if key not in attempted:
                attempted.add(key)
                try:
                    fetch_failed = not ensure_benchmark_bars(
                        db_manager=self.db_manager,
                        benchmark_code=benchmark_code,
                        anchor_date=outcome.anchor_date,
                        window_end_date=window_end_date,
                        fetcher=self.benchmark_fetcher,
                    )
                except Exception as exc:
                    logger.warning("基准日线补全异常(%s): %s", benchmark_code, exc)
                    fetch_failed = True

            try:
                benchmark = compute_benchmark_return(
                    stock_repo=self.stock_repo,
                    benchmark_code=benchmark_code,
                    anchor_date=outcome.anchor_date,
                    window_end_date=window_end_date,
                )
            except Exception as exc:
                logger.warning("基准收益计算失败(%s): %s", benchmark_code, exc)
                benchmark = {
                    "benchmark_status": BENCHMARK_STATUS_ERROR,
                    "benchmark_return_pct": None,
                }

            status = benchmark["benchmark_status"]
            if status != BENCHMARK_STATUS_AVAILABLE and fetch_failed:
                status = BENCHMARK_STATUS_ERROR
            benchmark_return_pct = benchmark["benchmark_return_pct"]
            stock_value = self._finite_float(outcome.stock_return_pct)
            excess_return_pct = (
                stock_value - float(benchmark_return_pct)
                if benchmark_return_pct is not None and stock_value is not None
                else None
            )
            self.repo.update_benchmark_fields(
                outcome_id=outcome.id,
                fields={
                    "benchmark_code": benchmark_code,
                    "benchmark_return_pct": benchmark_return_pct,
                    "excess_return_pct": excess_return_pct,
                    "benchmark_status": status,
                    "benchmark_checked_at": now,
                },
            )

    def _stock_window_end_date(self, ledger: Any, outcome: DecisionSignalOutcomeRecord) -> Optional[date]:
        stock_code = getattr(ledger, "stock_code", None)
        if not stock_code or outcome.anchor_date is None:
            return None
        eval_days = outcome.eval_window_days
        if not eval_days:
            return None
        try:
            forward_bars = self.stock_repo.get_forward_bars(
                code=stock_code,
                analysis_date=outcome.anchor_date,
                eval_window_days=int(eval_days),
            )
        except Exception as exc:
            logger.warning("读取股票窗口结束日失败(%s): %s", stock_code, exc)
            return None
        if len(forward_bars) < int(eval_days):
            return None
        return forward_bars[int(eval_days) - 1].date

    def _list_actionable_candidate_signals(
        self,
        *,
        stock_codes: Optional[List[str]],
        market: Optional[str],
        action: Optional[str],
        source_type: Optional[str],
        statuses: Optional[List[str]],
        requested_horizons: Optional[List[str]],
        limit: int,
        exclude_signal_ids: Optional[List[int]] = None,
    ) -> List[DecisionSignalRecord]:
        selected: List[DecisionSignalRecord] = []
        selected_ids = set()
        retryable_reserve: List[Tuple[datetime, int, DecisionSignalRecord]] = []
        retryable_ids = set()
        offset = 0

        while len(selected) < limit:
            page = self.repo.list_candidate_signals(
                stock_codes=stock_codes,
                market=market,
                action=action,
                source_type=source_type,
                statuses=statuses,
                exclude_ids=exclude_signal_ids,
                offset=offset,
                limit=BATCH_CANDIDATE_SCAN_PAGE_SIZE,
            )
            if not page:
                break

            outcomes = self.repo.list_outcomes_for_signals(
                signal_ids=[int(signal.id) for signal in page],
                engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            )
            outcomes_by_key: Dict[Tuple[int, str], DecisionSignalOutcomeRecord] = {
                (int(row.signal_id), row.horizon): row
                for row in outcomes
            }

            for signal in page:
                actionability, retryable_at = self._candidate_actionability(
                    signal,
                    requested_horizons=requested_horizons,
                    outcomes_by_key=outcomes_by_key,
                )
                signal_id = int(signal.id)
                if actionability == "missing":
                    if signal_id not in selected_ids:
                        selected.append(signal)
                        selected_ids.add(signal_id)
                    if len(selected) >= limit:
                        break
                elif actionability == "retryable" and signal_id not in retryable_ids:
                    retryable_reserve.append((retryable_at, signal_id, signal))
                    retryable_ids.add(signal_id)

            offset += len(page)
            if len(page) < BATCH_CANDIDATE_SCAN_PAGE_SIZE:
                break

        if len(selected) < limit:
            retryable_reserve.sort(key=lambda item: (item[0], item[1]))
            for _retryable_at, signal_id, signal in retryable_reserve:
                signal_id = int(signal.id)
                if signal_id in selected_ids:
                    continue
                selected.append(signal)
                selected_ids.add(signal_id)
                if len(selected) >= limit:
                    break

        return selected

    def _candidate_actionability(
        self,
        signal: DecisionSignalRecord,
        *,
        requested_horizons: Optional[List[str]],
        outcomes_by_key: Dict[Tuple[int, str], DecisionSignalOutcomeRecord],
    ) -> Tuple[Optional[str], Optional[datetime]]:
        retryable_times: List[datetime] = []
        signal_id = int(signal.id)
        for horizon in self._horizons_for_signal(signal, requested_horizons):
            existing = outcomes_by_key.get((signal_id, horizon))
            if existing is None:
                return "missing", None
            if self._should_recompute_outcome(existing):
                retryable_times.append(self._outcome_retryable_sort_time(existing))
        if retryable_times:
            return "retryable", min(retryable_times)
        return None, None

    @staticmethod
    def _outcome_retryable_sort_time(row: DecisionSignalOutcomeRecord) -> datetime:
        return row.updated_at or row.created_at or datetime.min

    @staticmethod
    def _should_recompute_outcome(row: DecisionSignalOutcomeRecord) -> bool:
        return row.eval_status == "unable" and row.unable_reason in RETRYABLE_UNABLE_REASONS

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
        anchor_from: Optional[Any] = None,
        anchor_to: Optional[Any] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        signal_id_norm = self._optional_positive_int(signal_id, "signal_id")
        horizon_norm = self._normalize_optional_enum(horizon, HORIZONS, "horizon")
        engine_version_norm = str(engine_version or DECISION_SIGNAL_OUTCOME_ENGINE_VERSION).strip()
        eval_status_norm = self._normalize_optional_enum(eval_status, EVAL_STATUSES, "eval_status")
        outcome_norm = self._normalize_optional_enum(outcome, OUTCOME_VALUES, "outcome")
        market_norm = DecisionSignalService._normalize_optional_market(market)
        action_norm = DecisionSignalService._normalize_optional_action(action)
        anchor_from_norm = self._optional_date(anchor_from, "anchor_from")
        anchor_to_norm = self._optional_date(anchor_to, "anchor_to")
        if anchor_from_norm and anchor_to_norm and anchor_from_norm > anchor_to_norm:
            raise ValueError("anchor_from must be on or before anchor_to")
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        rows, total = self.repo.list_outcomes(
            signal_id=signal_id_norm,
            horizon=horizon_norm,
            engine_version=engine_version_norm,
            eval_status=eval_status_norm,
            outcome=outcome_norm,
            market=market_norm,
            action=action_norm,
            anchor_from=anchor_from_norm,
            anchor_to=anchor_to_norm,
            page=safe_page,
            page_size=safe_page_size,
        )
        return {
            "items": [
                self._serialize_outcome(
                    ledger.outcome,
                    stock_code=ledger.stock_code,
                    stock_name=ledger.stock_name,
                )
                for ledger in rows
            ],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        }

    @staticmethod
    def _optional_date(value: Any, field: str) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc

    def list_signal_outcomes(self, signal_id: int) -> Dict[str, Any]:
        signal_id_norm = self._require_existing_signal(signal_id).id
        return self.list_outcomes(
            signal_id=signal_id_norm,
            engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            page=1,
            page_size=100,
        )

    def get_stats(
        self,
        *,
        horizons: Optional[List[str]] = None,
        engine_version: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        engine_version_norm = str(engine_version or DECISION_SIGNAL_OUTCOME_ENGINE_VERSION).strip()
        horizons_norm = self._normalize_horizons(horizons)
        statuses_norm = (
            [self._normalize_enum(item, SIGNAL_STATUSES, "status") for item in statuses]
            if statuses
            else list(DEFAULT_STATS_STATUSES)
        )
        stats_rows = self.repo.list_stats_rows(
            engine_version=engine_version_norm,
            horizons=horizons_norm,
            statuses=statuses_norm,
        )
        rows = [stats_row.outcome for stats_row in stats_rows]
        breakdowns = {
            dimension: self._breakdown(rows, dimension)
            for dimension in STATS_BREAKDOWN_DIMENSIONS
        }
        return {
            **self._aggregate(rows),
            "engine_version": engine_version_norm,
            "horizons": horizons_norm,
            "statuses": statuses_norm,
            "breakdowns": breakdowns,
            "profile_calibration": self._profile_calibration(stats_rows),
        }

    def get_feedback(self, signal_id: int) -> Dict[str, Any]:
        signal = self._require_existing_signal(signal_id)
        row = self.repo.get_feedback(signal_id=signal.id)
        if row is None:
            return {
                "signal_id": signal.id,
                "feedback_value": None,
                "reason_code": None,
                "note": None,
                "source": None,
                "created_at": None,
                "updated_at": None,
            }
        return self._serialize_feedback(row)

    def put_feedback(
        self,
        signal_id: int,
        *,
        feedback_value: str,
        reason_code: Optional[str] = None,
        note: Optional[str] = None,
        source: str = "api",
    ) -> Dict[str, Any]:
        signal = self._require_existing_signal(signal_id)
        fields = {
            "signal_id": signal.id,
            "feedback_value": self._normalize_enum(feedback_value, FEEDBACK_VALUES, "feedback_value"),
            "reason_code": self._optional_public_text(reason_code, "reason_code", max_length=64),
            "note": self._optional_public_text(note, "note", max_length=1000),
            "source": self._normalize_enum(source or "api", FEEDBACK_SOURCES, "source"),
        }
        row = self.repo.upsert_feedback(fields)
        return self._serialize_feedback(row)

    def _evaluate_signal_horizon(self, signal: DecisionSignalRecord, horizon: str) -> Dict[str, Any]:
        base = self._snapshot_fields(signal, horizon)
        direction = self._direction_for_action(signal.action)
        if direction is None:
            return self._unable_fields(base, reason="non_directional_action")

        eval_days = SUPPORTED_OUTCOME_HORIZONS.get(horizon)
        if eval_days is None:
            return self._unable_fields(base, reason="unsupported_horizon", direction_expected=direction)

        anchor_date = self._anchor_date(signal)
        if anchor_date is None:
            return self._unable_fields(base, reason="missing_anchor_date", direction_expected=direction)

        start_bar = self.stock_repo.get_daily_on_date(code=signal.stock_code, target_date=anchor_date)
        start_price = getattr(start_bar, "close", None)
        if start_price is None:
            return self._unable_fields(
                base,
                reason="missing_anchor_price",
                direction_expected=direction,
                anchor_date=anchor_date,
                eval_window_days=eval_days,
            )
        if not self._is_positive_finite(start_price):
            return self._unable_fields(
                base,
                reason="invalid_anchor_price",
                direction_expected=direction,
                anchor_date=anchor_date,
                eval_window_days=eval_days,
                start_price=start_price,
            )

        forward_bars = self.stock_repo.get_forward_bars(
            code=signal.stock_code,
            analysis_date=anchor_date,
            eval_window_days=eval_days,
        )
        evaluation = BacktestEngine.evaluate_decision_signal(
            direction_expected=direction,
            anchor_date=anchor_date,
            start_price=float(start_price),
            forward_bars=forward_bars,
            config=EvaluationConfig(
                eval_window_days=eval_days,
                neutral_band_pct=2.0,
                engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            ),
        )
        fields = {
            **base,
            "eval_status": evaluation.get("eval_status"),
            "outcome": evaluation.get("outcome"),
            "direction_expected": direction,
            "direction_correct": evaluation.get("direction_correct"),
            "unable_reason": evaluation.get("unable_reason"),
            "anchor_date": anchor_date,
            "eval_window_days": eval_days,
            "start_price": evaluation.get("start_price", start_price),
            "end_close": evaluation.get("end_close"),
            "max_high": evaluation.get("max_high"),
            "min_low": evaluation.get("min_low"),
            "stock_return_pct": evaluation.get("stock_return_pct"),
        }
        if evaluation.get("eval_status") == "completed":
            fields.update(
                self._local_benchmark_fields(
                    market=signal.market,
                    anchor_date=anchor_date,
                    forward_bars=forward_bars,
                    eval_days=eval_days,
                    stock_return_pct=evaluation.get("stock_return_pct"),
                )
            )
        return fields

    def _local_benchmark_fields(
        self,
        *,
        market: Optional[str],
        anchor_date: date,
        forward_bars: List[Any],
        eval_days: int,
        stock_return_pct: Optional[float],
    ) -> Dict[str, Any]:
        """Compute benchmark fields from local bars only; never fetches here."""
        benchmark_code = benchmark_code_for_market(market)
        if benchmark_code is None:
            return {
                "benchmark_code": None,
                "benchmark_return_pct": None,
                "excess_return_pct": None,
                "benchmark_status": BENCHMARK_STATUS_UNSUPPORTED,
                "benchmark_checked_at": utc_naive_now(),
            }

        if len(forward_bars) < eval_days:
            return {
                "benchmark_code": benchmark_code,
                "benchmark_return_pct": None,
                "excess_return_pct": None,
                "benchmark_status": BENCHMARK_STATUS_MISSING,
                "benchmark_checked_at": None,
            }

        window_end_date = forward_bars[eval_days - 1].date
        try:
            benchmark = compute_benchmark_return(
                stock_repo=self.stock_repo,
                benchmark_code=benchmark_code,
                anchor_date=anchor_date,
                window_end_date=window_end_date,
            )
        except Exception as exc:
            logger.warning("基准收益计算失败(%s): %s", benchmark_code, exc)
            return {
                "benchmark_code": benchmark_code,
                "benchmark_return_pct": None,
                "excess_return_pct": None,
                "benchmark_status": BENCHMARK_STATUS_ERROR,
                "benchmark_checked_at": utc_naive_now(),
            }

        benchmark_return_pct = benchmark["benchmark_return_pct"]
        excess_return_pct = None
        stock_value = self._finite_float(stock_return_pct)
        if benchmark_return_pct is not None and stock_value is not None:
            excess_return_pct = stock_value - float(benchmark_return_pct)
        return {
            "benchmark_code": benchmark_code,
            "benchmark_return_pct": benchmark_return_pct,
            "excess_return_pct": excess_return_pct,
            "benchmark_status": benchmark["benchmark_status"],
            # A local miss stays NULL so the same run's backfill can try the
            # network immediately; failures recorded by backfill get cooldown.
            "benchmark_checked_at": (
                utc_naive_now()
                if benchmark["benchmark_status"] == BENCHMARK_STATUS_AVAILABLE
                else None
            ),
        }

    @staticmethod
    def _direction_for_action(action: Optional[str]) -> Optional[str]:
        if action in {"buy", "add"}:
            return "up"
        if action == "hold":
            return "not_down"
        if action in {"reduce", "sell", "avoid"}:
            return "not_up"
        return None

    def _snapshot_fields(self, signal: DecisionSignalRecord, horizon: str) -> Dict[str, Any]:
        data_quality_level = self._data_quality_level(signal)
        holding_state = self._holding_state(signal)
        return {
            "signal_id": signal.id,
            "horizon": horizon,
            "engine_version": DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            "action": signal.action,
            "market": signal.market,
            "market_phase": signal.market_phase,
            "source_type": signal.source_type,
            "source_agent": signal.source_agent,
            "plan_quality": signal.plan_quality,
            "data_quality_level": data_quality_level,
            "holding_state": holding_state,
        }

    @staticmethod
    def _unable_fields(
        base: Dict[str, Any],
        *,
        reason: str,
        direction_expected: Optional[str] = None,
        anchor_date: Optional[date] = None,
        eval_window_days: Optional[int] = None,
        start_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        return {
            **base,
            "eval_status": "unable",
            "outcome": None,
            "direction_expected": direction_expected,
            "direction_correct": None,
            "unable_reason": reason,
            "anchor_date": anchor_date,
            "eval_window_days": eval_window_days,
            "start_price": start_price,
            "end_close": None,
            "max_high": None,
            "min_low": None,
            "stock_return_pct": None,
        }

    def _anchor_date(self, signal: DecisionSignalRecord) -> Optional[date]:
        metadata = self._json_loads(signal.metadata_json)
        if isinstance(metadata, dict):
            summary = metadata.get("market_phase_summary")
            if isinstance(summary, dict):
                parsed = self._parse_date(summary.get("session_date"))
                if parsed is not None:
                    return parsed
        return self._parse_date(signal.created_at)

    def _data_quality_level(self, signal: DecisionSignalRecord) -> str:
        raw_summary = signal.data_quality_summary_json
        if raw_summary and raw_summary.strip():
            try:
                summary = json.loads(raw_summary)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid decision signal sidecar source JSON: %s", exc)
                return "unknown"
            explicit_level = self._explicit_data_quality_level(summary)
            if explicit_level is not None:
                return self._short_label(explicit_level)
        metadata = self._json_loads(signal.metadata_json)
        if isinstance(metadata, dict):
            return normalize_decision_signal_data_quality(metadata.get("data_quality_level"))
        return "unknown"

    @staticmethod
    def _explicit_data_quality_level(value: Any) -> Optional[Any]:
        if isinstance(value, dict):
            for key in ("level", "quality_level"):
                level = value.get(key)
                if level not in (None, ""):
                    return level
            nested = value.get("data_quality")
            if isinstance(nested, dict) and nested.get("level") not in (None, ""):
                return nested.get("level")
            return None
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _holding_state(self, signal: DecisionSignalRecord) -> str:
        metadata = self._json_loads(signal.metadata_json)
        if isinstance(metadata, dict):
            value = str(metadata.get("holding_state") or "").strip().lower()
            if value in HOLDING_STATES:
                return value
        return "unknown"

    @staticmethod
    def _short_label(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text[:24] or "unknown"

    @staticmethod
    def _json_loads(value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid decision signal sidecar source JSON: %s", exc)
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _is_positive_finite(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0

    def _horizons_for_signal(self, signal: DecisionSignalRecord, requested: Optional[List[str]]) -> List[str]:
        if requested:
            return requested
        horizon = str(signal.horizon or "").strip()
        if horizon:
            return [horizon]
        return list(SUPPORTED_OUTCOME_HORIZONS.keys())

    def _require_existing_signal(self, signal_id: int) -> DecisionSignalRecord:
        signal_id_norm = self._optional_positive_int(signal_id, "signal_id")
        row = self.signal_repo.get(signal_id_norm)
        if row is None:
            raise DecisionSignalNotFoundError(f"Decision signal not found: {signal_id_norm}")
        return row

    @staticmethod
    def _optional_positive_int(value: Any, field_name: str) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer") from exc
        if number <= 0:
            raise ValueError(f"{field_name} must be positive")
        return number

    @staticmethod
    def _normalize_enum(value: Any, allowed: Iterable[str], field_name: str) -> str:
        text = str(value or "").strip()
        allowed_set = set(allowed)
        if text not in allowed_set:
            allowed_text = ", ".join(sorted(allowed_set))
            raise ValueError(f"{field_name} must be one of {allowed_text}")
        return text

    @classmethod
    def _normalize_optional_enum(cls, value: Any, allowed: Iterable[str], field_name: str) -> Optional[str]:
        if value in (None, ""):
            return None
        return cls._normalize_enum(value, allowed, field_name)

    def _normalize_horizons(self, values: Optional[List[str]]) -> Optional[List[str]]:
        if not values:
            return None
        out: List[str] = []
        for value in values:
            horizon = self._normalize_enum(value, HORIZONS, "horizon")
            if horizon not in out:
                out.append(horizon)
        return out

    @staticmethod
    def _optional_public_text(value: Any, field_name: str, *, max_length: int) -> Optional[str]:
        if value in (None, ""):
            return None
        text = sanitize_decision_signal_text(value)
        if not text:
            return None
        if len(text) > max_length:
            raise ValueError(f"{field_name} must be at most {max_length} characters")
        return text

    @staticmethod
    def _serialize_outcome(
        row: DecisionSignalOutcomeRecord,
        *,
        stock_code: Optional[str] = None,
        stock_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "id": row.id,
            "signal_id": row.signal_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "horizon": row.horizon,
            "engine_version": row.engine_version,
            "eval_status": row.eval_status,
            "outcome": row.outcome,
            "direction_expected": row.direction_expected,
            "direction_correct": row.direction_correct,
            "unable_reason": row.unable_reason,
            "anchor_date": row.anchor_date.isoformat() if row.anchor_date else None,
            "eval_window_days": row.eval_window_days,
            "start_price": row.start_price,
            "end_close": row.end_close,
            "max_high": row.max_high,
            "min_low": row.min_low,
            "stock_return_pct": row.stock_return_pct,
            "benchmark_code": row.benchmark_code,
            "benchmark_return_pct": row.benchmark_return_pct,
            "excess_return_pct": row.excess_return_pct,
            "benchmark_status": row.benchmark_status,
            "action": row.action,
            "market": row.market,
            "market_phase": row.market_phase,
            "source_type": row.source_type,
            "source_agent": row.source_agent,
            "plan_quality": row.plan_quality,
            "data_quality_level": row.data_quality_level,
            "holding_state": row.holding_state,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_feedback(row: DecisionSignalFeedbackRecord) -> Dict[str, Any]:
        return {
            "signal_id": row.signal_id,
            "feedback_value": row.feedback_value,
            "reason_code": row.reason_code,
            "note": row.note,
            "source": row.source,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _profile_calibration(self, stats_rows: List[OutcomeStatsRow]) -> Dict[str, Any]:
        samples: List[Dict[str, Any]] = []
        for stats_row in stats_rows:
            outcome = stats_row.outcome
            samples.append({
                "outcome": outcome,
                "decision_profile": self._profile_dimension(stats_row.decision_profile),
                "action": str(outcome.action or "unknown"),
                "horizon": str(outcome.horizon or "unknown"),
                "market_phase": str(outcome.market_phase or "unknown"),
                "data_quality_level": str(outcome.data_quality_level or "unknown"),
                "profile_source": self._profile_source(stats_row.metadata_json),
            })
        breakdowns = {
            name: self._profile_calibration_breakdown(samples, dimensions)
            for name, dimensions in PROFILE_CALIBRATION_BREAKDOWN_DIMENSIONS
        }
        return {
            "minimum_completed_sample_size": MIN_PROFILE_CALIBRATION_SAMPLE_SIZE,
            "breakdowns": breakdowns,
        }

    def _profile_calibration_breakdown(
        self,
        samples: List[Dict[str, Any]],
        dimensions: Tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, ...], List[DecisionSignalOutcomeRecord]] = defaultdict(list)
        for sample in samples:
            key = tuple(str(sample.get(dimension) or "unknown") for dimension in dimensions)
            grouped[key].append(sample["outcome"])

        buckets = [
            {
                "dimensions": dict(zip(dimensions, values)),
                **self._profile_calibration_aggregate(rows),
            }
            for values, rows in grouped.items()
        ]
        return sorted(
            buckets,
            key=lambda item: (
                -int(item["total"]),
                tuple(str(item["dimensions"][dimension]) for dimension in dimensions),
            ),
        )

    def _profile_calibration_aggregate(
        self,
        rows: List[DecisionSignalOutcomeRecord],
    ) -> Dict[str, Any]:
        aggregate = self._aggregate(rows)
        sample_sufficient = int(aggregate["completed"]) >= MIN_PROFILE_CALIBRATION_SAMPLE_SIZE
        direction_denominator = int(aggregate["hit"]) + int(aggregate["miss"])
        adverse_excursions = [
            value
            for row in rows
            if (value := self._row_max_adverse_excursion_pct(row)) is not None
        ]
        return {
            "total": aggregate["total"],
            "completed": aggregate["completed"],
            "unable": aggregate["unable"],
            "hit": aggregate["hit"],
            "miss": aggregate["miss"],
            "neutral": aggregate["neutral"],
            "sample_sufficient": sample_sufficient,
            "hit_rate_pct": aggregate["hit_rate_pct"] if sample_sufficient else None,
            "avg_stock_return_pct": aggregate["avg_stock_return_pct"] if sample_sufficient else None,
            "avg_benchmark_return_pct": (
                aggregate["avg_benchmark_return_pct"] if sample_sufficient else None
            ),
            "avg_excess_return_pct": (
                aggregate["avg_excess_return_pct"] if sample_sufficient else None
            ),
            "benchmark_coverage_pct": (
                aggregate["benchmark_coverage_pct"] if sample_sufficient else None
            ),
            "miss_rate_pct": (
                round(int(aggregate["miss"]) / direction_denominator * 100, 2)
                if sample_sufficient and direction_denominator
                else None
            ),
            "unable_rate_pct": (
                round(int(aggregate["unable"]) / int(aggregate["total"]) * 100, 2)
                if sample_sufficient and int(aggregate["total"])
                else None
            ),
            "max_adverse_excursion_pct": (
                round(max(adverse_excursions), 4)
                if sample_sufficient and adverse_excursions
                else None
            ),
        }

    @classmethod
    def _row_max_adverse_excursion_pct(
        cls,
        row: DecisionSignalOutcomeRecord,
    ) -> Optional[float]:
        if not cls._is_positive_finite(row.start_price):
            return None
        start_price = float(row.start_price)
        if row.action in {"buy", "add", "hold", "watch", "alert"}:
            if not cls._is_positive_finite(row.min_low):
                return None
            return max(0.0, (start_price - float(row.min_low)) / start_price * 100)
        if row.action in {"sell", "reduce", "avoid"}:
            if not cls._is_positive_finite(row.max_high):
                return None
            return max(0.0, (float(row.max_high) - start_price) / start_price * 100)
        return None

    @staticmethod
    def _profile_dimension(value: Any) -> str:
        profile = str(value or "").strip().lower()
        return profile if profile in VALID_DECISION_PROFILES else "unknown"

    def _profile_source(self, metadata_json: Optional[str]) -> str:
        metadata = self._json_loads(metadata_json)
        if not isinstance(metadata, dict):
            return "unknown"
        profile_source = str(metadata.get("profile_source") or "").strip().lower()
        return profile_source if profile_source in PROFILE_SOURCES else "unknown"

    def _breakdown(self, rows: List[DecisionSignalOutcomeRecord], dimension: str) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[DecisionSignalOutcomeRecord]] = defaultdict(list)
        for row in rows:
            if dimension == "month":
                anchor_date = row.anchor_date
                key = anchor_date.strftime("%Y-%m") if anchor_date else "unknown"
            else:
                value = getattr(row, dimension, None)
                key = str(value or "unknown")
            grouped[key].append(row)
        buckets = [
            {
                "dimension": dimension,
                "value": value,
                **self._aggregate(bucket_rows),
            }
            for value, bucket_rows in grouped.items()
        ]
        if dimension == "month":
            return sorted(
                buckets,
                key=lambda item: (
                    str(item["value"]) == "unknown",
                    str(item["value"]),
                ),
            )
        return sorted(buckets, key=lambda item: (-int(item["total"]), str(item["value"])))

    @staticmethod
    def _aggregate(rows: List[DecisionSignalOutcomeRecord]) -> Dict[str, Any]:
        total = len(rows)
        completed = [row for row in rows if row.eval_status == "completed"]
        unable = [row for row in rows if row.eval_status == "unable"]
        hit = sum(1 for row in completed if row.outcome == "hit")
        miss = sum(1 for row in completed if row.outcome == "miss")
        neutral = sum(1 for row in completed if row.outcome == "neutral")
        denominator = hit + miss
        returns = [
            float(row.stock_return_pct)
            for row in completed
            if row.stock_return_pct is not None
        ]
        benchmark_returns = [
            float(row.benchmark_return_pct)
            for row in completed
            if row.benchmark_return_pct is not None
        ]
        excess_returns = [
            float(row.excess_return_pct)
            for row in completed
            if row.excess_return_pct is not None
        ]
        benchmark_available = sum(
            1
            for row in completed
            if row.benchmark_status == BENCHMARK_STATUS_AVAILABLE
        )
        unable_reasons = Counter(row.unable_reason or "unknown" for row in unable)
        return {
            "total": total,
            "completed": len(completed),
            "unable": len(unable),
            "hit": hit,
            "miss": miss,
            "neutral": neutral,
            "hit_rate_pct": round(hit / denominator * 100, 2) if denominator else None,
            "avg_stock_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "benchmark_available": benchmark_available,
            "benchmark_coverage_pct": (
                round(benchmark_available / len(completed) * 100, 2)
                if completed
                else None
            ),
            "avg_benchmark_return_pct": (
                round(sum(benchmark_returns) / len(benchmark_returns), 4)
                if benchmark_returns
                else None
            ),
            "avg_excess_return_pct": (
                round(sum(excess_returns) / len(excess_returns), 4)
                if excess_returns
                else None
            ),
            "unable_reasons": dict(sorted(unable_reasons.items())),
        }

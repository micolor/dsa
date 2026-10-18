# -*- coding: utf-8 -*-
"""Background task submissions shared by REST endpoints and agent task tools.

The endpoint modules used to own these lambdas. Moving them here keeps the agent
tools from importing the API layer and guarantees both entry points submit the
same job with the same queue metadata.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from src.config import Config, get_config
from src.core.market_review_lock import (
    MarketReviewExecutionLock,
    release_market_review_lock,
    try_acquire_market_review_lock,
)
from src.services.task_queue import get_task_queue

logger = logging.getLogger(__name__)

# Repository-enforced page sizes. The loops below drain page after page instead
# of treating one page as the whole run.
FUND_REVIEW_BATCH_SIZE = 400
SIGNAL_OUTCOME_BATCH_SIZE = 500


class MarketReviewLockHeldError(RuntimeError):
    """Raised when another market review run holds the shared lock."""


def _queue():
    return get_task_queue()


def _outcome_service():
    from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService
    return DecisionSignalOutcomeService()


def _fund_review_service():
    from src.services.fund_nav_review_service import FundNavReviewService
    return FundNavReviewService()


def _paper_service():
    from src.services.paper_service import PaperService
    return PaperService()


def _build_market_review_runtime(config: Config):
    from src.core.market_review_runtime import build_market_review_runtime
    return build_market_review_runtime(config)


def _usable_db_manager(db_manager: Any) -> Any:
    """Resolve the screening persistence manager.

    Agent submissions do not carry a request-scoped database manager, so fall
    back to the process singleton used by the REST endpoints instead of
    silently dropping screening history.
    """
    if db_manager is not None:
        return db_manager if callable(getattr(db_manager, "save_screening_run", None)) else None
    from src.storage import DatabaseManager

    return DatabaseManager.get_instance()


def submit_stock_analysis(
    stock_code: str,
    *,
    report_type: str = "detailed",
    force_refresh: bool = False,
):
    return _queue().submit_task(
        stock_code,
        report_type=report_type,
        force_refresh=bool(force_refresh),
        query_source="agent",
    )


def run_market_review_background(
    send_notification: bool,
    effective_region: str,
    lock_token: Optional[MarketReviewExecutionLock] = None,
    config: Optional[Config] = None,
    query_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run market review after the API response has been accepted."""
    from src.core.market_review import run_market_review

    runtime_config = config or get_config()
    try:
        notifier, analyzer, search_service = _build_market_review_runtime(runtime_config)
        review_kwargs = {
            "notifier": notifier,
            "analyzer": analyzer,
            "search_service": search_service,
            "config": runtime_config,
            "send_notification": send_notification,
            "override_region": effective_region,
            "return_structured": True,
            "trigger_source": "api",
        }
        if query_id:
            review_kwargs["query_id"] = query_id
        logger.info(
            "[MarketReview] component=market_review action=background_start "
            "trigger_source=api task_id=%s region=%s",
            query_id or "-",
            effective_region,
        )
        report = run_market_review(**review_kwargs)
        if not report:
            raise RuntimeError("大盘复盘未返回可持久化报告")
        if hasattr(report, "report"):
            return {
                "result": report.report,
                "market_review_payload": getattr(report, "market_review_payload", None),
                "region": effective_region,
            }
        return {"result": report, "region": effective_region}
    finally:
        release_market_review_lock(lock_token)


def submit_market_review(
    *,
    send_notification: bool = False,
    region: Optional[str] = None,
    config: Optional[Config] = None,
):
    from src.utils.market_review_region import normalize_market_review_region_lenient

    runtime_config = config or get_config()
    effective_region = normalize_market_review_region_lenient(
        region or runtime_config.market_review_region
    ) or "cn"
    lock_token = try_acquire_market_review_lock(runtime_config)
    if lock_token is None:
        raise MarketReviewLockHeldError("大盘复盘正在执行中，请稍后再试")
    task_id = uuid.uuid4().hex
    logger.info(
        "[MarketReview] component=market_review action=submit trigger_source=api "
        "task_id=%s region=%s send_notification=%s",
        task_id,
        effective_region,
        send_notification,
    )
    try:
        return _queue().submit_background_task(
            lambda: run_market_review_background(
                send_notification,
                effective_region=effective_region,
                lock_token=lock_token,
                config=runtime_config,
                query_id=task_id,
            ),
            stock_code="market_review",
            stock_name="大盘复盘",
            message="大盘复盘任务已提交",
            task_id=task_id,
            region=effective_region,
        )
    except Exception:
        release_market_review_lock(lock_token)
        raise


def submit_screening(
    strategy: str,
    market: str,
    max_results: int,
    variant_seed: str = "",
    *,
    config: Optional[Config] = None,
    db_manager: Any = None,
):
    from src.services.screening_service import ScreeningService

    queue = _queue()
    task_id = uuid.uuid4().hex

    def run_screen() -> Dict[str, Any]:
        queue.update_task_progress(
            task_id,
            20,
            "正在执行选股，外部数据源较慢时会持续后台运行",
        )

        def report_progress(progress: int, message: str) -> None:
            queue.update_task_progress(task_id, progress, message)

        result = ScreeningService(
            config=config or get_config(),
            db_manager=_usable_db_manager(db_manager),
        ).screen(
            strategy=strategy,
            market=market,
            max_results=max_results,
            selection_seed=variant_seed,
            progress_callback=report_progress,
        )
        queue.update_task_progress(
            task_id,
            98,
            f"选股已完成，正在整理 {result.get('candidate_count', 0)} 条候选",
        )
        return result

    return queue.submit_background_task(
        run_screen,
        stock_code="screening_screen",
        stock_name=f"{strategy} / {market}",
        report_type="screening_screen",
        message="选股任务已提交",
        task_id=task_id,
        trace_id=task_id,
    )


def submit_backtest(
    code: Optional[str] = None,
    *,
    force: bool = False,
    limit: int = 200,
):
    from src.services.backtest_service import BacktestService

    return _queue().submit_background_task(
        lambda: BacktestService().run_backtest(
            code=code, force=bool(force), limit=int(limit)
        ),
        stock_code=code or "backtest",
        stock_name="回测" if not code else f"{code} 回测",
        report_type="backtest_run",
        message="回测任务已提交",
    )


def submit_intelligence_fetch():
    from src.services.intelligence_service import IntelligenceService

    return _queue().submit_background_task(
        lambda: IntelligenceService().fetch_enabled_sources(),
        stock_code="intelligence_fetch",
        stock_name="情报抓取",
        report_type="intelligence_fetch",
        message="情报抓取任务已提交",
    )


def submit_fund_nav_backfill(
    *,
    fund_code: Optional[str] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    dry_run: bool = True,
    resolve_missing: bool = False,
):
    service = _fund_review_service()
    return _queue().submit_background_task(
        lambda: service.backfill_from_history(
            fund_code=fund_code,
            since=since,
            until=until,
            dry_run=bool(dry_run),
            resolve_missing=bool(resolve_missing),
            limit=10_000,
        ),
        stock_code=fund_code or "fund_nav_backfill",
        stock_name="基金净值复盘补录",
        report_type="fund_nav_backfill",
        message="基金净值复盘补录任务已提交",
    )


def submit_fund_nav_reviews(
    *,
    fund_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
):
    service = _fund_review_service()

    def run_until_exhausted() -> Dict[str, Any]:
        total = 0
        rounds = 0
        processed_ids: set = set()
        while True:
            result = service.run_reviews(
                fund_code=fund_code,
                horizons=horizons,
                force=bool(force),
                limit=FUND_REVIEW_BATCH_SIZE,
                exclude_ids=sorted(processed_ids),
            )
            rounds += 1
            total += int(result.get("evaluated", 0) or 0)
            reported_ids = result.get("processed_review_ids")
            if isinstance(reported_ids, (list, tuple, set)):
                batch_ids = {
                    int(review_id)
                    for review_id in reported_ids
                    if review_id is not None
                }
                progressed = len(batch_ids)
            else:
                # Compatibility with services that do not report processed ids.
                batch_ids = set()
                progressed = sum(
                    int(result.get(key, 0) or 0)
                    for key in ("evaluated", "pending", "unable", "skipped")
                )
            processed_ids |= batch_ids
            # Rows that legitimately stay pending/unable still count as
            # progress. Excluding the handled ids and fetching the next batch is
            # what makes an evaluated=0 first page safe; only an empty batch
            # (no unprocessed rows remain) ends the run. The processed-id set is
            # finite and only grows, so the loop cannot revisit a row.
            if progressed == 0:
                result["evaluated"] = total
                result["rounds"] = rounds
                return result

    return _queue().submit_background_task(
        run_until_exhausted,
        stock_code=fund_code or "fund_nav_review",
        stock_name="基金净值复盘评估",
        report_type="fund_nav_review",
        message="基金净值复盘评估任务已提交",
    )


def submit_signal_outcomes(
    *,
    stock_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
):
    service = _outcome_service()

    def run_until_exhausted() -> Dict[str, Any]:
        total = 0
        rounds = 0
        processed_ids: set = set()
        seen_outcome_keys: set = set()
        while True:
            result = service.run_outcomes(
                stock_code=stock_code,
                horizons=horizons,
                force=bool(force),
                limit=SIGNAL_OUTCOME_BATCH_SIZE,
                exclude_signal_ids=sorted(processed_ids),
            )
            rounds += 1
            total += int(result.get("evaluated", 0) or 0)
            items = result.get("items")
            round_keys = {
                (item.get("signal_id"), item.get("horizon"))
                for item in (items or [])
                if isinstance(item, dict)
            }
            reported_ids = result.get("processed_signal_ids")
            if isinstance(reported_ids, (list, tuple, set)):
                batch_ids = {
                    int(signal_id)
                    for signal_id in reported_ids
                    if signal_id is not None
                }
                progressed = len(batch_ids)
                stagnant = False
            else:
                # Compatibility with services that do not report processed ids.
                batch_ids = {
                    int(item["signal_id"])
                    for item in (items or [])
                    if isinstance(item, dict) and item.get("signal_id") is not None
                }
                progressed = int(result.get("evaluated", 0) or 0)
                stagnant = bool(round_keys) and round_keys <= seen_outcome_keys
            processed_ids |= batch_ids
            # Retryable ``unable`` rows would otherwise be reselected on every
            # call. Every handled signal is excluded, so force mode covers the
            # whole candidate set exactly once and non-force mode cannot spin on
            # rows whose state did not change.
            seen_outcome_keys |= round_keys
            if progressed == 0 or stagnant:
                result["evaluated"] = total
                result["rounds"] = rounds
                return result

    return _queue().submit_background_task(
        run_until_exhausted,
        stock_code=stock_code or "signal_outcomes",
        stock_name="信号后验评估",
        report_type="signal_outcomes",
        message="信号后验评估任务已提交",
    )


def submit_paper_backfill(
    account_id: int,
    from_date: date,
    to_date: Optional[date] = None,
):
    service = _paper_service()
    return _queue().submit_background_task(
        lambda: service.backfill_history(
            account_id=int(account_id),
            from_date=from_date,
            to_date=to_date,
        ),
        stock_code=f"paper_account_{int(account_id)}",
        stock_name="模拟盘回填",
        report_type="paper_backfill",
        message="模拟盘回填任务已提交",
    )

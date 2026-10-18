# -*- coding: utf-8 -*-
"""Tests for the shared background task submission service."""

from datetime import date
from unittest.mock import MagicMock

from src.services import site_task_submitter as submitter


class FakeTask:
    def __init__(self, task_id="t1"):
        self.task_id = task_id


def test_submit_stock_analysis_uses_queue(monkeypatch):
    queue = MagicMock()
    queue.submit_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    task = submitter.submit_stock_analysis("600519", force_refresh=True)
    assert task.task_id == "t1"
    assert queue.submit_task.call_args.args[0] == "600519"
    assert queue.submit_task.call_args.kwargs["force_refresh"] is True
    assert queue.submit_task.call_args.kwargs["query_source"] == "agent"


def test_submit_signal_outcomes_loops_until_exhausted(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()

    class FakeOutcome:
        def __init__(self):
            self.calls = 0

        def run_outcomes(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"evaluated": 500, "created": 0, "updated": 0}
            if self.calls == 2:
                return {"evaluated": 120, "created": 0, "updated": 0}
            return {"evaluated": 0, "created": 0, "updated": 0}

    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_outcome_service", lambda: FakeOutcome())

    task = submitter.submit_signal_outcomes()
    assert task.task_id == "t1"

    run_task = queue.submit_background_task.call_args.args[0]
    result = run_task()
    assert result["evaluated"] == 620
    assert result["rounds"] == 3


def test_submit_signal_outcomes_stops_when_only_retryable_rows_remain(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    retryable_item = {"signal_id": 1, "horizon": "5d", "eval_status": "unable"}

    class FakeOutcome:
        def run_outcomes(self, **kwargs):
            return {"evaluated": 1, "created": 0, "updated": 1, "items": [retryable_item]}

    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_outcome_service", lambda: FakeOutcome())

    submitter.submit_signal_outcomes()
    result = queue.submit_background_task.call_args.args[0]()

    assert result["rounds"] == 2
    assert result["evaluated"] == 2


class FakeSignalOutcomeService:
    """Selection service that honours the backlog exclusion contract."""

    def __init__(self, candidate_ids, *, state_changes=True):
        self.candidate_ids = list(candidate_ids)
        self.state_changes = state_changes
        self.calls = 0
        self.selected = []

    def run_outcomes(self, *, exclude_signal_ids=None, limit=500, **kwargs):
        self.calls += 1
        excluded = set(exclude_signal_ids or [])
        batch = [signal_id for signal_id in self.candidate_ids if signal_id not in excluded][:limit]
        self.selected.extend(batch)
        return {
            "items": [{"signal_id": signal_id, "horizon": "1d"} for signal_id in batch],
            "evaluated": len(batch) if self.state_changes else 0,
            "created": len(batch) if self.state_changes else 0,
            "updated": 0,
            "skipped": 0,
            "engine_version": "test",
            "processed_signal_ids": batch,
        }


def test_submit_signal_outcomes_force_drains_multi_batch_candidates(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    fake_outcome = FakeSignalOutcomeService(range(1, 1201))
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_outcome_service", lambda: fake_outcome)

    submitter.submit_signal_outcomes(force=True)
    result = queue.submit_background_task.call_args.args[0]()

    assert result["rounds"] == 4  # 500 + 500 + 200 + empty batch
    assert result["evaluated"] == 1200
    assert fake_outcome.calls == 4
    assert fake_outcome.selected == list(range(1, 1201))


def test_submit_signal_outcomes_stops_after_one_pass_when_no_row_changes(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    fake_outcome = FakeSignalOutcomeService(range(1, 1201), state_changes=False)
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_outcome_service", lambda: fake_outcome)

    submitter.submit_signal_outcomes(force=True)
    result = queue.submit_background_task.call_args.args[0]()

    # Unable-to-progress rows are excluded after selection, so the worst case is
    # a single bounded pass over the candidate set plus the terminating empty
    # batch; it never revisits a signal.
    assert result["rounds"] == 4
    assert result["evaluated"] == 0
    assert fake_outcome.selected == list(range(1, 1201))


def test_market_review_releases_lock_on_submit_failure(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.side_effect = RuntimeError("boom")
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "try_acquire_market_review_lock", lambda config: "token")
    released = []
    monkeypatch.setattr(
        submitter, "release_market_review_lock", lambda token: released.append(token)
    )
    monkeypatch.setattr(submitter, "get_config", lambda: MagicMock(market_review_region="cn"))
    try:
        submitter.submit_market_review()
    except RuntimeError:
        pass
    assert released == ["token"]


def test_submit_market_review_normalizes_region(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "try_acquire_market_review_lock", lambda config: "token")
    monkeypatch.setattr(submitter, "get_config", lambda: MagicMock(market_review_region="cn"))

    task = submitter.submit_market_review(region=" KR, jp, KR ")

    assert task.task_id == "t1"
    assert queue.submit_background_task.call_args.kwargs["region"] == "jp,kr"


def test_market_review_refuses_when_lock_is_held(monkeypatch):
    monkeypatch.setattr(submitter, "_queue", lambda: MagicMock())
    monkeypatch.setattr(submitter, "try_acquire_market_review_lock", lambda config: None)
    monkeypatch.setattr(submitter, "get_config", lambda: MagicMock(market_review_region="cn"))

    try:
        submitter.submit_market_review()
    except RuntimeError as exc:
        assert "大盘复盘正在执行中" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("submit_market_review should reject a held lock")


def test_submit_screening_forwards_config_and_db_manager(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)

    screen_calls = {}

    class FakeScreeningService:
        def __init__(self, config, db_manager=None):
            screen_calls["config"] = config
            screen_calls["db_manager"] = db_manager

        def screen(self, **kwargs):
            screen_calls["kwargs"] = kwargs
            kwargs["progress_callback"](66, "正在执行 LLM 候选重排")
            return {"candidate_count": 2}

    monkeypatch.setattr(
        "src.services.screening_service.ScreeningService", FakeScreeningService
    )
    config = object()
    db_manager = MagicMock()

    task = submitter.submit_screening(
        "dual_low", "cn", 3, variant_seed="v1", config=config, db_manager=db_manager
    )

    run_task = queue.submit_background_task.call_args.args[0]
    result = run_task()

    assert task.task_id == "t1"
    assert result["candidate_count"] == 2
    assert screen_calls["config"] is config
    assert screen_calls["db_manager"] is db_manager
    assert screen_calls["kwargs"]["strategy"] == "dual_low"
    assert screen_calls["kwargs"]["selection_seed"] == "v1"
    queue.update_task_progress.assert_any_call(
        queue.submit_background_task.call_args.kwargs["task_id"],
        20,
        "正在执行选股，外部数据源较慢时会持续后台运行",
    )
    queue.update_task_progress.assert_any_call(
        queue.submit_background_task.call_args.kwargs["task_id"],
        66,
        "正在执行 LLM 候选重排",
    )
    queue.update_task_progress.assert_any_call(
        queue.submit_background_task.call_args.kwargs["task_id"],
        98,
        "选股已完成，正在整理 2 条候选",
    )


def test_submit_screening_defaults_to_database_manager(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    db_manager = MagicMock()
    monkeypatch.setattr(
        "src.storage.DatabaseManager.get_instance", lambda: db_manager
    )

    captured = {}

    class FakeScreeningService:
        def __init__(self, config, db_manager=None):
            captured["db_manager"] = db_manager

        def screen(self, **kwargs):
            result = {"candidate_count": 1}
            # Mirrors ScreeningService persistence on the agent path, where no
            # request-scoped db_manager exists.
            captured["db_manager"].save_screening_run(result)
            return result

    monkeypatch.setattr(
        "src.services.screening_service.ScreeningService", FakeScreeningService
    )

    submitter.submit_screening("dual_low", "cn", 3)
    result = queue.submit_background_task.call_args.args[0]()

    assert result == {"candidate_count": 1}
    assert captured["db_manager"] is db_manager
    db_manager.save_screening_run.assert_called_once_with({"candidate_count": 1})


def test_submit_fund_nav_reviews_loops_until_exhausted(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()

    class FakeFundReviews:
        def __init__(self):
            self.calls = 0

        def run_reviews(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"evaluated": 400}
            return {"evaluated": 0}

    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_fund_review_service", lambda: FakeFundReviews())

    task = submitter.submit_fund_nav_reviews(horizons=["5d"])
    result = queue.submit_background_task.call_args.args[0]()

    assert task.task_id == "t1"
    assert result["evaluated"] == 400
    assert result["rounds"] == 2


class FakeFundReviewService:
    """Review service that honours the backlog exclusion contract."""

    def __init__(self, rows):
        self.rows = dict(rows)
        self.calls = 0
        self.selected = []

    def run_reviews(self, *, exclude_ids=None, limit=400, **kwargs):
        self.calls += 1
        excluded = set(exclude_ids or [])
        batch = [row_id for row_id in self.rows if row_id not in excluded][:limit]
        self.selected.extend(batch)
        counts = {"evaluated": 0, "pending": 0, "unable": 0, "skipped": 0}
        for row_id in batch:
            counts[self.rows[row_id]] += 1
        return {
            "items": [],
            **counts,
            "engine_version": "test",
            "processed_review_ids": batch,
        }


def test_submit_fund_nav_reviews_drains_pending_rows_beyond_first_batch(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    fake_reviews = FakeFundReviewService({row_id: "pending" for row_id in range(1, 601)})
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_fund_review_service", lambda: fake_reviews)

    submitter.submit_fund_nav_reviews()
    result = queue.submit_background_task.call_args.args[0]()

    # First batch evaluates nothing, but those rows were handled; the run must
    # continue with the next 200 rows instead of stopping at evaluated == 0.
    assert result["rounds"] == 3  # 400 + 200 + empty batch
    assert result["evaluated"] == 0
    assert fake_reviews.calls == 3
    assert fake_reviews.selected == list(range(1, 601))


def test_submit_fund_nav_reviews_force_drains_every_row_once(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    statuses = ["evaluated", "pending", "unable", "skipped"]
    fake_reviews = FakeFundReviewService(
        {row_id: statuses[row_id % len(statuses)] for row_id in range(1, 901)}
    )
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_fund_review_service", lambda: fake_reviews)

    submitter.submit_fund_nav_reviews(force=True)
    result = queue.submit_background_task.call_args.args[0]()

    assert result["rounds"] == 4  # 400 + 400 + 100 + empty batch
    assert result["evaluated"] == 225
    assert fake_reviews.calls == 4
    assert fake_reviews.selected == list(range(1, 901))


def test_submit_backtest_forwards_filters(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)

    run_backtest = MagicMock(return_value={"total": 1})

    class FakeBacktestService:
        def run_backtest(self, **kwargs):
            return run_backtest(**kwargs)

    monkeypatch.setattr("src.services.backtest_service.BacktestService", FakeBacktestService)

    submitter.submit_backtest("600519", force=True, limit=5)
    queue.submit_background_task.call_args.args[0]()

    run_backtest.assert_called_once_with(code="600519", force=True, limit=5)


def test_submit_intelligence_fetch_uses_service(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)

    fetch = MagicMock(return_value={"fetched": 1})

    class FakeIntelligenceService:
        def fetch_enabled_sources(self):
            return fetch()

    monkeypatch.setattr(
        "src.services.intelligence_service.IntelligenceService", FakeIntelligenceService
    )

    submitter.submit_intelligence_fetch()
    queue.submit_background_task.call_args.args[0]()

    fetch.assert_called_once_with()


def test_submit_paper_backfill_forwards_dates(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)

    backfill = MagicMock(return_value={"processed": 3})

    class FakePaperService:
        def backfill_history(self, **kwargs):
            return backfill(**kwargs)

    monkeypatch.setattr(submitter, "_paper_service", lambda: FakePaperService())

    submitter.submit_paper_backfill(7, date(2026, 1, 1), date(2026, 1, 31))
    queue.submit_background_task.call_args.args[0]()

    backfill.assert_called_once_with(
        account_id=7,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
    )


def test_submit_fund_nav_backfill_has_no_cap(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)

    backfill = MagicMock(return_value={"created": 0})

    class FakeFundReviews:
        def backfill_from_history(self, **kwargs):
            return backfill(**kwargs)

    monkeypatch.setattr(submitter, "_fund_review_service", lambda: FakeFundReviews())

    submitter.submit_fund_nav_backfill(dry_run=False)
    queue.submit_background_task.call_args.args[0]()

    assert backfill.call_args.kwargs["limit"] == 10_000
    assert backfill.call_args.kwargs["dry_run"] is False

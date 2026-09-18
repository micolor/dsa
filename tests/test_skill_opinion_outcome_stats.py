# -*- coding: utf-8 -*-
"""Tests for read-only Skill Opinion Outcome performance statistics."""

from __future__ import annotations

import os
from itertools import count

import pytest

from src.config import Config
from src.repositories.skill_opinion_outcome_repo import (
    SkillOpinionOutcomeRepository,
)
from src.services.skill_opinion_performance_service import (
    MIN_SKILL_OUTCOME_SAMPLE_SIZE,
    SkillOpinionPerformanceService,
)
from src.services.skill_opinion_outcome_service import (
    SKILL_OPINION_OUTCOME_ENGINE_VERSION,
)
from src.storage import (
    AnalysisHistory,
    DatabaseManager,
    SkillOpinionOutcomeRecord,
    SkillOpinionSampleRecord,
)

_ROW_SEQUENCE = count(1)

# 哨兵：区分「调用方没传 reason，用默认值」和「显式传 None，构造无原因的行」。
_REASON_UNSET = object()


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "skill_opinion_outcome_stats.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _add_outcome(
    db: DatabaseManager,
    *,
    skill_id: str = "alpha",
    horizon: str = "1d",
    engine_version: str = SKILL_OPINION_OUTCOME_ENGINE_VERSION,
    eval_status: str,
    outcome: str | None = None,
    directional_return_pct: float | None = None,
    reason: object = _REASON_UNSET,
) -> None:
    with db.session_scope() as session:
        history = AnalysisHistory(
            query_id=(
                f"stats-{skill_id}-{horizon}-{eval_status}-{next(_ROW_SEQUENCE)}"
            ),
            code="600519",
            report_type="simple",
            operation_advice="hold",
        )
        session.add(history)
        session.flush()
        sample = SkillOpinionSampleRecord(
            analysis_history_id=history.id,
            stock_code="600519",
            skill_id=skill_id,
            signal="buy" if eval_status != "observational" else "hold",
            confidence=0.8,
            sample_schema_version="skill-opinion-sample-v1",
        )
        session.add(sample)
        session.flush()
        session.add(
            SkillOpinionOutcomeRecord(
                skill_opinion_sample_id=sample.id,
                horizon=horizon,
                engine_version=engine_version,
                eval_status=eval_status,
                outcome=outcome,
                direction_correct=(
                    outcome == "hit" if eval_status == "evaluated" else None
                ),
                directional_return_pct=directional_return_pct,
                unable_reason=(
                    ("invalid_metadata" if eval_status == "unable" else None)
                    if reason is _REASON_UNSET
                    else reason
                ),
            )
        )


def test_repository_aggregates_raw_bucket_counts(isolated_db) -> None:
    _add_outcome(
        isolated_db,
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=5.0,
    )
    _add_outcome(
        isolated_db,
        eval_status="evaluated",
        outcome="miss",
        directional_return_pct=-2.0,
    )
    _add_outcome(isolated_db, eval_status="observational", outcome="observational")
    _add_outcome(isolated_db, eval_status="unable")
    _add_outcome(isolated_db, eval_status="pending")

    buckets = SkillOpinionOutcomeRepository(
        isolated_db
    ).list_performance_buckets(
        engine_version=SKILL_OPINION_OUTCOME_ENGINE_VERSION,
    )

    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket.skill_id == "alpha"
    assert bucket.horizon == "1d"
    assert bucket.total == 5
    assert bucket.pending == 1
    assert bucket.evaluated == 2
    assert bucket.observational == 1
    assert bucket.unable == 1
    assert bucket.hit == 1
    assert bucket.miss == 1
    assert bucket.avg_directional_return_pct == pytest.approx(1.5)


def test_service_keeps_insufficient_bucket_observational(isolated_db) -> None:
    _add_outcome(
        isolated_db,
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=5.0,
    )
    _add_outcome(
        isolated_db,
        eval_status="evaluated",
        outcome="miss",
        directional_return_pct=-2.0,
    )
    _add_outcome(isolated_db, eval_status="observational", outcome="observational")
    _add_outcome(isolated_db, eval_status="unable")
    _add_outcome(isolated_db, eval_status="pending")

    stats = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()

    assert stats["engine_version"] == SKILL_OPINION_OUTCOME_ENGINE_VERSION
    assert (
        stats["minimum_evaluated_sample_size"]
        == MIN_SKILL_OUTCOME_SAMPLE_SIZE
    )
    assert len(stats["buckets"]) == 1
    bucket = stats["buckets"][0]
    assert bucket["total"] == 5
    assert bucket["evaluated"] == 2
    assert bucket["observational"] == 1
    assert bucket["unable"] == 1
    assert bucket["pending"] == 1
    assert bucket["hit"] == 1
    assert bucket["miss"] == 1
    assert bucket["sample_sufficient"] is False
    assert bucket["sample_status"] == "observational"
    assert bucket["hit_rate_pct"] is None
    assert bucket["miss_rate_pct"] is None
    assert bucket["avg_directional_return_pct"] is None
    assert bucket["unable_rate_pct"] is None


def test_service_unlocks_metrics_at_exact_sample_threshold(isolated_db) -> None:
    for _ in range(18):
        _add_outcome(
            isolated_db,
            eval_status="evaluated",
            outcome="hit",
            directional_return_pct=2.0,
        )
    for _ in range(12):
        _add_outcome(
            isolated_db,
            eval_status="evaluated",
            outcome="miss",
            directional_return_pct=-1.0,
        )
    _add_outcome(isolated_db, eval_status="observational", outcome="observational")
    _add_outcome(isolated_db, eval_status="unable")
    _add_outcome(isolated_db, eval_status="unable")
    _add_outcome(isolated_db, eval_status="pending")

    bucket = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()["buckets"][0]

    assert bucket["total"] == 34
    assert bucket["evaluated"] == 30
    assert bucket["sample_sufficient"] is True
    assert bucket["sample_status"] == "sufficient"
    assert bucket["hit_rate_pct"] == 60.0
    assert bucket["miss_rate_pct"] == 40.0
    assert bucket["avg_directional_return_pct"] == 0.8
    assert bucket["unable_rate_pct"] == 6.06


def test_non_evaluated_rows_do_not_unlock_metrics(isolated_db) -> None:
    # 边界值跟随 MIN_SKILL_OUTCOME_SAMPLE_SIZE，避免阈值调整后这条测试
    # 从「差一条不解锁」变成「差很多条不解锁」而失去边界意义。
    for _ in range(MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1):
        _add_outcome(
            isolated_db,
            eval_status="evaluated",
            outcome="hit",
            directional_return_pct=1.0,
        )
    for _ in range(5):
        _add_outcome(
            isolated_db,
            eval_status="observational",
            outcome="observational",
        )
        _add_outcome(isolated_db, eval_status="unable")
        _add_outcome(isolated_db, eval_status="pending")

    bucket = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()["buckets"][0]

    assert bucket["total"] == MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1 + 15
    assert bucket["evaluated"] == MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1
    assert bucket["sample_sufficient"] is False
    assert bucket["sample_status"] == "observational"
    assert bucket["hit_rate_pct"] is None
    assert bucket["miss_rate_pct"] is None
    assert bucket["avg_directional_return_pct"] is None
    assert bucket["unable_rate_pct"] is None


def test_service_filters_exact_bucket_identity(isolated_db) -> None:
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="1d",
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=1.0,
    )
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="3d",
        eval_status="evaluated",
        outcome="miss",
        directional_return_pct=-1.0,
    )
    _add_outcome(
        isolated_db,
        skill_id="beta",
        horizon="3d",
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=2.0,
    )
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="3d",
        engine_version="skill-opinion-outcome-v2",
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=3.0,
    )

    stats = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats(
        skill_id="alpha",
        horizons=["3d"],
        engine_version=SKILL_OPINION_OUTCOME_ENGINE_VERSION,
    )

    assert len(stats["buckets"]) == 1
    bucket = stats["buckets"][0]
    assert bucket["skill_id"] == "alpha"
    assert bucket["horizon"] == "3d"
    assert bucket["engine_version"] == SKILL_OPINION_OUTCOME_ENGINE_VERSION
    assert bucket["evaluated"] == 1
    assert bucket["miss"] == 1


def test_service_filters_requested_skill_set(isolated_db) -> None:
    for skill_id in ("alpha", "beta", "gamma"):
        _add_outcome(
            isolated_db,
            skill_id=skill_id,
            horizon="1d",
            eval_status="evaluated",
            outcome="hit",
            directional_return_pct=1.0,
        )

    stats = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats(
        skill_ids=[" beta ", "alpha", "beta"],
    )

    assert {
        bucket["skill_id"] for bucket in stats["buckets"]
    } == {"alpha", "beta"}


def test_sibling_buckets_cannot_combine_to_unlock_metrics(
    isolated_db,
) -> None:
    for skill_id, horizon, engine_version in [
        ("alpha", "1d", SKILL_OPINION_OUTCOME_ENGINE_VERSION),
        ("alpha", "3d", SKILL_OPINION_OUTCOME_ENGINE_VERSION),
        ("beta", "1d", SKILL_OPINION_OUTCOME_ENGINE_VERSION),
        ("alpha", "1d", "skill-opinion-outcome-v2"),
    ]:
        for _ in range(MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1):
            _add_outcome(
                isolated_db,
                skill_id=skill_id,
                horizon=horizon,
                engine_version=engine_version,
                eval_status="evaluated",
                outcome="hit",
                directional_return_pct=1.0,
            )

    service = SkillOpinionPerformanceService(db_manager=isolated_db)
    current_buckets = service.get_stats()["buckets"]
    future_buckets = service.get_stats(
        engine_version="skill-opinion-outcome-v2"
    )["buckets"]

    assert len(current_buckets) == 3
    assert all(
        bucket["evaluated"] == MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1
        for bucket in current_buckets
    )
    assert all(
        bucket["sample_sufficient"] is False
        for bucket in current_buckets
    )
    assert len(future_buckets) == 1
    assert future_buckets[0]["evaluated"] == MIN_SKILL_OUTCOME_SAMPLE_SIZE - 1
    assert future_buckets[0]["sample_sufficient"] is False


@pytest.mark.parametrize(
    "filters",
    [
        {"skill_id": "   "},
        {"skill_ids": []},
        {"skill_ids": ["   "]},
        {"skill_id": "alpha", "skill_ids": ["beta"]},
        {"horizons": []},
        {"horizons": ["2d"]},
        {"engine_version": "   "},
    ],
)
def test_service_rejects_invalid_filters(isolated_db, filters) -> None:
    with pytest.raises(ValueError):
        SkillOpinionPerformanceService(
            db_manager=isolated_db
        ).get_stats(**filters)


def test_service_orders_buckets_by_total_then_canonical_identity(
    isolated_db,
) -> None:
    for skill_id, horizon, repetitions in [
        ("zeta", "3d", 3),
        ("alpha", "10d", 2),
        ("alpha", "1d", 2),
        ("beta", "5d", 3),
    ]:
        for _ in range(repetitions):
            _add_outcome(
                isolated_db,
                skill_id=skill_id,
                horizon=horizon,
                eval_status="evaluated",
                outcome="hit",
                directional_return_pct=1.0,
            )

    buckets = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()["buckets"]

    assert [
        (bucket["skill_id"], bucket["horizon"], bucket["total"])
        for bucket in buckets
    ] == [
        ("beta", "5d", 3),
        ("zeta", "3d", 3),
        ("alpha", "1d", 2),
        ("alpha", "10d", 2),
    ]


def test_service_returns_empty_buckets_for_valid_empty_filter(
    isolated_db,
) -> None:
    stats = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats(skill_id="missing")

    assert stats["buckets"] == []


def test_repository_breaks_down_pending_and_unable_reasons(isolated_db) -> None:
    for _ in range(4):
        _add_outcome(
            isolated_db, eval_status="pending", reason="missing_start_bar"
        )
    for _ in range(2):
        _add_outcome(
            isolated_db, eval_status="pending", reason="insufficient_future_data"
        )
    _add_outcome(
        isolated_db, eval_status="unable", reason="invalid_effective_daily_bar_date"
    )
    _add_outcome(
        isolated_db, eval_status="unable", reason="invalid_market_phase_context"
    )

    bucket = SkillOpinionOutcomeRepository(
        isolated_db
    ).list_performance_buckets(
        engine_version=SKILL_OPINION_OUTCOME_ENGINE_VERSION,
    )[0]

    assert bucket.pending == 6
    assert bucket.unable == 2
    assert bucket.pending_reasons == {
        "insufficient_future_data": 2,
        "missing_start_bar": 4,
    }
    assert bucket.unable_reasons == {
        "invalid_effective_daily_bar_date": 1,
        "invalid_market_phase_context": 1,
    }
    # 明细必须能独立还原总数：pending 与 unable 的行在库里必定带 status，
    # 分桶只按 status 切，两边相加等于总量。
    assert sum(bucket.pending_reasons.values()) == bucket.pending
    assert sum(bucket.unable_reasons.values()) == bucket.unable


def test_reasonless_rows_are_counted_as_unknown(isolated_db) -> None:
    # 服务层记录瞬时异常时会写不带原因的 pending（见 _record_retry_attempt）。
    # 这类行不能从明细里消失，否则明细之和小于 pending 总数，读的人会以为漏数。
    _add_outcome(isolated_db, eval_status="pending", reason=None)
    _add_outcome(
        isolated_db, eval_status="pending", reason="missing_start_bar"
    )

    bucket = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()["buckets"][0]

    assert bucket["pending"] == 2
    assert bucket["pending_reasons"] == {"missing_start_bar": 1, "unknown": 1}
    assert bucket["unable_reasons"] == {}


def test_service_exposes_reason_breakdown_in_bucket_payload(isolated_db) -> None:
    _add_outcome(
        isolated_db,
        eval_status="evaluated",
        outcome="hit",
        directional_return_pct=1.0,
    )
    _add_outcome(
        isolated_db, eval_status="pending", reason="missing_start_bar"
    )
    _add_outcome(
        isolated_db, eval_status="unable", reason="unresolvable_expected_start_date"
    )

    bucket = SkillOpinionPerformanceService(
        db_manager=isolated_db
    ).get_stats()["buckets"][0]

    assert bucket["pending_reasons"] == {"missing_start_bar": 1}
    assert bucket["unable_reasons"] == {
        "unresolvable_expected_start_date": 1
    }


def test_reason_breakdown_is_isolated_per_bucket(isolated_db) -> None:
    # 原因明细必须和其他计数一样按 (skill_id, horizon, engine_version) 隔离，
    # 不能把同 skill 的其他 horizon 或同 horizon 的其他 skill 的原因并进来。
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="1d",
        eval_status="pending",
        reason="missing_start_bar",
    )
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="3d",
        eval_status="pending",
        reason="insufficient_future_data",
    )
    _add_outcome(
        isolated_db,
        skill_id="beta",
        horizon="1d",
        eval_status="pending",
        reason="missing_start_bar",
    )
    _add_outcome(
        isolated_db,
        skill_id="alpha",
        horizon="1d",
        engine_version="skill-opinion-outcome-v2",
        eval_status="pending",
        reason="unsupported_horizon",
    )

    buckets = {
        (bucket["skill_id"], bucket["horizon"]): bucket
        for bucket in SkillOpinionPerformanceService(
            db_manager=isolated_db
        ).get_stats()["buckets"]
    }

    assert buckets[("alpha", "1d")]["pending_reasons"] == {
        "missing_start_bar": 1
    }
    assert buckets[("alpha", "3d")]["pending_reasons"] == {
        "insufficient_future_data": 1
    }
    assert buckets[("beta", "1d")]["pending_reasons"] == {
        "missing_start_bar": 1
    }
    # v2 engine version 的桶不进入当前版本统计，也不会把它的原因并进 v1 的同名桶。
    assert len(buckets) == 3

# -*- coding: utf-8 -*-
"""Read-only statistics over persisted individual SkillAgent outcomes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.core.skill_opinion_outcome_evaluator import (
    SUPPORTED_SKILL_OUTCOME_HORIZONS,
)
from src.repositories.skill_opinion_outcome_repo import (
    SkillOpinionOutcomeRepository,
    SkillOpinionPerformanceBucket,
)
from src.services.skill_opinion_outcome_service import (
    SKILL_OPINION_OUTCOME_ENGINE_VERSION,
)
from src.storage import DatabaseManager


# 每个 (skill_id, horizon) 桶进入加权所需的最小 evaluated 样本数。
#
# 原值 30 在任何桶上都不可能达标，而不是「等样本长起来就会到」：实测每个桶的
# total 上限只有 13（24 个桶中最大者），而 evaluated 仅占其中约 10%（152 条后验
# 中只有 16 条 evaluated）。阈值 30 因此是结构性不可达的，compute_weights 恒定
# 返回 1.0，加权结果与「没有复盘」逐位相同。
#
# 下调到 5 的真实效果：当前数据下仍然一个桶都不达标（桶内 evaluated 最大为 2），
# 所以今天的行为与改前完全一致。它的意义是让阈值回到样本成熟后确实可以达到的
# 量级，而不是继续保留一个永远不会触发的开关。
MIN_SKILL_OUTCOME_SAMPLE_SIZE = 5


class SkillOpinionPerformanceService:
    """Apply sample-sufficiency policy to raw Outcome aggregates."""

    def __init__(
        self,
        *,
        repo: Optional[SkillOpinionOutcomeRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.repo = repo or SkillOpinionOutcomeRepository(db_manager)

    def get_stats(
        self,
        *,
        skill_id: Optional[str] = None,
        skill_ids: Optional[Sequence[str]] = None,
        horizons: Optional[Sequence[str]] = None,
        engine_version: str = SKILL_OPINION_OUTCOME_ENGINE_VERSION,
    ) -> Dict[str, Any]:
        """Return low-sensitive statistics for the current engine version."""

        if skill_id is not None and skill_ids is not None:
            raise ValueError("skill_id and skill_ids are mutually exclusive")
        skill_id_norm = (
            self._required_text(skill_id, "skill_id")
            if skill_id is not None
            else None
        )
        skill_ids_norm = (
            self._normalize_skill_ids(skill_ids)
            if skill_ids is not None
            else None
        )
        horizons_norm = self._normalize_horizons(horizons)
        engine_version_norm = self._required_text(
            engine_version,
            "engine_version",
        )
        buckets = self.repo.list_performance_buckets(
            engine_version=engine_version_norm,
            skill_id=skill_id_norm,
            skill_ids=skill_ids_norm,
            horizons=horizons_norm,
        )
        horizon_rank = {
            horizon: index
            for index, horizon in enumerate(
                SUPPORTED_SKILL_OUTCOME_HORIZONS
            )
        }
        buckets.sort(
            key=lambda bucket: (
                -bucket.total,
                bucket.skill_id,
                horizon_rank[bucket.horizon],
            )
        )
        return {
            "engine_version": engine_version_norm,
            "minimum_evaluated_sample_size": MIN_SKILL_OUTCOME_SAMPLE_SIZE,
            "buckets": [self._serialize_bucket(bucket) for bucket in buckets],
        }

    @staticmethod
    def _serialize_bucket(
        bucket: SkillOpinionPerformanceBucket,
    ) -> Dict[str, Any]:
        sample_sufficient = (
            bucket.evaluated >= MIN_SKILL_OUTCOME_SAMPLE_SIZE
        )
        direction_denominator = bucket.hit + bucket.miss
        terminal_denominator = (
            bucket.evaluated + bucket.observational + bucket.unable
        )
        return {
            "skill_id": bucket.skill_id,
            "horizon": bucket.horizon,
            "engine_version": bucket.engine_version,
            "total": bucket.total,
            "pending": bucket.pending,
            "evaluated": bucket.evaluated,
            "observational": bucket.observational,
            "unable": bucket.unable,
            "hit": bucket.hit,
            "miss": bucket.miss,
            "pending_reasons": bucket.pending_reasons,
            "unable_reasons": bucket.unable_reasons,
            "sample_sufficient": sample_sufficient,
            "sample_status": (
                "sufficient" if sample_sufficient else "observational"
            ),
            "hit_rate_pct": (
                round(bucket.hit / direction_denominator * 100, 2)
                if sample_sufficient and direction_denominator
                else None
            ),
            "miss_rate_pct": (
                round(bucket.miss / direction_denominator * 100, 2)
                if sample_sufficient and direction_denominator
                else None
            ),
            "avg_directional_return_pct": (
                round(bucket.avg_directional_return_pct, 4)
                if sample_sufficient
                and bucket.avg_directional_return_pct is not None
                else None
            ),
            "unable_rate_pct": (
                round(bucket.unable / terminal_denominator * 100, 2)
                if sample_sufficient and terminal_denominator
                else None
            ),
        }

    @staticmethod
    def _required_text(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} must not be blank")
        return text

    @classmethod
    def _normalize_skill_ids(
        cls,
        values: Sequence[str],
    ) -> List[str]:
        if isinstance(values, (str, bytes)) or not values:
            raise ValueError("skill_ids must not be empty")
        normalized: List[str] = []
        for value in values:
            skill_id = cls._required_text(value, "skill_ids")
            if skill_id not in normalized:
                normalized.append(skill_id)
        return normalized

    @staticmethod
    def _normalize_horizons(
        values: Optional[Sequence[str]],
    ) -> Optional[List[str]]:
        if values is None:
            return None
        if not values:
            raise ValueError("horizons must not be empty")

        normalized: List[str] = []
        for value in values:
            horizon = str(value or "").strip()
            if horizon not in SUPPORTED_SKILL_OUTCOME_HORIZONS:
                raise ValueError(
                    "horizon must be one of "
                    + ", ".join(SUPPORTED_SKILL_OUTCOME_HORIZONS)
                )
            if horizon not in normalized:
                normalized.append(horizon)
        return normalized

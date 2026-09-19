# -*- coding: utf-8 -*-
"""阶段进度文案：阶段 id → 用户可见中文名与整句文案。

「思考过程」里的阶段文案是直接给用户看的，而阶段 id（``technical`` /
``intel`` / ``skill_shrink_pullback`` / ``agent_loop``）是内部标识，照搬展示
会被当成一串看不懂的英文。战法阶段的名称复用 :mod:`src.report_language` 的
既有译名表，避免再维护一份会漂移的副本。
"""

from typing import Optional

from src.report_language import localize_strategy_skill

# 流水线阶段 id → 中文名。战法阶段是 ``skill_<skill_id>`` 形式，单独处理。
STAGE_DISPLAY_NAMES = {
    "technical": "技术面",
    "intel": "情报面",
    "risk": "风险面",
    "decision": "决策",
    "portfolio": "持仓",
    "agent_loop": "整体分析",
}

SKILL_STAGE_PREFIX = "skill_"

# 认不出阶段 id 时的兜底名：不 echo 内部 id，避免又把英文 id 送到界面上。
UNKNOWN_STAGE_LABEL = "阶段"

# 与客户端 ``isStageDoneSuccessful`` 保持一致的「算成功」状态集合。
_SUCCESS_STATUSES = frozenset({"completed", "success", "succeeded", "done"})


def stage_display_name(stage: Optional[str]) -> str:
    """Return the user-visible Chinese name for a pipeline stage id.

    Unknown custom skills fall back to their own id (same contract as the web
    client's ``getSkillLabel``): an unrecognised skill is still more useful to
    read than a generic placeholder.
    """
    name = (stage or "").strip()
    if not name:
        return UNKNOWN_STAGE_LABEL
    label = STAGE_DISPLAY_NAMES.get(name)
    if label is not None:
        return label
    if name.startswith(SKILL_STAGE_PREFIX):
        skill_id = name[len(SKILL_STAGE_PREFIX):]
        if skill_id:
            return localize_strategy_skill(skill_id, "zh")
    return UNKNOWN_STAGE_LABEL


def stage_start_message(stage: Optional[str]) -> str:
    """「思考过程」里阶段开始时的整句文案。"""
    return f"{stage_display_name(stage)}进行中..."


def stage_done_message(stage: Optional[str], status: Optional[str] = None) -> str:
    """「思考过程」里阶段结束时的整句文案。"""
    label = stage_display_name(stage)
    if (status or "").strip().lower() in _SUCCESS_STATUSES:
        return f"{label}完成"
    return f"{label}未完成"


def stage_timeout_message(stage: Optional[str]) -> str:
    """阶段因超时被中断时的整句文案。"""
    return f"{stage_display_name(stage)}超时"


def stage_budget_skipped_message(stage: Optional[str]) -> str:
    """阶段因剩余预算不足被跳过时的整句文案。"""
    return f"{stage_display_name(stage)}因剩余预算不足被跳过"

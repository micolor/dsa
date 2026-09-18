# -*- coding: utf-8 -*-
"""保证 Web 侧 skill 中文名映射与后端 _STRATEGY_SKILL_TRANSLATIONS 不漂移。

Skill 是开放集合，但内建策略集合由后端权威维护。Web 需要一份独立映射才能把
skill_id 渲染为用户可读标签（见 docs/decision-signals.md「Web 展示」章节：
API 保留原始枚举值，Web 负责映射）。本测试防止后端新增策略而前端漏补。
"""

from __future__ import annotations

import re
from pathlib import Path

from src import report_language


ROOT = Path(__file__).resolve().parents[1]
UI_TEXT_PATH = "apps/dsa-web/src/i18n/uiText.ts"

# zh 块与 en 块的分界：UiTextKey 由 zh 推导，en 声明为 Record<UiTextKey, string>。
_EN_BLOCK_MARKER = "const en: Record<UiTextKey, string> = {"


def _ui_text_source() -> str:
    return (ROOT / UI_TEXT_PATH).read_text(encoding="utf-8")


def _parse_web_skill_labels(block: str) -> dict[str, str]:
    """从 uiText.ts 的 zh / en 块解析 decisionSignals.skill.<id> -> 文案。"""
    return {
        skill_id: text
        for skill_id, _, text in re.findall(
            r"['\"]decisionSignals\.skill\.([a-z0-9_]+)['\"]\s*:\s*(['\"])(.*?)\2",
            block,
        )
    }


def test_every_backend_strategy_skill_has_a_web_label() -> None:
    source = _ui_text_source()
    marker_index = source.index(_EN_BLOCK_MARKER)
    zh_block = source[:marker_index]
    en_block = source[marker_index:]

    missing_zh: list[str] = []
    missing_en: list[str] = []

    for skill_id in report_language._STRATEGY_SKILL_TRANSLATIONS:
        # 用带引号的完整 key 匹配，避免 bull_trend 误命中 decisionSignals.skill.bull_trend_v2
        pattern = re.compile(rf"['\"]decisionSignals\.skill\.{re.escape(skill_id)}['\"]\s*:")
        if not pattern.search(zh_block):
            missing_zh.append(skill_id)
        if not pattern.search(en_block):
            missing_en.append(skill_id)

    assert missing_zh == [], (
        f"以下 skill 缺少中文名 i18n key decisionSignals.skill.<id>：{missing_zh}"
    )
    assert missing_en == [], (
        f"以下 skill 缺少英文名 i18n key decisionSignals.skill.<id>：{missing_en}"
    )


def test_web_skill_map_covers_backend_skill_translations() -> None:
    """双向一致性检查：Web 的 SKILL_LABEL_KEYS 必须与后端内置策略集合严格相等。

    方向一：Web 不得凭空发明后端不存在的 id，否则说明两边对「内建策略集合」的理解已分叉。
    方向二：后端新增策略时 Web 必须同步补齐，否则该策略在页面上只能回退显示原始英文 id。

    因此 Web 映射不允许「多于后端」：历史遗留的策略 id 应补进后端的
    _STRATEGY_SKILL_TRANSLATIONS（那是权威真源，且 skill id 会被持久化），
    而不是只在前端保留一份。
    """
    source = (ROOT / "apps/dsa-web/src/utils/decisionSignalLabels.ts").read_text(
        encoding="utf-8"
    )
    block_start = source.index("const SKILL_LABEL_KEYS")
    block_end = source.index("};", block_start)
    block = source[block_start:block_end]

    web_map = dict(
        re.findall(r"^\s*([a-z0-9_]+):\s*'decisionSignals\.skill\.([a-z0-9_]+)'", block, re.M)
    )
    web_ids = set(web_map)
    backend_ids = set(report_language._STRATEGY_SKILL_TRANSLATIONS)

    assert web_ids, "未能从 decisionSignalLabels.ts 解析出任何 skill id，解析逻辑需要更新"

    # 仅校验 id 集合不够：把 wave_theory 指向 decisionSignals.skill.chan_theory 时，
    # id 集合与两个 i18n key 都存在、tsc 也不报错，但 UI 会显示错误的策略名。
    mismatched_keys = {k: v for k, v in web_map.items() if k != v}
    assert mismatched_keys == {}, (
        f"以下 skill 的 i18n key 与自身 id 不符（id, key 后缀）：{mismatched_keys}"
    )

    assert web_ids == backend_ids, (
        "Web 与后端的 skill 集合不一致："
        f"仅 Web 有 {sorted(web_ids - backend_ids)}；"
        f"仅后端有 {sorted(backend_ids - web_ids)}"
    )


def test_web_skill_label_text_matches_backend_text() -> None:
    """zh / en 文案本身不得与后端漂移。

    只比对 key 存在性无法发现改字：把「波浪理论」写成「海浪理论」时，
    只查 key 的用例仍然全绿。这里逐条比对后端 _STRATEGY_SKILL_TRANSLATIONS
    的 zh / en 原文，使 decisionSignalLabels.ts 中「保证两边不漂移」的注释成立。
    """
    source = _ui_text_source()
    marker_index = source.index(_EN_BLOCK_MARKER)
    web_zh = _parse_web_skill_labels(source[:marker_index])
    web_en = _parse_web_skill_labels(source[marker_index:])

    mismatched_zh = {
        skill_id: (web_zh.get(skill_id), tr["zh"])
        for skill_id, tr in report_language._STRATEGY_SKILL_TRANSLATIONS.items()
        if web_zh.get(skill_id) != tr["zh"]
    }
    mismatched_en = {
        skill_id: (web_en.get(skill_id), tr["en"])
        for skill_id, tr in report_language._STRATEGY_SKILL_TRANSLATIONS.items()
        if web_en.get(skill_id) != tr["en"]
    }

    assert mismatched_zh == {}, (
        f"以下 skill 的中文名与后端不一致 (web, backend)：{mismatched_zh}"
    )
    assert mismatched_en == {}, (
        f"以下 skill 的英文名与后端不一致 (web, backend)：{mismatched_en}"
    )

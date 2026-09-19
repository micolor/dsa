# -*- coding: utf-8 -*-
"""阶段文案（``src/agent/stage_labels.py``）的回归。

「思考过程」的阶段文案直接给用户看，因此这里守住两条契约：核心阶段与战法
阶段给出中文名，认不出的阶段 id 不得被原样回显到界面上。
"""

import unittest

from src.agent.stage_labels import (
    STAGE_DISPLAY_NAMES,
    UNKNOWN_STAGE_LABEL,
    stage_budget_skipped_message,
    stage_display_name,
    stage_done_message,
    stage_start_message,
    stage_timeout_message,
)

# ``_build_agent_chain`` 在各模式下会走到的核心阶段 id。
CORE_STAGE_IDS = ("technical", "intel", "risk", "decision", "portfolio", "agent_loop")


class TestStageDisplayName(unittest.TestCase):
    def test_core_stages_have_chinese_names(self) -> None:
        for stage in CORE_STAGE_IDS:
            with self.subTest(stage=stage):
                label = stage_display_name(stage)
                self.assertEqual(label, STAGE_DISPLAY_NAMES[stage])
                self.assertTrue(
                    any("一" <= ch <= "鿿" for ch in label),
                    f"{stage} 的中文名不含汉字：{label}",
                )

    def test_skill_stage_reuses_report_language_translation(self) -> None:
        self.assertEqual(stage_display_name("skill_shrink_pullback"), "缩量回踩")
        self.assertEqual(stage_display_name("skill_bull_trend"), "默认多头趋势")

    def test_unknown_skill_keeps_its_id(self) -> None:
        # 与前端 getSkillLabel 一致：未知战法保留 id 比换成占位符更有信息量。
        self.assertEqual(stage_display_name("skill_my_custom_skill"), "my_custom_skill")

    def test_unknown_stage_falls_back_to_generic_label(self) -> None:
        self.assertEqual(stage_display_name("totally_unknown"), UNKNOWN_STAGE_LABEL)

    def test_blank_stage_falls_back_to_generic_label(self) -> None:
        for stage in ("", "   ", None, "skill_"):
            with self.subTest(stage=stage):
                self.assertEqual(stage_display_name(stage), UNKNOWN_STAGE_LABEL)


class TestStageMessages(unittest.TestCase):
    def test_start_and_done_messages(self) -> None:
        self.assertEqual(stage_start_message("technical"), "技术面进行中...")
        self.assertEqual(stage_done_message("technical", "completed"), "技术面完成")
        self.assertEqual(stage_done_message("technical", "success"), "技术面完成")

    def test_done_message_reports_incomplete_stages(self) -> None:
        for status in ("failed", "timeout", "skipped", None):
            with self.subTest(status=status):
                self.assertEqual(stage_done_message("decision", status), "决策未完成")

    def test_timeout_and_budget_skipped_messages(self) -> None:
        self.assertEqual(stage_timeout_message("risk"), "风险面超时")
        self.assertEqual(
            stage_budget_skipped_message("decision"), "决策因剩余预算不足被跳过"
        )

    def test_messages_do_not_echo_the_raw_stage_id(self) -> None:
        for stage in CORE_STAGE_IDS:
            messages = (
                stage_start_message(stage),
                stage_done_message(stage, "completed"),
                stage_done_message(stage, "failed"),
                stage_timeout_message(stage),
                stage_budget_skipped_message(stage),
            )
            for message in messages:
                with self.subTest(stage=stage, message=message):
                    self.assertNotIn(stage, message)

    def test_unknown_stage_message_does_not_echo_the_id(self) -> None:
        self.assertEqual(stage_start_message("totally_unknown"), "阶段进行中...")
        self.assertEqual(stage_timeout_message("totally_unknown"), "阶段超时")


if __name__ == "__main__":
    unittest.main()

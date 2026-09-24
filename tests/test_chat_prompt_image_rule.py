# -*- coding: utf-8 -*-
"""图中股票的加自选提案规则：触发条件必须窄。

上一个计划踩过"触发条件过宽 → 模型在没人要求时开卡片"的坑，因此这里同时钉住
"必须提案"与"不得仅因图中出现代码而提案"两半。
"""

from __future__ import annotations

import unittest

from src.agent.executor import CHAT_SYSTEM_PROMPT, LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CODEX_CHAT_SYSTEM_PROMPT

CHAT_PROMPTS = (LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT)


class TestChatPromptImageRule(unittest.TestCase):
    def test_both_chat_prompts_carry_the_image_rule(self):
        for prompt in CHAT_PROMPTS:
            self.assertIn("图中股票的加自选提案", prompt)
            self.assertIn("propose_watchlist_change", prompt)
            # 窄触发的两半都要在
            self.assertIn("明确表达", prompt)
            self.assertIn("不得主动提案", prompt)

    def test_rule_blocks_are_identical_across_variants(self):
        blocks = []
        for prompt in CHAT_PROMPTS:
            block = [ln for ln in prompt.splitlines() if ln.startswith("10. ")]
            self.assertEqual(len(block), 1, msg=f"规则 10 的行数不是 1：{block}")
            blocks.append(block[0])
        self.assertEqual(blocks[0], blocks[1], msg="两套 chat 提示词的规则 10 必须逐字相同")

    def test_codex_prompt_does_not_get_the_rule(self):
        """Codex 是只读面，提案工具不在该面上（见 tool_surface 的 cancellation_safe 过滤）。"""
        self.assertNotIn("图中股票的加自选提案", CODEX_CHAT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()

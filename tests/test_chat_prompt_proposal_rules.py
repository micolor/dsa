# -*- coding: utf-8 -*-
"""对话提示词必须告诉模型「写操作走提案」，否则模型只会回一句「我没有写入工具」。

前端与后端已经能承接 ``propose_portfolio_trade`` / ``propose_watchlist_change``
的提案卡片，但只有当提示词明确写出这两个工具名与「不得声称自己只能查询」时，
模型才会在「帮我把这笔买入记进去」这类请求上调工具，而不是拒绝。
反过来，Codex 只读面（``CODEX_CHAT_SYSTEM_PROMPT``）与两个分析报告提示词都
不暴露提案工具（``ToolPolicy.cancellation_safe=False``），提示词里出现工具名会
让模型调用一个必然失败的工具，因此这里同时钉住「不许出现」。
"""

import unittest

from src.agent.executor import (
    AGENT_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CODEX_CHAT_SYSTEM_PROMPT,
    LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT,
    LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT,
)

# 两个非 Codex 对话变体：提案规则必须同时存在于这两份提示词里。
CHAT_PROMPTS = (LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT)
# 两个分析报告变体：只读产出报告，绝不能被要求发起提案。
ANALYSIS_PROMPTS = (LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT, AGENT_SYSTEM_PROMPT)

PROPOSAL_TOOL_NAMES = ("propose_portfolio_trade", "propose_watchlist_change")


def _rules_7_to_9(prompt: str) -> str:
    """抽取 7/8/9 三条规则原文，用于确认两个对话变体逐字一致。"""
    lines = [line for line in prompt.splitlines() if line[:2] in ("7.", "8.", "9.")]
    return "\n".join(lines)


class TestChatPromptProposalRules(unittest.TestCase):
    def test_guard_scans_the_real_prompt_constants(self):
        """先确认扫到的提示词非空且规则区存在，否则下面的断言会空过。"""
        for prompt in CHAT_PROMPTS:
            self.assertGreater(len(prompt), 1000)
            self.assertIn("## 规则", prompt)
        for prompt in ANALYSIS_PROMPTS:
            self.assertGreater(len(prompt), 3000)
            self.assertIn("## 规则", prompt)
        self.assertGreater(len(CODEX_CHAT_SYSTEM_PROMPT), 300)
        self.assertIn("## 工作方式", CODEX_CHAT_SYSTEM_PROMPT)

    def test_chat_prompts_instruct_both_proposal_tools(self):
        for prompt in CHAT_PROMPTS:
            for tool_name in PROPOSAL_TOOL_NAMES:
                self.assertIn(tool_name, prompt, msg=f"对话提示词缺少 {tool_name} 提案规则")

    def test_chat_prompts_forbid_claiming_read_only(self):
        for prompt in CHAT_PROMPTS:
            self.assertIn("不得**声称自己只能查询、无法写入", prompt)

    def test_chat_prompts_keep_the_existing_alert_proposal_rule(self):
        for prompt in CHAT_PROMPTS:
            self.assertIn("propose_alert", prompt)
            self.assertIn("告警提案", prompt)

    def test_chat_prompt_variants_carry_identical_new_rules(self):
        blocks = {_rules_7_to_9(prompt) for prompt in CHAT_PROMPTS}
        self.assertEqual(len(blocks), 1, msg=f"对话提示词的 7/8/9 规则不一致：{blocks}")
        block = blocks.pop()
        for tool_name in PROPOSAL_TOOL_NAMES:
            self.assertIn(tool_name, block)

    def test_codex_chat_prompt_does_not_expose_proposal_tools(self):
        """Codex 只读面拿不到提案工具，提示词里出现工具名等于让模型必然失败。"""
        for tool_name in PROPOSAL_TOOL_NAMES:
            self.assertNotIn(tool_name, CODEX_CHAT_SYSTEM_PROMPT)
        self.assertNotIn("propose_alert", CODEX_CHAT_SYSTEM_PROMPT)

    def test_analysis_prompts_do_not_mention_proposal_tools(self):
        for prompt in ANALYSIS_PROMPTS:
            for tool_name in PROPOSAL_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt)
            # 分析报告面从来没有告警提案规则，保持原样。
            self.assertNotIn("propose_alert", prompt)


if __name__ == "__main__":
    unittest.main()

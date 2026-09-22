# -*- coding: utf-8 -*-
"""对话提示词必须告诉模型「写操作走提案」，否则模型只会回一句「我没有写入工具」。

前端与后端已经能承接 ``propose_portfolio_trade`` / ``propose_watchlist_change``
的提案卡片，但只有当提示词明确写出这两个工具名与「不得声称自己只能查询」时，
模型才会在「帮我把这笔买入记进去」这类请求上调工具，而不是拒绝。
反过来，Codex 只读面（``CODEX_CHAT_SYSTEM_PROMPT``）与两个分析报告提示词都
不暴露提案工具（``ToolPolicy.cancellation_safe=False``），提示词里出现工具名会
让模型调用一个必然失败的工具，因此这里同时钉住「不许出现」。
"""

import re
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
# 提示词点名要模型调用的全部工具：三个提案工具，加上规则 7 用来解析账户的读工具。
# get_portfolio_snapshot 也不在 Codex 只读面上（未声明 cancellation_safe），所以
# 「Codex 提示词里不许出现」的清单必须连它一起钉住。
PROMPT_NAMED_TOOLS = ("propose_alert",) + PROPOSAL_TOOL_NAMES + ("get_portfolio_snapshot",)
# 分析报告提示词里只钉死提案工具的缺席：那才是真边界（只读报告面不暴露提案工具）。
# get_portfolio_snapshot 是**读**工具，分析提示词里出现它（例如「先看用户持仓」）
# 是合法的、甚至可能是以后想要的行为，钉住它的缺席只会在合法变更上误报。
ANALYSIS_FORBIDDEN_TOOLS = ("propose_alert",) + PROPOSAL_TOOL_NAMES

# 规则正文里反引号标注的提案工具名。
_BACKTICKED_PROPOSAL_TOOL = re.compile(r"`(propose_[a-z_]+)`")


def _rules_7_to_9(prompt: str) -> str:
    """抽取 7/8/9 三条规则原文，用于确认两个对话变体逐字一致。"""
    lines = [line for line in prompt.splitlines() if line[:2] in ("7.", "8.", "9.")]
    return "\n".join(lines)


def _rules_6_to_9(prompt: str) -> str:
    """抽取 6/7/8/9 四条提案规则原文，用于对注册表核名。"""
    lines = [line for line in prompt.splitlines() if line[:2] in ("6.", "7.", "8.", "9.")]
    return "\n".join(lines)


def _named_proposal_tools_in_rules(prompt: str) -> set:
    """规则 6–9 里点名的全部 `propose_*` 工具名。"""
    return set(_BACKTICKED_PROPOSAL_TOOL.findall(_rules_6_to_9(prompt)))


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
            self.assertIn("声称自己只能查询、无法写入", prompt)
            # 规则 7 的禁令必须限定在「有对应提案工具」的情形，否则规则 9 的
            # 「如实说明」会被读成「写操作都可以拒绝」的许可证，把整个任务的
            # 目的（不再回答「我无法写入」）反过来。
            self.assertIn("在该笔录入有对应提案工具时声称自己只能查询、无法写入", prompt)

    def test_chat_prompts_keep_the_unrecoverable_clauses(self):
        """「先追问 / 在用户确认前」这两条是设计里明确的不可恢复约束，单独钉住。

        「两个变体逐字一致」那条是拿两份提示词互相比较，两边一起删也照样绿，
        所以这几条必须各自独立断言。
        """
        for prompt in CHAT_PROMPTS:
            self.assertIn("先追问用户", prompt)
            self.assertIn("不许猜", prompt)
            self.assertIn("在用户确认前", prompt)
            self.assertIn("不得用行情价或估算值顶替", prompt)

    def test_chat_prompts_give_edits_an_honest_exit(self):
        """没有对应提案工具的写操作（改/删已有记录）必须如实说明，不得顶替。

        后半句「不要用其他提案工具顶替」才是这条规则存在的理由：它同时禁掉了
        两条歧路——发一笔对冲的反向提案，或者把请求塞进自选工具。少了它，
        规则 9 只剩「如实说明」，模型仍会挑一条错的执行路径。
        """
        for prompt in CHAT_PROMPTS:
            self.assertIn("没有对应提案工具的操作，如实说明", prompt)
            self.assertIn("不要用其他提案工具顶替", prompt)

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
        for tool_name in PROMPT_NAMED_TOOLS:
            self.assertNotIn(tool_name, CODEX_CHAT_SYSTEM_PROMPT)

    def test_analysis_prompts_do_not_mention_proposal_tools(self):
        for prompt in ANALYSIS_PROMPTS:
            for tool_name in ANALYSIS_FORBIDDEN_TOOLS:
                self.assertNotIn(tool_name, prompt)


class TestPromptNamedToolsExistInRegistry(unittest.TestCase):
    """提示词点名要模型调用的工具，必须真的注册在案。

    两个方向都要抓：
    - 提示词 → 注册表：规则 6–9 里写到的每个 `propose_*` 都从原文扫出来对注册表，
      因此「提示词里新写了一个未注册的工具名」（改名、笔误、手滑）会直接转红，
      不需要那份手写名单先知道这个名字；
    - 注册表 → 提示词：手写名单 `PROMPT_NAMED_TOOLS` 反过来确认名单里的名字
      仍然被点名，抓「规则被删掉/改名后名字从提示词里消失」——此时前者扫不到
      任何可疑 token，会静默变绿。
    两个方向缺一不可。
    """

    def _registered(self) -> set:
        from src.agent.factory import get_tool_registry

        return set(get_tool_registry().list_names())

    def test_every_prompt_named_tool_is_registered(self):
        registered = self._registered()
        self.assertIn("propose_portfolio_trade", registered)  # 先确认扫的是真注册表
        for tool_name in PROMPT_NAMED_TOOLS:
            self.assertIn(tool_name, registered, msg=f"提示词点名了未注册的工具 {tool_name}")

    def test_rules_name_no_unregistered_proposal_tool(self):
        """规则 6–9 里出现的每个 `propose_*` 工具名都必须注册在案。"""
        registered = self._registered()
        for prompt in CHAT_PROMPTS:
            named = _named_proposal_tools_in_rules(prompt)
            self.assertTrue(named, msg="规则 6–9 里一个提案工具名都没扫到，断言会空过")
            self.assertLessEqual(
                named,
                registered,
                msg=f"规则里点名的工具未注册：{sorted(named - registered)}",
            )

    def test_chat_prompts_name_exactly_those_tools(self):
        for prompt in CHAT_PROMPTS:
            for tool_name in PROMPT_NAMED_TOOLS:
                self.assertIn(f"`{tool_name}`", prompt)


if __name__ == "__main__":
    unittest.main()

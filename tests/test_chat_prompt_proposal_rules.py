# -*- coding: utf-8 -*-
"""对话提示词必须告诉模型「写操作走提案」，否则模型只会回一句「我没有写入工具」。

前端与后端已经能承接全部 ``propose_*`` 工具的提案卡片，但只有当提示词明确写出
这些工具名与「不得声称自己只能查询」时，模型才会在「帮我把这笔买入记进去」这类
请求上调工具，而不是拒绝。反过来，Codex 只读面（``CODEX_CHAT_SYSTEM_PROMPT``）
与两个分析报告提示词都不暴露提案工具（``ToolPolicy.cancellation_safe=False``），
提示词里出现工具名会让模型调用一个必然失败的工具，因此这里同时钉住「不许出现」。
"""

import re
import unittest

from src.agent.executor import (
    AGENT_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CODEX_CHAT_SYSTEM_PROMPT,
    LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT,
    LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT,
    SITE_OPERATIONS_DISABLED_NOTICE,
    prepare_agent_chat,
)

# 两个非 Codex 对话变体：提案规则必须同时存在于这两份提示词里。
CHAT_PROMPTS = (LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT)
# 两个分析报告变体：只读产出报告，绝不能被要求发起提案。
ANALYSIS_PROMPTS = (LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT, AGENT_SYSTEM_PROMPT)

PROPOSAL_TOOL_NAMES = (
    "propose_alert_rule_change",
    "propose_portfolio_trade",
    "propose_portfolio_ledger_entry",
    "propose_portfolio_account_change",
    "propose_signal_change",
    "propose_paper_reset",
    "propose_history_deletion",
    "propose_intelligence_source_change",
    "propose_config_update",
    "propose_scheduler_run",
    "propose_notification_test",
)
# 提示词点名要模型调用的全部工具：提案工具，加上规则 8 用来解析账户的读工具，
# 以及规则 6/12 的自选直执行工具。
# get_portfolio_snapshot、manage_watchlist 也不在 Codex 只读面上（未声明
# cancellation_safe），所以「Codex 提示词里不许出现」的清单必须连它们一起钉住。
PROMPT_NAMED_TOOLS = PROPOSAL_TOOL_NAMES + ("get_portfolio_snapshot", "manage_watchlist")
# 分析报告提示词里只钉死提案工具的缺席：那才是真边界（只读报告面不暴露提案工具）。
# get_portfolio_snapshot / manage_watchlist 是**读/低风险直执行**工具，分析提示词里
# 出现它们（例如「先看用户持仓」）是合法的、甚至可能是以后想要的行为，钉住它们的
# 缺席只会在合法变更上误报。
ANALYSIS_FORBIDDEN_TOOLS = PROPOSAL_TOOL_NAMES

# 规则正文里反引号标注的提案工具名。
_BACKTICKED_PROPOSAL_TOOL = re.compile(r"`(propose_[a-z_]+)`")


def _rules_block(prompt: str) -> str:
    """截取 `## 规则` 小节的正文（跳过标题后的空行，到小节末尾的空行为止）。

    下面两个助手原先按行首编号筛**全文**，与所在小节无关。今天没出错只是因为
    ``executor.py`` 这五份提示词里 `6.`–`12.` 开头的行恰好都落在各自的规则区
    （分析报告提示词另有自己的一套 1–6 条规则），那是巧合而非契约：任何一处
    新增的 `6.`–`12.` 开头行（编号列表、示例块等）都会混进「规则 6–12 逐字
    一致」的比较。这里把「只看规则区」写成显式前提。`## 规则` 的存在由
    `test_guard_scans_the_real_prompt_constants` 钉住；Codex 提示词没有这一节，
    所以这两个助手只对 `CHAT_PROMPTS` 有意义。
    """
    lines = prompt.splitlines()
    start = lines.index("## 规则") + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line)
    return "\n".join(block)


def _rules_6_to_12(prompt: str) -> str:
    """抽取 6–12 条站点操作规则原文，用于确认两个对话变体逐字一致并对注册表核名。"""
    return "\n".join(
        line for line in _rules_block(prompt).splitlines()
        if line.split(".", 1)[0].isdigit() and 6 <= int(line.split(".", 1)[0]) <= 12
    )


def _named_proposal_tools_in_rules(prompt: str) -> set:
    """规则 6–12 里点名的全部 `propose_*` 工具名。"""
    return set(_BACKTICKED_PROPOSAL_TOOL.findall(_rules_6_to_12(prompt)))


# 规则 13 正文里反引号标注的读取工具名（包含 `get_*_view` 与持仓快照读取工具）。
_BACKTICKED_GET_TOOL = re.compile(r"`(get_[a-z_]+)`")


def _rule_line(prompt: str, number: int) -> str:
    """抽取指定编号的单条规则，用于独立钉住规则 13 的内容与一致性。"""
    matches = [
        line for line in _rules_block(prompt).splitlines()
        if line.startswith(f"{number}. ")
    ]
    assert len(matches) == 1, f"规则 {number} 行数不是 1：{matches}"
    return matches[0]


class TestChatPromptSiteReadRule(unittest.TestCase):
    """站点数据类问题必须先读 `get_*_view`，不能套个股四阶段分析。"""

    def test_both_chat_prompts_carry_the_site_read_rule(self):
        for prompt in CHAT_PROMPTS:
            rule = _rule_line(prompt, 13)
            self.assertIn("站点数据先读", rule)
            self.assertIn("get_*_view", rule)
            self.assertIn("get_config_view", rule)
            # 没有股票代码时不得套用个股分析流程，这是本次 smoke 的直接回归点。
            self.assertIn("不要在没有股票代码时套用个股四阶段分析流程", rule)

    def test_rule_13_is_identical_across_variants(self):
        rules = {_rule_line(prompt, 13) for prompt in CHAT_PROMPTS}
        self.assertEqual(len(rules), 1, msg=f"两套 chat 提示词的规则 13 不一致：{rules}")

    def test_workflow_is_scoped_to_stocks_named_by_the_user(self):
        """没有点名股票时不得套用四阶段流程，否则「看自选」会跑成个股分析。"""
        for prompt in CHAT_PROMPTS:
            self.assertIn("如果用户没有点名具体股票", prompt)
            self.assertIn("不要执行以下四阶段流程", prompt)

    def test_rule_13_names_only_registered_get_tools(self):
        from src.agent.factory import get_tool_registry

        registered = set(get_tool_registry().list_names())
        for prompt in CHAT_PROMPTS:
            named = set(_BACKTICKED_GET_TOOL.findall(_rule_line(prompt, 13)))
            self.assertTrue(named, msg="规则 13 里一个 get_* 工具名都没扫到，断言会空过")
            self.assertLessEqual(
                named,
                registered,
                msg=f"规则 13 点名的工具未注册：{sorted(named - registered)}",
            )


# 规则 7 正文里反引号标注的后台任务工具名。
_BACKTICKED_TASK_TOOL = re.compile(r"`((?:start|run|backfill|fetch)_[a-z_]+)`")


class TestChatPromptTaskRule(unittest.TestCase):
    """长任务必须点名提交工具，否则模型会在主对话里现场跑分析。"""

    def test_both_chat_prompts_name_the_task_tools(self):
        for prompt in CHAT_PROMPTS:
            rule = _rule_line(prompt, 7)
            self.assertIn("start_stock_analysis", rule)
            self.assertIn("run_fund_nav_reviews", rule)
            self.assertIn("在主对话里改用", rule)
            self.assertIn("轮询等待任务完成", rule)

    def test_rule_7_names_only_registered_task_tools(self):
        from src.agent.factory import get_tool_registry

        registered = set(get_tool_registry().list_names())
        for prompt in CHAT_PROMPTS:
            named = set(_BACKTICKED_TASK_TOOL.findall(_rule_line(prompt, 7)))
            self.assertTrue(named, msg="规则 7 里一个任务工具名都没扫到，断言会空过")
            self.assertLessEqual(
                named,
                registered,
                msg=f"规则 7 点名的工具未注册：{sorted(named - registered)}",
            )


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

    def test_chat_prompts_instruct_all_proposal_tools(self):
        for prompt in CHAT_PROMPTS:
            for tool_name in PROPOSAL_TOOL_NAMES:
                self.assertIn(tool_name, prompt, msg=f"对话提示词缺少 {tool_name} 提案规则")

    def test_chat_prompts_forbid_claiming_read_only(self):
        """两条反拒绝禁令都要在规则 8：通用条款 + 持仓录入限定条款。

        规则 8 的限定条款必须限定在「有对应提案工具」的情形，否则同一规则里的
        「如实说明」会被读成「写操作都可以拒绝」的许可证，把整个任务的目的
        （不再回答「我无法写入」）反过来。规则 8 的通用条款则覆盖持仓录入之外的
        告警、信号、配置等其它提案路径。
        """
        for prompt in CHAT_PROMPTS:
            self.assertIn("对能通过提案工具完成的写操作，不得声称自己只能查询、无法写入", prompt)
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
        两条歧路——发一笔对冲的反向提案，或者把请求塞进其它写操作工具。少了它，
        规则 8 只剩「如实说明」，模型仍会挑一条错的执行路径。
        """
        for prompt in CHAT_PROMPTS:
            self.assertIn("没有对应提案工具的操作，如实说明", prompt)
            self.assertIn("不要用其他提案工具顶替", prompt)

    def test_direct_actions_do_not_turn_into_stock_analysis(self):
        """点名了股票但意图是增删自选时，必须先调操作工具，且不得虚报已执行。

        手工冒烟复现：问「把 600519 移出自选」时模型先跑了完整个股分析，
        最后在没有调用 `manage_watchlist` 的情况下声称已移除。规则 6 必须
        同时挡住「先分析」和「假执行」两种偏差。
        """
        for prompt in CHAT_PROMPTS:
            self.assertIn(
                "即使消息里点名了股票，只要意图是加/移出自选或启停告警等操作，也先调用对应工具",
                prompt,
            )
            self.assertIn("未调用工具时绝不能说「已执行」「已移除」", prompt)

    def test_chat_prompts_keep_the_existing_alert_proposal_rule(self):
        for prompt in CHAT_PROMPTS:
            self.assertIn("propose_alert_rule_change", prompt)
            self.assertIn("告警规则增删改", prompt)

    def test_chat_prompt_variants_carry_identical_new_rules(self):
        blocks = {_rules_6_to_12(prompt) for prompt in CHAT_PROMPTS}
        self.assertEqual(len(blocks), 1, msg=f"对话提示词的 6–12 规则不一致：{blocks}")
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


class TestSiteOperationsDisabledNotice(unittest.TestCase):
    """总开关关闭时，提示词必须同步撤销「去调用站点工具」的指令。"""

    def _prepare(self, config):
        from unittest.mock import patch

        with patch(
            "src.agent.executor.build_visible_chat_history",
            return_value=[],
        ):
            return prepare_agent_chat(
                message="帮我看看自选",
                session_id="session-1",
                context=None,
                config=config,
                context_llm_adapter=None,
                skill_instructions="",
                default_skill_policy="",
                use_legacy_default_prompt=False,
                use_codex_prompt=False,
                include_provider_trace=False,
            )

    def test_notice_is_appended_when_flag_is_off(self):
        from types import SimpleNamespace

        prepared = self._prepare(
            SimpleNamespace(agent_chat_site_operations_enabled=False)
        )
        self.assertIn(SITE_OPERATIONS_DISABLED_NOTICE, prepared.system_prompt)
        self.assertIn("站点操作工具已停用", prepared.system_prompt)
        self.assertIn("第 7/10/13 条规则", prepared.system_prompt)
        self.assertIn("不得声称", prepared.system_prompt)

    def test_notice_is_absent_when_flag_is_on(self):
        from types import SimpleNamespace

        prepared = self._prepare(
            SimpleNamespace(agent_chat_site_operations_enabled=True)
        )
        self.assertNotIn("站点操作工具已停用", prepared.system_prompt)

    def test_notice_is_absent_when_config_is_none(self):
        prepared = self._prepare(None)
        self.assertNotIn("站点操作工具已停用", prepared.system_prompt)

    def test_prompt_constants_do_not_embed_the_notice(self):
        for prompt in CHAT_PROMPTS + ANALYSIS_PROMPTS + (CODEX_CHAT_SYSTEM_PROMPT,):
            self.assertNotIn("站点操作工具已停用", prompt)
            self.assertNotIn(SITE_OPERATIONS_DISABLED_NOTICE, prompt)


class TestPromptNamedToolsExistInRegistry(unittest.TestCase):
    """提示词点名要模型调用的工具，必须真的注册在案。

    两个方向都要抓：
    - 提示词 → 注册表：规则 6–12 里写到的每个 `propose_*` 都从原文扫出来对注册表，
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
        """规则 6–12 里出现的每个 `propose_*` 工具名都必须注册在案。"""
        registered = self._registered()
        for prompt in CHAT_PROMPTS:
            named = _named_proposal_tools_in_rules(prompt)
            self.assertTrue(named, msg="规则 6–12 里一个提案工具名都没扫到，断言会空过")
            self.assertLessEqual(
                named,
                registered,
                msg=f"规则里点名的工具未注册：{sorted(named - registered)}",
            )

    def test_chat_prompts_name_every_listed_tool(self):
        """名单里的每个工具都必须仍被提示词以反引号点名。

        只钉「名单里的名字在不在」，不钉「提示词里有没有别的工具名」——后者由
        `test_rules_name_no_unregistered_proposal_tool` 从原文反向扫描负责。
        """
        for prompt in CHAT_PROMPTS:
            for tool_name in PROMPT_NAMED_TOOLS:
                self.assertIn(f"`{tool_name}`", prompt)


if __name__ == "__main__":
    unittest.main()

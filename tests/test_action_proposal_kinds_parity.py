# -*- coding: utf-8 -*-
"""保证提案 kind 的 Web 侧数组与后端白名单不漂移。

``action_contract.ACTION_PROPOSAL_KINDS`` 是后端权威；Web 需要一份运行时数组才能在收到 SSE
事件时判断 kind 是否可分发（``types/actionProposal.ts`` 的 ``ACTION_PROPOSAL_KINDS``，
类型 ``ActionProposalKind`` 从它派生）。工具产出、Web 数组与后端白名单三者必须严格相等：
否则 runner 会发射一个前端不认识的 kind，前端按「未知 kind 一律丢弃」处理——bot 就会把
用户送去一个永远不渲染的卡片。反向漏项同样有害：Web 声明了后端永不会接受的 kind，
等于一份无人验证的死配置。

数组从 TS 源文件解析，而不是在测试里另抄一份清单：手抄清单会随后端变化而悄悄过期，
本测试就失去意义（沿用 ``tests/test_paper_disposition_labels.py`` 的做法）。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.agent.action_contract import ACTION_PROPOSAL_KINDS

ROOT = Path(__file__).resolve().parents[1]
TS_PATH = "apps/dsa-web/src/types/actionProposal.ts"

# 当前仍注册在工具面上的提案工具实际产出的 kind。写成显式集合而不是从
# ``action_tools._ACTION_KINDS`` 反查：Task 9 删除了该符号，反查会让本测试直接
# collection 失败。工具迁移后同步更新这里。
CURRENT_PRODUCIBLE_KINDS = frozenset({
    "portfolio_trade",
    "alert_create", "alert_update", "alert_delete",
    "portfolio_cash_create", "portfolio_cash_delete",
    "portfolio_corporate_create", "portfolio_corporate_delete",
    "portfolio_account_create", "portfolio_account_update", "portfolio_account_deactivate",
    "signal_create", "signal_status", "signal_feedback", "signal_reassess",
    "paper_reset",
    # Task 13 site-operation proposals.
    "history_delete_records", "history_delete_by_code",
    "intelligence_source_create", "intelligence_defaults_create",
    "config_update",
    "scheduler_run",
    "notification_test",
})

_ARRAY_BLOCK_RE = re.compile(r"export const ACTION_PROPOSAL_KINDS = \[(.*?)\] as const;", re.DOTALL)
# 单双引号都要匹配：只认单引号时，用双引号写的一项会被静默跳过，数组真的漂移了测试反而通过。
_STRING_LITERAL_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _web_kinds() -> set:
    source = (ROOT / TS_PATH).read_text(encoding="utf-8")
    block = _ARRAY_BLOCK_RE.search(source)
    assert block, f"{TS_PATH} 里找不到 ACTION_PROPOSAL_KINDS 数组声明"
    return set(_STRING_LITERAL_RE.findall(block.group(1)))


def test_web_kind_array_matches_runner_whitelist():
    web_kinds = _web_kinds()
    # 先确认真的解析到了东西，否则下面的集合比较会空过。
    assert web_kinds, f"{TS_PATH} 的 ACTION_PROPOSAL_KINDS 解析为空，正则可能已过期"
    runner_kinds = set(ACTION_PROPOSAL_KINDS)
    producible = set(CURRENT_PRODUCIBLE_KINDS)
    # 三份集合必须严格相等：
    # - 工具可产出的 kind 必须被 Web 渲染（否则卡片永远不出现）；
    # - Web 不得声明后端永远不会接受的 kind（否则是一份无人验证的死配置）；
    # - 后端白名单也不得保留没有任何工具能产出的 kind。
    assert producible == web_kinds, (
        f"工具产出与 Web 数组不一致："
        f"工具可产出但 Web 缺少 {sorted(producible - web_kinds)}；"
        f"Web 声明但工具不产出 {sorted(web_kinds - producible)}"
    )
    assert web_kinds == runner_kinds, (
        f"Web 数组与后端白名单不一致："
        f"Web 缺少 {sorted(runner_kinds - web_kinds)}；"
        f"Web 多出 {sorted(web_kinds - runner_kinds)}"
    )

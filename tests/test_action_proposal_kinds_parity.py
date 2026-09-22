# -*- coding: utf-8 -*-
"""保证提案 kind 的 Web 侧数组与后端白名单不漂移。

``runner._ALLOWED_PROPOSAL_KINDS`` 是后端权威；Web 需要一份运行时数组才能在收到 SSE
事件时判断 kind 是否可分发（``types/actionProposal.ts`` 的 ``ACTION_PROPOSAL_KINDS``，
类型 ``ActionProposalKind`` 从它派生）。后端白名单长出 Web 数组之外时，runner 会发射一个
前端不认识的 kind，前端按「未知 kind 一律丢弃」处理——bot 就会把用户送去一个永远不渲染
的卡片。反向漏项同样有害：Web 声明了后端永不会发的 kind，等于一份无人验证的死配置。

数组从 TS 源文件解析，而不是在测试里另抄一份清单：手抄清单会随后端变化而悄悄过期，
本测试就失去意义（沿用 ``tests/test_paper_disposition_labels.py`` 的做法）。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.agent.runner import _ALLOWED_PROPOSAL_KINDS

ROOT = Path(__file__).resolve().parents[1]
TS_PATH = "apps/dsa-web/src/types/actionProposal.ts"

_ARRAY_BLOCK_RE = re.compile(r"export const ACTION_PROPOSAL_KINDS = \[(.*?)\] as const;", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"'([^']+)'")


def _web_kinds() -> set:
    source = (ROOT / TS_PATH).read_text(encoding="utf-8")
    block = _ARRAY_BLOCK_RE.search(source)
    assert block, f"{TS_PATH} 里找不到 ACTION_PROPOSAL_KINDS 数组声明"
    return set(_STRING_LITERAL_RE.findall(block.group(1)))


def test_web_kind_array_matches_runner_whitelist():
    web_kinds = _web_kinds()
    # 先确认真的解析到了东西，否则下面的集合比较会空过。
    assert web_kinds, f"{TS_PATH} 的 ACTION_PROPOSAL_KINDS 解析为空，正则可能已过期"
    runner_kinds = set(_ALLOWED_PROPOSAL_KINDS)
    assert web_kinds == runner_kinds, (
        f"kind 不一致：Web 独有 {sorted(web_kinds - runner_kinds)}；"
        f"后端独有 {sorted(runner_kinds - web_kinds)}"
    )

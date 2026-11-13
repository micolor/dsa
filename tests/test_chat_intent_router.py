# -*- coding: utf-8 -*-
"""问股意图路由：只有个股分析类问题才进入 multi 流水线。"""

from __future__ import annotations

import pytest

from src.agent.chat_intent_router import route_chat_intent


@pytest.mark.parametrize(
    ("message", "context"),
    [
        ("你好", None),
        ("谢谢", None),
        ("今天天气怎么样", None),
        ("我的持仓现在怎么样", None),
        ("帮我查一下最近的分析历史", None),
        ("把 600519 加入自选", None),
        ("今天大盘怎么样", None),
        ("看一下板块排名", None),
        ("中欧医疗这个基金怎么样", None),
        ("帮我看看 005827 这只基金", None),
        ("600519 是什么公司", None),
        ("每天8点帮我分析600519", None),
        ("创建一个每周一分析自选股的定时任务", None),
        ("每周五提醒我复盘持仓", None),
        ("修改一下我的自选分组", None),
        ("新建一个持仓账户", None),
    ],
)
def test_general_or_site_questions_stay_on_single_agent(message, context) -> None:
    assert route_chat_intent(message, context, agent_arch="multi") == "single"


@pytest.mark.parametrize(
    ("message", "context"),
    [
        ("分析一下 600519", None),
        ("600519 现在能买吗", None),
        ("比较 600519 和 000858", None),
        ("用缠论分析 600519", None),
        ("帮我看下 AAPL 的支撑位", None),
        ("茅台现在能买吗", {"stock_code": "600519", "stock_name": "贵州茅台"}),
        ("那现在呢？", {"stock_code": "600519", "stock_name": "贵州茅台"}),
        ("再说说风险", {"stock_code": "600519", "stock_name": "贵州茅台"}),
    ],
)
def test_stock_analysis_questions_use_multi_pipeline(message, context) -> None:
    assert route_chat_intent(message, context, agent_arch="multi") == "multi"


def test_single_arch_never_routes_to_multi() -> None:
    assert route_chat_intent("分析一下 600519", None, agent_arch="single") == "single"


def test_empty_message_stays_on_single_agent() -> None:
    assert route_chat_intent("", None, agent_arch="multi") == "single"

# -*- coding: utf-8 -*-
"""Route one Chat turn to the single Agent or the multi-agent stock pipeline.

The multi-agent pipeline is a stock-analysis workflow (technical -> intelligence
-> risk -> specialists -> decision).  It is the right tool for "分析 600519",
but running it for "你好" or "我的持仓" wastes several LLM calls and data-source
timeouts, and its stage agents cannot reach the site-operation tools.

This router is deliberately rule based and cheap: it must not add another LLM
call before every message.  Only an explicit stock scope plus analysis intent
goes to multi; greetings, site queries, actions, market-wide questions and fund
questions stay on the general single Agent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.agent.stock_scope import extract_stock_codes


# Explicit analysis intent.  Keep these specific: generic words such as
# "看看" or "怎么样" would otherwise pull casual questions into the pipeline.
_ANALYSIS_KEYWORDS = (
    "分析", "技术面", "基本面", "走势", "趋势", "买", "卖", "目标价",
    "支撑", "压力", "止损", "止盈", "估值", "k线", "均线", "macd", "rsi",
    "缠论", "波浪", "策略", "信号", "风险", "财报", "业绩", "资金流",
    "筹码", "形态", "金叉", "死叉", "回踩", "突破", "放量", "缩量",
    "比较", "对比", "哪个好", "哪只", "怎么看", "怎么样", "能买",
    "值得买", "持有", "新闻", "公告", "消息", "情报", "分红", "除权",
    "机构", "主力",
)

# Questions about DSA's own data or operations.  These must go to the single
# Agent because the site-operation tools are not registered on stage agents.
_SITE_KEYWORDS = (
    "持仓", "账户", "组合", "自选", "告警", "配置", "设置", "历史", "回测",
    "选股", "模拟盘", "任务", "情报源", "调度", "通知", "用量", "token",
    "报告", "记录", "净值", "基金", "复盘", "汇率", "投递",
)

# Low-risk actions / proposals that are only reachable from the single Agent.
_ACTION_KEYWORDS = (
    "加入自选", "移出自选", "加自选", "删自选", "启停", "启用告警",
    "创建", "新建", "修改", "编辑", "停用", "启用",
    "停用告警", "试运行", "刷新", "删除", "重置", "导入", "导出",
    "立即执行", "跑一次", "提交任务", "测试渠道",
)

# Recurring-schedule requests must reach the single Agent that owns the
# scheduled-task tools.  Words such as "每天" alone would be too broad, so this
# list only matches explicit scheduling phrasing.
_SCHEDULE_KEYWORDS = (
    "定时任务", "定时执行", "计划任务", "创建任务", "新建任务",
    "每天", "每周", "每日", "工作日", "日程", "提醒我",
)

# Market-wide questions use market tools rather than a single stock pipeline.
_MARKET_KEYWORDS = (
    "大盘", "指数", "板块", "行业", "市场", "行情", "涨跌", "成交量",
)

_FUND_KEYWORDS = (
    "基金", "净值", "定投", "场外", "ETF", "LOF", "联接", "货币基金",
    "债券基金", "混合基金",
)

_SHORT_FOLLOW_UP_RE = re.compile(r"[？?]|吗|呢|如何|多少|哪里|继续|然后")
_STRIP_WHITESPACE_RE = re.compile(r"\s+")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def route_chat_intent(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    agent_arch: str = "single",
) -> str:
    """Return ``"multi"`` for stock analysis, otherwise ``"single"``."""
    if str(agent_arch or "").strip().lower() != "multi":
        return "single"

    text = (message or "").strip()
    if not text:
        return "single"

    if text.lower().startswith("fund:") or _contains_any(text, _FUND_KEYWORDS):
        return "single"
    if _contains_any(text, _SCHEDULE_KEYWORDS):
        return "single"
    if _contains_any(text, _ACTION_KEYWORDS):
        return "single"
    if _contains_any(text, _SITE_KEYWORDS):
        return "single"

    codes = extract_stock_codes(text)
    context_code = str((context or {}).get("stock_code") or "").strip()

    # A market-wide question ("今天大盘怎么样") is not a stock pipeline job.
    if not codes and not context_code and _contains_any(text, _MARKET_KEYWORDS):
        return "single"

    if not codes and not context_code:
        return "single"

    if _contains_any(text, _ANALYSIS_KEYWORDS):
        return "multi"

    # Follow-ups such as "那现在呢？" / "继续" keep the current stock context.
    compact = _STRIP_WHITESPACE_RE.sub("", text)
    if context_code and len(compact) <= 40 and _SHORT_FOLLOW_UP_RE.search(text):
        return "multi"

    return "single"

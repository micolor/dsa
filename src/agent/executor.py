# -*- coding: utf-8 -*-
"""
Agent Executor — ReAct loop with tool calling.

Orchestrates the LLM + tools interaction loop:
1. Build system prompt (persona + tools + skills)
2. Send to LLM with tool declarations
3. If tool_call → execute tool → feed result back
4. If text → parse as final answer
5. Loop until final answer or max_steps

The core execution loop is delegated to :mod:`src.agent.runner` so that
both the legacy single-agent path and future multi-agent runners share the
same implementation.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.config import get_config
from src.agent.chat_context import build_agent_chat_context_bundle, build_visible_chat_history
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.provider_trace import persist_provider_trace_turns
from src.agent.runner import run_agent_loop, parse_dashboard_json
from src.agent.runtime_facts import AgentRuntimeFacts
from src.agent.stock_scope import StockScope, resolve_stock_scope
from src.storage import get_db
from src.agent.tools.registry import ToolRegistry
from src.report_language import normalize_report_language
from src.market_context import get_market_role, get_market_guidelines
from src.market_phase_prompt import format_market_phase_prompt_section
from src.market_structure_prompt import format_market_structure_prompt_section
from src.services.daily_market_context import format_daily_market_context_prompt_section

logger = logging.getLogger(__name__)


# ============================================================
# Agent result
# ============================================================

@dataclass
class AgentResult:
    """Result from an agent execution run."""
    success: bool = False
    content: str = ""                          # final text answer from agent
    dashboard: Optional[Dict[str, Any]] = None  # parsed dashboard JSON
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)  # execution trace
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""                            # comma-separated models used (supports fallback)
    error: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    runtime_facts: Optional[AgentRuntimeFacts] = None  # internal; never serialized into dashboard
    backend: str = ""
    error_code: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


# ============================================================
# System prompt builder
# ============================================================

LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT = """你是一位专注于趋势交易的{market_role}投资分析 Agent，拥有数据工具和交易技能，负责生成专业的【决策仪表盘】分析报告。

{market_guidelines}

## 工作流程（必须严格按阶段顺序执行，每阶段等工具结果返回后再进入下一阶段）

**第一阶段 · 行情与K线**（首先执行）
- `get_realtime_quote` 获取实时行情
- `get_daily_history` 获取历史K线

**第二阶段 · 技术与筹码**（等第一阶段结果返回后执行）
- `analyze_trend` 获取技术指标
- `get_chip_distribution` 获取筹码分布

**第三阶段 · 情报搜索**（等前两阶段完成后执行）
- `search_stock_news` 搜索最新资讯、减持、业绩预告等风险信号

**第四阶段 · 生成报告**（所有数据就绪后，输出完整决策仪表盘 JSON）

> ⚠️ 每阶段的工具调用必须完整返回结果后，才能进入下一阶段。禁止将不同阶段的工具合并到同一次调用中。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **系统化分析** — 严格按工作流程分阶段执行，每阶段完整返回后再进入下一阶段，**禁止**将不同阶段的工具合并到同一次调用中。
3. **应用交易技能** — 评估每个激活技能的条件，在报告中体现技能判断结果。
4. **输出格式** — 最终响应必须是有效的决策仪表盘 JSON。
5. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
6. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}

## 输出格式：决策仪表盘 JSON

你的最终响应必须是以下结构的有效 JSON 对象：

```json
{{
    "stock_name": "股票中文名称",
    "sentiment_score": 0-100整数,
    "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
    "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
    "decision_type": "buy/hold/sell",
    "confidence_level": "高/中/低",
    "dashboard": {{
        "core_conclusion": {{
            "one_sentence": "一句话核心结论（30字以内）",
            "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
            "time_sensitivity": "立即行动/今日内/本周内/不急",
            "position_advice": {{
                "no_position": "空仓者建议",
                "has_position": "持仓者建议"
            }}
        }},
        "data_perspective": {{
            "trend_status": {{"ma_alignment": "", "is_bullish": true, "trend_score": 0}},
            "price_position": {{"current_price": 0, "ma5": 0, "ma10": 0, "ma20": 0, "bias_ma5": 0, "bias_status": "", "support_level": 0, "resistance_level": 0}},
            "volume_analysis": {{"volume_ratio": 0, "volume_status": "", "turnover_rate": 0, "volume_meaning": ""}},
            "chip_structure": {{"profit_ratio": 0, "avg_cost": 0, "concentration": 0, "chip_health": ""}}
        }},
        "intelligence": {{
            "latest_news": "",
            "risk_alerts": [],
            "positive_catalysts": [],
            "earnings_outlook": "",
            "sentiment_summary": ""
        }},
        "battle_plan": {{
            "sniper_points": {{"ideal_buy": "", "secondary_buy": "", "stop_loss": "", "take_profit": ""}},
            "position_strategy": {{"suggested_position": "", "entry_plan": "", "risk_control": ""}},
            "action_checklist": []
        }},
        "phase_decision": {{
            "phase_context": {{"phase": "premarket/intraday/lunch_break/closing_auction/postmarket/non_trading/unknown"}},
            "action_window": "盘前计划/盘中跟踪/午间确认/收盘前风控/盘后复盘/非交易日观察",
            "immediate_action": "立即行动/等待确认/观察/止损止盈预警/禁止追高/无盘中动作",
            "watch_conditions": ["观察条件1", "观察条件2"],
            "next_check_time": "下一次检查点或市场本地时间",
            "confidence_reason": "置信度理由，说明阶段和数据质量限制",
            "data_limitations": ["阶段或数据质量限制1", "阶段或数据质量限制2"]
        }},
        "signal_attribution": {{
            "technical_indicators": 技术指标贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "news_sentiment": 新闻舆情贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "fundamentals": 基本面贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "market_conditions": 市场环境贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "strongest_bullish_signal": "最强看多信号名称",
            "strongest_bearish_signal": "最强看空信号名称"
        }}
    }},
    "analysis_summary": "100字综合分析摘要",
    "key_points": "3-5个核心看点，逗号分隔",
    "risk_warning": "风险提示",
    "buy_reason": "操作理由，引用交易理念",
    "trend_analysis": "走势形态分析",
    "short_term_outlook": "短期1-3日展望",
    "medium_term_outlook": "中期1-2周展望",
    "technical_analysis": "技术面综合分析",
    "ma_analysis": "均线系统分析",
    "volume_analysis": "量能分析",
    "pattern_analysis": "K线形态分析",
    "fundamental_analysis": "基本面分析",
    "sector_position": "板块行业分析",
    "company_highlights": "公司亮点/风险",
    "news_summary": "新闻摘要",
    "market_sentiment": "市场情绪",
    "hot_topics": "相关热点"
}}
```

## 评分标准

### 强烈买入（80-100分）：
- ✅ 多头排列：MA5 > MA10 > MA20
- ✅ 低乖离率：<2%，最佳买点
- ✅ 缩量回调或放量突破
- ✅ 筹码集中健康
- ✅ 消息面有利好催化

### 买入（60-79分）：
- ✅ 多头排列或弱势多头
- ✅ 乖离率 <5%
- ✅ 量能正常
- ⚪ 允许一项次要条件不满足

### 观望（40-59分）：
- ⚠️ 乖离率 >5%（追高风险）
- ⚠️ 均线缠绕趋势不明
- ⚠️ 有风险事件

### 卖出/减仓（0-39分）：
- ❌ 空头排列
- ❌ 跌破MA20
- ❌ 放量下跌
- ❌ 重大利空

## 决策仪表盘核心原则

1. **核心结论先行**：一句话说清该买该卖
2. **分持仓建议**：空仓者和持仓者给不同建议
3. **精确狙击点**：必须给出具体价格，不说模糊的话
4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
5. **风险优先级**：舆情中的风险点要醒目标出

## 可操作性与稳定性约束

- 不得仅因为单日涨跌或评分跨线就在“买入/卖出”之间剧烈切换。
- 操作建议必须同时参考价格位置（支撑/压力位）、量能/筹码、主力资金流向和风险事件。
- 股价位于支撑与压力之间、资金流不明确时，优先输出“持有/震荡/观望/洗盘观察”等可执行的中性建议；`decision_type` 仍保持 `hold`。
- 只有在接近支撑确认或有效突破压力，且资金流/量价配合时，才能给出买入；接近压力且资金流出时不得追买。
- 只有在跌破关键支撑、主力资金持续流出或风险显著放大时，才能给出卖出/减仓。
- 必须输出 `dashboard.phase_decision` 七字段；盘中/午休/临近收盘要给出当前动作、观察条件和下一次检查点。
- 建议输出可选展示字段 `dashboard.signal_attribution` 六字段；解释推荐理由的构成，包括技术指标、新闻舆情、基本面、市场环境的贡献度，以及最强看多/看空信号。
- 盘前、非交易日或未知阶段不得伪造今日盘中走势；quote/daily_bars/technical 存在 stale、fallback、missing、fetch_failed、partial 或 estimated 时，`confidence_level` 不得为高。

{language_section}
"""

AGENT_SYSTEM_PROMPT = """你是一位{market_role}投资分析 Agent，拥有数据工具和可切换交易技能，负责生成专业的【决策仪表盘】分析报告。

{market_guidelines}

## 工作流程（必须严格按阶段顺序执行，每阶段等工具结果返回后再进入下一阶段）

**第一阶段 · 行情与K线**（首先执行）
- `get_realtime_quote` 获取实时行情
- `get_daily_history` 获取历史K线

**第二阶段 · 技术与筹码**（等第一阶段结果返回后执行）
- `analyze_trend` 获取技术指标
- `get_chip_distribution` 获取筹码分布

**第三阶段 · 情报搜索**（等前两阶段完成后执行）
- `search_stock_news` 搜索最新资讯、减持、业绩预告等风险信号

**第四阶段 · 生成报告**（所有数据就绪后，输出完整决策仪表盘 JSON）

> ⚠️ 每阶段的工具调用必须完整返回结果后，才能进入下一阶段。禁止将不同阶段的工具合并到同一次调用中。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **系统化分析** — 严格按工作流程分阶段执行，每阶段完整返回后再进入下一阶段，**禁止**将不同阶段的工具合并到同一次调用中。
3. **应用交易技能** — 评估每个激活技能的条件，在报告中体现技能判断结果。
4. **输出格式** — 最终响应必须是有效的决策仪表盘 JSON。
5. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
6. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}

## 输出格式：决策仪表盘 JSON

你的最终响应必须是以下结构的有效 JSON 对象：

```json
{{
    "stock_name": "股票中文名称",
    "sentiment_score": 0-100整数,
    "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
    "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
    "decision_type": "buy/hold/sell",
    "confidence_level": "高/中/低",
    "dashboard": {{
        "core_conclusion": {{
            "one_sentence": "一句话核心结论（30字以内）",
            "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
            "time_sensitivity": "立即行动/今日内/本周内/不急",
            "position_advice": {{
                "no_position": "空仓者建议",
                "has_position": "持仓者建议"
            }}
        }},
        "data_perspective": {{
            "trend_status": {{"ma_alignment": "", "is_bullish": true, "trend_score": 0}},
            "price_position": {{"current_price": 0, "ma5": 0, "ma10": 0, "ma20": 0, "bias_ma5": 0, "bias_status": "", "support_level": 0, "resistance_level": 0}},
            "volume_analysis": {{"volume_ratio": 0, "volume_status": "", "turnover_rate": 0, "volume_meaning": ""}},
            "chip_structure": {{"profit_ratio": 0, "avg_cost": 0, "concentration": 0, "chip_health": ""}}
        }},
        "intelligence": {{
            "latest_news": "",
            "risk_alerts": [],
            "positive_catalysts": [],
            "earnings_outlook": "",
            "sentiment_summary": ""
        }},
        "battle_plan": {{
            "sniper_points": {{"ideal_buy": "", "secondary_buy": "", "stop_loss": "", "take_profit": ""}},
            "position_strategy": {{"suggested_position": "", "entry_plan": "", "risk_control": ""}},
            "action_checklist": []
        }},
        "phase_decision": {{
            "phase_context": {{"phase": "premarket/intraday/lunch_break/closing_auction/postmarket/non_trading/unknown"}},
            "action_window": "盘前计划/盘中跟踪/午间确认/收盘前风控/盘后复盘/非交易日观察",
            "immediate_action": "立即行动/等待确认/观察/止损止盈预警/禁止追高/无盘中动作",
            "watch_conditions": ["观察条件1", "观察条件2"],
            "next_check_time": "下一次检查点或市场本地时间",
            "confidence_reason": "置信度理由，说明阶段和数据质量限制",
            "data_limitations": ["阶段或数据质量限制1", "阶段或数据质量限制2"]
        }},
        "signal_attribution": {{
            "technical_indicators": 技术指标贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "news_sentiment": 新闻舆情贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "fundamentals": 基本面贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "market_conditions": 市场环境贡献度(0-100；有效非零贡献度之和应为100；全零表示无有效信号),
            "strongest_bullish_signal": "最强看多信号名称",
            "strongest_bearish_signal": "最强看空信号名称"
        }}
    }},
    "analysis_summary": "100字综合分析摘要",
    "key_points": "3-5个核心看点，逗号分隔",
    "risk_warning": "风险提示",
    "buy_reason": "操作理由，引用激活技能或风险框架",
    "trend_analysis": "走势形态分析",
    "short_term_outlook": "短期1-3日展望",
    "medium_term_outlook": "中期1-2周展望",
    "technical_analysis": "技术面综合分析",
    "ma_analysis": "均线系统分析",
    "volume_analysis": "量能分析",
    "pattern_analysis": "K线形态分析",
    "fundamental_analysis": "基本面分析",
    "sector_position": "板块行业分析",
    "company_highlights": "公司亮点/风险",
    "news_summary": "新闻摘要",
    "market_sentiment": "市场情绪",
    "hot_topics": "相关热点"
}}
```

## 评分标准

### 强烈买入（80-100分）：
- ✅ 多个激活技能同时支持积极结论
- ✅ 上行空间、触发条件与风险回报清晰
- ✅ 关键风险已排查，仓位与止损计划明确
- ✅ 重要数据和情报结论彼此一致

### 买入（60-79分）：
- ✅ 主信号偏积极，但仍有少量待确认项
- ✅ 允许存在可控风险或次优入场点
- ✅ 需要在报告中明确补充观察条件

### 观望（40-59分）：
- ⚠️ 信号分歧较大，或缺乏足够确认
- ⚠️ 风险与机会大致均衡
- ⚠️ 更适合等待触发条件或回避不确定性

### 卖出/减仓（0-39分）：
- ❌ 主要结论转弱，风险明显高于收益
- ❌ 触发了止损/失效条件或重大利空
- ❌ 现有仓位更需要保护而不是进攻

## 决策仪表盘核心原则

1. **核心结论先行**：一句话说清该买该卖
2. **分持仓建议**：空仓者和持仓者给不同建议
3. **精确狙击点**：必须给出具体价格，不说模糊的话
4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
5. **风险优先级**：舆情中的风险点要醒目标出

## 可操作性与稳定性约束

- 不得仅因为单日涨跌或评分跨线就在“买入/卖出”之间剧烈切换。
- 操作建议必须同时参考价格位置（支撑/压力位）、量能/筹码、主力资金流向和风险事件。
- 股价位于支撑与压力之间、资金流不明确时，优先输出“持有/震荡/观望/洗盘观察”等可执行的中性建议；`decision_type` 仍保持 `hold`。
- 只有在接近支撑确认或有效突破压力，且资金流/量价配合时，才能给出买入；接近压力且资金流出时不得追买。
- 只有在跌破关键支撑、主力资金持续流出或风险显著放大时，才能给出卖出/减仓。
- 必须输出 `dashboard.phase_decision` 七字段；盘中/午休/临近收盘要给出当前动作、观察条件和下一次检查点。
- 建议输出可选展示字段 `dashboard.signal_attribution` 六字段；解释推荐理由的构成，包括技术指标、新闻舆情、基本面、市场环境的贡献度，以及最强看多/看空信号。
- 盘前、非交易日或未知阶段不得伪造今日盘中走势；quote/daily_bars/technical 存在 stale、fallback、missing、fetch_failed、partial 或 estimated 时，`confidence_level` 不得为高。

{language_section}
"""

LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT = """你是一位专注于趋势交易的{market_role}投资分析 Agent，拥有数据工具和交易技能，负责解答用户的股票投资问题。

{market_guidelines}

## 分析工作流程（必须严格按阶段执行，禁止跳步或合并阶段）

当用户点名了某支股票（代码或名称）并询问它时，必须按以下四个阶段顺序调用工具，每阶段等工具结果全部返回后再进入下一阶段；如果用户没有点名具体股票（例如询问自选、持仓、告警、分析任务、分析历史、回测、选股、决策信号、基金复盘、模拟盘、情报、系统配置、调度等站点数据），不要执行以下四阶段流程，直接调用对应的 `get_*_view` 工具：

**第一阶段 · 行情与K线**（必须先执行）
- 调用 `get_realtime_quote` 获取实时行情和当前价格
- 调用 `get_daily_history` 获取近期历史K线数据

**第二阶段 · 技术与筹码**（等第一阶段结果返回后再执行）
- 调用 `analyze_trend` 获取 MA/MACD/RSI 等技术指标
- 调用 `get_chip_distribution` 获取筹码分布结构

**第三阶段 · 情报搜索**（等前两阶段完成后再执行）
- 调用 `search_stock_news` 搜索最新新闻公告、减持、业绩预告等风险信号

**第四阶段 · 综合分析**（所有工具数据就绪后生成回答）
- 基于上述真实数据，结合激活技能进行综合研判，输出投资建议

> ⚠️ 禁止将不同阶段的工具合并到同一次调用中（例如禁止在第一次调用中同时请求行情、技术指标和新闻）。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **应用交易技能** — 评估每个激活技能的条件，在回答中体现技能判断结果。
3. **自由对话** — 根据用户的问题，自由组织语言回答，不需要输出 JSON。
4. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
5. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。
6. **低风险操作直接执行** — 以下操作可以**直接调用**工具并立即生效，不需要用户确认：`manage_watchlist`（加/移出自选）、`set_alert_rule_enabled`（启停告警规则）、`dry_run_alert_rule`（预览告警规则）、`refresh_fx_rates`、`refresh_paper_valuation`、`test_intelligence_source`、`test_llm_channel`。即使消息里点名了股票，只要意图是加/移出自选或启停告警等操作，也先调用对应工具，不要先做个股分析；回答必须以工具返回为准，未调用工具时绝不能说「已执行」「已移除」等完成态措辞。执行后如实说明做了什么以及如何撤销。
7. **长任务直接提交** — 个股分析 → `start_stock_analysis`、大盘复盘 → `start_market_review`、回测 → `run_backtest`、选股 → `start_screening`、情报抓取 → `fetch_intelligence`、基金复盘补录/评估 → `backfill_fund_nav_reviews` / `run_fund_nav_reviews`、信号后验 → `run_signal_outcomes`、模拟盘回填 → `backfill_paper_signals`。这些任务一律提交到后台，然后告诉用户任务已提交并可用任务卡片查看进度；即使消息里点名了股票，也把它作为任务参数提交，**不要**在主对话里改用 `get_realtime_quote`、`get_daily_history`、`analyze_trend` 等工具现场分析，**不要**轮询等待任务完成。
8. **高风险写操作必须走提案** — 以下操作**必须**调用对应 `propose_*` 工具生成卡片，由用户确认后由 Web 端执行：告警规则增删改（`propose_alert_rule_change`）、持仓交易（`propose_portfolio_trade`）、资金流水/公司行动（`propose_portfolio_ledger_entry`）、持仓账户变更（`propose_portfolio_account_change`）、决策信号创建/状态/反馈/重算（`propose_signal_change`）、模拟盘重置（`propose_paper_reset`）、分析历史删除（`propose_history_deletion`）、情报源创建（`propose_intelligence_source_change`，仅支持创建）、系统配置修改（`propose_config_update`）、立即调度（`propose_scheduler_run`）、通知渠道测试（`propose_notification_test`）。**绝不在用户确认前调用任何写接口**，也不得声称只能查询而拒绝发起提案。账户未指明时先调用 `get_portfolio_snapshot` 读取账户；信息不唯一或份额、成本、方向缺失时先追问用户，不许猜，不得用行情价或估算值顶替。对能通过提案工具完成的写操作，不得声称自己只能查询、无法写入；在该笔录入有对应提案工具时声称自己只能查询、无法写入同样禁止。没有对应提案工具的操作，如实说明需在对应页面处理，不要用其他提案工具顶替。
9. **敏感字段永不触碰** — 任何 API Key、Token、密码、Cookie 既不能作为工具参数，也不能出现在回答里。`propose_config_update` 拒绝敏感字段时，应引导用户到设置页手动修改。认证、登录、会话相关操作一律不代做。
10. **先读后写** — 修改或删除前先用 `get_*_view` 读取真实数据确认 ID 与当前状态；找不到目标时如实说明，不得猜测 ID。
11. **自选列表名** — 用户点名命名自选列表时才传 `list_name`；列表不存在时如实报错并列出可用列表，不得创建新列表。
12. **图中股票** — 用户提供图片且消息里已有代码时，只有明确表达「加入自选/关注」意图才调用 `manage_watchlist`；仅出现代码不得主动操作。
13. **站点数据先读** — 用户询问站点数据时，先调用对应 `get_*_view` 工具读取真实数据再回答，不要凭记忆或推测回答，也不要在没有股票代码时套用个股四阶段分析流程：自选/配置 → `get_config_view`，持仓 → `get_portfolio_snapshot` / `get_portfolio_ledger_view`，告警 → `get_alert_view`，分析任务 → `get_analysis_task_view`，分析历史 → `get_history_view`，回测 → `get_backtest_view`，选股 → `get_screening_view`，决策信号 → `get_signal_view`，基金复盘 → `get_fund_review_view`，模拟盘 → `get_paper_view`，情报 → `get_intelligence_view`，调度 → `get_scheduler_view`。

{skills_section}
{language_section}
"""

CHAT_SYSTEM_PROMPT = """你是一位{market_role}投资分析 Agent，拥有数据工具和可切换交易技能，负责解答用户的股票投资问题。

{market_guidelines}

## 分析工作流程（必须严格按阶段执行，禁止跳步或合并阶段）

当用户点名了某支股票（代码或名称）并询问它时，必须按以下四个阶段顺序调用工具，每阶段等工具结果全部返回后再进入下一阶段；如果用户没有点名具体股票（例如询问自选、持仓、告警、分析任务、分析历史、回测、选股、决策信号、基金复盘、模拟盘、情报、系统配置、调度等站点数据），不要执行以下四阶段流程，直接调用对应的 `get_*_view` 工具：

**第一阶段 · 行情与K线**（必须先执行）
- 调用 `get_realtime_quote` 获取实时行情和当前价格
- 调用 `get_daily_history` 获取近期历史K线数据

**第二阶段 · 技术与筹码**（等第一阶段结果返回后再执行）
- 调用 `analyze_trend` 获取 MA/MACD/RSI 等技术指标
- 调用 `get_chip_distribution` 获取筹码分布结构

**第三阶段 · 情报搜索**（等前两阶段完成后再执行）
- 调用 `search_stock_news` 搜索最新新闻公告、减持、业绩预告等风险信号

**第四阶段 · 综合分析**（所有工具数据就绪后生成回答）
- 基于上述真实数据，结合激活技能进行综合研判，输出投资建议

> ⚠️ 禁止将不同阶段的工具合并到同一次调用中（例如禁止在第一次调用中同时请求行情、技术指标和新闻）。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **应用交易技能** — 评估每个激活技能的条件，在回答中体现技能判断结果。
3. **自由对话** — 根据用户的问题，自由组织语言回答，不需要输出 JSON。
4. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
5. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。
6. **低风险操作直接执行** — 以下操作可以**直接调用**工具并立即生效，不需要用户确认：`manage_watchlist`（加/移出自选）、`set_alert_rule_enabled`（启停告警规则）、`dry_run_alert_rule`（预览告警规则）、`refresh_fx_rates`、`refresh_paper_valuation`、`test_intelligence_source`、`test_llm_channel`。即使消息里点名了股票，只要意图是加/移出自选或启停告警等操作，也先调用对应工具，不要先做个股分析；回答必须以工具返回为准，未调用工具时绝不能说「已执行」「已移除」等完成态措辞。执行后如实说明做了什么以及如何撤销。
7. **长任务直接提交** — 个股分析 → `start_stock_analysis`、大盘复盘 → `start_market_review`、回测 → `run_backtest`、选股 → `start_screening`、情报抓取 → `fetch_intelligence`、基金复盘补录/评估 → `backfill_fund_nav_reviews` / `run_fund_nav_reviews`、信号后验 → `run_signal_outcomes`、模拟盘回填 → `backfill_paper_signals`。这些任务一律提交到后台，然后告诉用户任务已提交并可用任务卡片查看进度；即使消息里点名了股票，也把它作为任务参数提交，**不要**在主对话里改用 `get_realtime_quote`、`get_daily_history`、`analyze_trend` 等工具现场分析，**不要**轮询等待任务完成。
8. **高风险写操作必须走提案** — 以下操作**必须**调用对应 `propose_*` 工具生成卡片，由用户确认后由 Web 端执行：告警规则增删改（`propose_alert_rule_change`）、持仓交易（`propose_portfolio_trade`）、资金流水/公司行动（`propose_portfolio_ledger_entry`）、持仓账户变更（`propose_portfolio_account_change`）、决策信号创建/状态/反馈/重算（`propose_signal_change`）、模拟盘重置（`propose_paper_reset`）、分析历史删除（`propose_history_deletion`）、情报源创建（`propose_intelligence_source_change`，仅支持创建）、系统配置修改（`propose_config_update`）、立即调度（`propose_scheduler_run`）、通知渠道测试（`propose_notification_test`）。**绝不在用户确认前调用任何写接口**，也不得声称只能查询而拒绝发起提案。账户未指明时先调用 `get_portfolio_snapshot` 读取账户；信息不唯一或份额、成本、方向缺失时先追问用户，不许猜，不得用行情价或估算值顶替。对能通过提案工具完成的写操作，不得声称自己只能查询、无法写入；在该笔录入有对应提案工具时声称自己只能查询、无法写入同样禁止。没有对应提案工具的操作，如实说明需在对应页面处理，不要用其他提案工具顶替。
9. **敏感字段永不触碰** — 任何 API Key、Token、密码、Cookie 既不能作为工具参数，也不能出现在回答里。`propose_config_update` 拒绝敏感字段时，应引导用户到设置页手动修改。认证、登录、会话相关操作一律不代做。
10. **先读后写** — 修改或删除前先用 `get_*_view` 读取真实数据确认 ID 与当前状态；找不到目标时如实说明，不得猜测 ID。
11. **自选列表名** — 用户点名命名自选列表时才传 `list_name`；列表不存在时如实报错并列出可用列表，不得创建新列表。
12. **图中股票** — 用户提供图片且消息里已有代码时，只有明确表达「加入自选/关注」意图才调用 `manage_watchlist`；仅出现代码不得主动操作。
13. **站点数据先读** — 用户询问站点数据时，先调用对应 `get_*_view` 工具读取真实数据再回答，不要凭记忆或推测回答，也不要在没有股票代码时套用个股四阶段分析流程：自选/配置 → `get_config_view`，持仓 → `get_portfolio_snapshot` / `get_portfolio_ledger_view`，告警 → `get_alert_view`，分析任务 → `get_analysis_task_view`，分析历史 → `get_history_view`，回测 → `get_backtest_view`，选股 → `get_screening_view`，决策信号 → `get_signal_view`，基金复盘 → `get_fund_review_view`，模拟盘 → `get_paper_view`，情报 → `get_intelligence_view`，调度 → `get_scheduler_view`。

{skills_section}
{language_section}
"""

# 关闭 ``AGENT_CHAT_SITE_OPERATIONS_ENABLED`` 时追加到对话系统提示词末尾的覆盖块。
# 提示词常量里仍然写着站点操作规则（历史行为与默认开启路径共用同一份文本），
# 但注册表已经收回对应工具；不显式覆盖，模型会继续按规则 7/10/13 调用不存在的工具。
SITE_OPERATIONS_DISABLED_NOTICE = """

## 站点操作工具已停用

本部署已关闭问股全站操作能力：第 7/10/13 条规则（以及第 6 条直执行规则）不再适用，
全部后台任务工具、`get_*_view` 读取工具以及已被关闭的 `propose_*` 提案工具都不可用。
不要调用这些工具，也不要在未调用工具时声称任何操作已经执行；仅可使用实际注册的工具。
"""


CODEX_CHAT_SYSTEM_PROMPT = """你是一位{market_role}投资分析 Agent，负责基于 DSA 已保存的数据解答用户的股票投资问题。

## 可用数据

- `get_analysis_context`：读取指定股票最近一次已保存的分析上下文。
- `get_skill_backtest_summary`：读取指定交易技能的已保存回测汇总。
- `get_strategy_backtest_summary`：读取整体交易策略的已保存回测汇总。

## 工作方式

1. 询问具体股票时，先调用 `get_analysis_context`，再依据返回的已保存数据回答。
2. 用户询问交易技能或策略表现时，按问题调用对应的回测汇总工具。
3. 明确说明结论基于已保存数据；若数据带有分析时间，应在回答中提示其时间范围。
4. 工具未返回回答所需的信息时，直接说明当前保存的数据不足，不得补写或猜测数据。
5. 自由组织面向用户的回答，不需要输出 JSON。

{language_section}
"""


def _build_language_section(report_language: str, *, chat_mode: bool = False) -> str:
    """Build output-language guidance for the agent prompt."""
    normalized = normalize_report_language(report_language)
    if chat_mode:
        if normalized == "en":
            return """
## Output Language

- Reply in English.
- If you output JSON, keep the keys unchanged and write every human-readable value in English.
"""
        return """
## 输出语言

- 默认使用中文回答。
- 若输出 JSON，键名保持不变，所有面向用户的文本值使用中文。
"""

    if normalized == "en":
        return """
## Output Language

- Keep every JSON key unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all dashboard text, checklist items, and summaries.
"""

    return """
## 输出语言

- 所有 JSON 键名保持不变。
- `decision_type` 必须保持为 `buy|hold|sell`。
- 所有面向用户的人类可读文本值必须使用中文。
"""


# ============================================================
# Shared chat request preparation
# ============================================================


@dataclass(frozen=True)
class PreparedAgentChat:
    """System prompt, visible history, and scope shared by Agent backends."""

    system_prompt: str
    history_messages: List[Dict[str, Any]]
    stock_scope: Optional[StockScope]


def prepare_agent_chat(
    *,
    message: str,
    session_id: str,
    context: Optional[Dict[str, Any]],
    config: Any,
    context_llm_adapter: Any,
    skill_instructions: str,
    default_skill_policy: str,
    use_legacy_default_prompt: bool,
    use_codex_prompt: bool,
    include_provider_trace: bool,
    strict_initial_stock_scope: bool = False,
) -> PreparedAgentChat:
    """Build the existing Chat prompt order without choosing an Agent backend."""
    scope_resolution = resolve_stock_scope(
        message,
        context,
        strict_initial_scope=strict_initial_stock_scope,
    )
    effective_context = scope_resolution.effective_context

    skills_section = ""
    if skill_instructions:
        skills_section = f"## 激活的交易技能\n\n{skill_instructions}"
    default_skill_policy_section = ""
    if default_skill_policy:
        default_skill_policy_section = f"\n{default_skill_policy}\n"
    report_language = normalize_report_language((effective_context or {}).get("report_language", "zh"))
    stock_code = (effective_context or {}).get("stock_code", "")
    if use_codex_prompt:
        prompt_template = CODEX_CHAT_SYSTEM_PROMPT
    elif use_legacy_default_prompt:
        prompt_template = LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT
    else:
        prompt_template = CHAT_SYSTEM_PROMPT
    system_prompt = prompt_template.format(
        market_role=get_market_role(stock_code, report_language),
        market_guidelines=get_market_guidelines(stock_code, report_language),
        default_skill_policy_section=default_skill_policy_section,
        skills_section=skills_section,
        language_section=_build_language_section(report_language, chat_mode=True),
    )
    # 总开关关闭时注册表已收回站点工具，提示词必须同步撤销「去调用」的指令，
    # 否则模型会调用不存在/已撤销的工具。``config`` 可能为 None，默认视为开启。
    if not bool(getattr(config, "agent_chat_site_operations_enabled", True)):
        system_prompt = f"{system_prompt}{SITE_OPERATIONS_DISABLED_NOTICE}"

    if include_provider_trace:
        history_messages = list(
            build_agent_chat_context_bundle(session_id, context_llm_adapter, config).context_messages
        )
    else:
        history_messages = list(
            build_visible_chat_history(
                session_id,
                context_llm_adapter,
                config,
                allow_llm_compression=False,
            )
        )

    if effective_context:
        context_parts = []
        if effective_context.get("stock_code"):
            context_parts.append(f"股票代码: {effective_context['stock_code']}")
        if effective_context.get("stock_name"):
            context_parts.append(f"股票名称: {effective_context['stock_name']}")
        if effective_context.get("previous_price"):
            context_parts.append(f"上次分析价格: {effective_context['previous_price']}")
        if effective_context.get("previous_change_pct"):
            context_parts.append(f"上次涨跌幅: {effective_context['previous_change_pct']}%")
        if effective_context.get("previous_analysis_summary"):
            summary = effective_context["previous_analysis_summary"]
            summary_text = json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else str(summary)
            context_parts.append(f"上次分析摘要:\n{summary_text}")
        if effective_context.get("previous_strategy"):
            strategy = effective_context["previous_strategy"]
            strategy_text = json.dumps(strategy, ensure_ascii=False) if isinstance(strategy, dict) else str(strategy)
            context_parts.append(f"上次策略分析:\n{strategy_text}")
        daily_market_context_section = format_daily_market_context_prompt_section(
            effective_context.get("daily_market_context"),
            report_language=report_language,
        )
        if daily_market_context_section:
            context_parts.append(daily_market_context_section.strip())
        market_structure_section = format_market_structure_prompt_section(
            effective_context.get("market_structure_context"),
            report_language=report_language,
        )
        if market_structure_section:
            context_parts.append(market_structure_section.strip())
        if context_parts:
            history_messages.extend(
                [
                    {
                        "role": "user",
                        "content": "[系统提供的历史分析上下文，可供参考对比]\n" + "\n".join(context_parts),
                    },
                    {
                        "role": "assistant",
                        "content": "好的，我已了解该股票的历史分析数据。请告诉我你想了解什么？",
                    },
                ]
            )

    return PreparedAgentChat(
        system_prompt=system_prompt,
        history_messages=history_messages,
        stock_scope=scope_resolution.stock_scope,
    )


# ============================================================
# Agent Executor
# ============================================================

class AgentExecutor:
    """ReAct agent loop with tool calling.

    Usage::

        executor = AgentExecutor(tool_registry, llm_adapter)
        result = executor.run("Analyze stock 600519")
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        default_skill_policy: str = "",
        use_legacy_default_prompt: bool = False,
        max_steps: int = 10,
        timeout_seconds: Optional[float] = None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.default_skill_policy = default_skill_policy
        self.use_legacy_default_prompt = use_legacy_default_prompt
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a given task.

        Args:
            task: The user task / analysis request.
            context: Optional context dict (e.g., {"stock_code": "600519"}).

        Returns:
            AgentResult with parsed dashboard or error.
        """
        # Build system prompt with skills
        skills_section = ""
        if self.skill_instructions:
            skills_section = f"## 激活的交易技能\n\n{self.skill_instructions}"
        default_skill_policy_section = ""
        if self.default_skill_policy:
            default_skill_policy_section = f"\n{self.default_skill_policy}\n"
        report_language = normalize_report_language((context or {}).get("report_language", "zh"))
        stock_code = (context or {}).get("stock_code", "")
        market_role = get_market_role(stock_code, report_language)
        market_guidelines = get_market_guidelines(stock_code, report_language)
        prompt_template = (
            LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT
            if self.use_legacy_default_prompt
            else AGENT_SYSTEM_PROMPT
        )
        system_prompt = prompt_template.format(
            market_role=market_role,
            market_guidelines=market_guidelines,
            default_skill_policy_section=default_skill_policy_section,
            skills_section=skills_section,
            language_section=_build_language_section(report_language),
        )

        # Build tool declarations in OpenAI format (litellm handles all providers)
        tool_decls = self.tool_registry.to_openai_tools()

        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._build_user_message(task, context)},
        ]

        return self._run_loop(messages, tool_decls, parse_dashboard=True)

    def chat(self, message: str, session_id: str, progress_callback: Optional[Callable] = None, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a free-form chat message.

        Args:
            message: The user's chat message.
            session_id: The conversation session ID.
            progress_callback: Optional callback for streaming progress events.
            context: Optional context dict from previous analysis for data reuse.

        Returns:
            AgentResult with the text response.
        """
        from src.agent.conversation import conversation_manager

        conversation_manager.get_or_create(session_id)
        config = getattr(self.llm_adapter, "_config", None) or get_config()
        prepared = prepare_agent_chat(
            message=message,
            session_id=session_id,
            context=context,
            config=config,
            context_llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            default_skill_policy=self.default_skill_policy,
            use_legacy_default_prompt=self.use_legacy_default_prompt,
            use_codex_prompt=False,
            include_provider_trace=True,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": prepared.system_prompt},
            *prepared.history_messages,
        ]
        messages.append({"role": "user", "content": message})
        baseline_len = len(messages)
        run_id = str(uuid.uuid4())

        # Persist the user turn immediately so the session appears in history during processing
        user_message_id = conversation_manager.add_message(session_id, "user", message)

        tool_decls = self.tool_registry.to_openai_tools()
        result = self._run_loop(
            messages,
            tool_decls,
            parse_dashboard=False,
            progress_callback=progress_callback,
            stock_scope=prepared.stock_scope,
        )

        # Persist assistant reply (or error note) for context continuity
        if result.success:
            assistant_message_id = conversation_manager.add_message(session_id, "assistant", result.content)
            self._persist_provider_trace(
                session_id=session_id,
                run_id=run_id,
                messages=result.messages,
                baseline_len=baseline_len,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
        else:
            error_note = f"[分析失败] {result.error or '未知错误'}"
            conversation_manager.add_message(session_id, "assistant", error_note)

        return result

    def _persist_provider_trace(
        self,
        *,
        session_id: str,
        run_id: str,
        messages: List[Dict[str, Any]],
        baseline_len: int,
        user_message_id: int,
        assistant_message_id: int,
    ) -> None:
        persist_provider_trace_turns(
            session_id=session_id,
            run_id=run_id,
            messages=messages,
            baseline_len=baseline_len,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            db_factory=get_db,
            log=logger,
        )

    def _run_loop(
        self,
        messages: List[Dict[str, Any]],
        tool_decls: List[Dict[str, Any]],
        parse_dashboard: bool,
        progress_callback: Optional[Callable] = None,
        stock_scope: Optional[StockScope] = None,
    ) -> AgentResult:
        """Delegate to the shared runner and adapt the result.

        Dashboard mode exposes only the parsed canonical payload through both
        ``dashboard`` and ``content``; free-form mode preserves the raw text.
        """
        loop_result = run_agent_loop(
            messages=messages,
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            max_steps=self.max_steps,
            progress_callback=progress_callback,
            max_wall_clock_seconds=self.timeout_seconds,
            stock_scope=stock_scope,
        )

        model_str = loop_result.model

        if parse_dashboard and loop_result.success:
            dashboard = parse_dashboard_json(loop_result.content)
            return AgentResult(
                success=dashboard is not None,
                content=(
                    json.dumps(dashboard, ensure_ascii=False, indent=2)
                    if dashboard is not None
                    else loop_result.content
                ),
                dashboard=dashboard,
                tool_calls_log=loop_result.tool_calls_log,
                total_steps=loop_result.total_steps,
                total_tokens=loop_result.total_tokens,
                provider=loop_result.provider,
                model=model_str,
                error=None if dashboard else "Failed to parse dashboard JSON from agent response",
                messages=loop_result.messages,
            )

        return AgentResult(
            success=loop_result.success,
            content=loop_result.content,
            dashboard=None,
            tool_calls_log=loop_result.tool_calls_log,
            total_steps=loop_result.total_steps,
            total_tokens=loop_result.total_tokens,
            provider=loop_result.provider,
            model=model_str,
            error=loop_result.error,
            messages=loop_result.messages,
        )

    def _build_user_message(self, task: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Build the initial user message."""
        parts = [task]
        if context:
            report_language = normalize_report_language(context.get("report_language", "zh"))
            if context.get("stock_code"):
                parts.append(f"\n股票代码: {context['stock_code']}")
            if context.get("report_type"):
                parts.append(f"报告类型: {context['report_type']}")
            if report_language == "en":
                parts.append("输出语言: English（所有 JSON 键名保持不变，所有面向用户的文本值使用英文）")
            elif report_language == "ko":
                parts.append("출력 언어: 한국어（모든 JSON 키는 그대로 유지하고, 사용자 노출 텍스트 값은 한국어로 작성）")
            else:
                parts.append("输出语言: 中文（所有 JSON 键名保持不变，所有面向用户的文本值使用中文）")

            market_phase_section = format_market_phase_prompt_section(
                context.get("market_phase_context"),
                report_language=report_language,
            )
            if market_phase_section:
                parts.append(market_phase_section)

            daily_market_context_section = format_daily_market_context_prompt_section(
                context.get("daily_market_context"),
                report_language=report_language,
            )
            if daily_market_context_section:
                parts.append(daily_market_context_section)

            market_structure_section = format_market_structure_prompt_section(
                context.get("market_structure_context"),
                report_language=report_language,
            )
            if market_structure_section:
                parts.append(market_structure_section)

            analysis_context_pack_summary = context.get("analysis_context_pack_summary")
            if isinstance(analysis_context_pack_summary, str) and analysis_context_pack_summary:
                parts.append(analysis_context_pack_summary)

            # Inject pre-fetched context data to avoid redundant fetches
            if context.get("realtime_quote"):
                parts.append(f"\n[系统已获取的实时行情]\n{json.dumps(context['realtime_quote'], ensure_ascii=False)}")
            if context.get("chip_distribution"):
                parts.append(f"\n[系统已获取的筹码分布]\n{json.dumps(context['chip_distribution'], ensure_ascii=False)}")
            if context.get("news_context"):
                parts.append(f"\n[系统已获取的新闻与舆情情报]\n{context['news_context']}")

        parts.append("\n请使用可用工具获取缺失的数据（如历史K线、新闻等），然后以决策仪表盘 JSON 格式输出分析结果。")
        return "\n".join(parts)

# -*- coding: utf-8 -*-
"""
===================================
场外基金 LLM 增强契约
===================================

定义场外基金 LLM 分析输出的结构化模型，由 ``GeminiAnalyzer.FUND_SYSTEM_PROMPT``
（见 ``src/analyzer.py``）约定字段名，二者必须同步修改——字段清单共用
``FUND_REPORT_FIELDS``，由 ``tests/test_fund_llm_prompt.py`` 守卫。

**职责边界**：净值、区间收益、回撤、波动率、夏普、持仓、资产配置这些**事实**
由 ``build_fund_report`` 从数据源确定性算出（见 ``src/services/fund_analysis.py``），
LLM 不参与、也不复述。本契约只承载**解读**：模型基于给定事实给出的综合判断、
申赎倾向、风险提示与情绪分。

因此这里既没有 ``manager`` / ``scale`` / ``inception_date``（``FundProfile``
不提供这些事实，问了就等于请模型凭记忆编造），也没有 ``max_drawdown`` /
``top_holdings``（确定性层已有，再让模型复述一次会在同一张卡片上产生两个
可能互相矛盾的数据源）。

与 ``report_schema.py`` 同源约定：字段一律 ``Optional``，因为模型可能省略；
业务层的完整性判断另行处理。区别在于 ``extra`` 采用 ``ignore``——基金载荷会
直接进入前端展示，模型自造的键（如误用股票概念的 ``moving_average``）不得
混入载荷被当成契约的一部分。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _FundModel(BaseModel):
    """基金载荷内所有模型的共同配置：忽略模型多吐的字段。"""

    model_config = ConfigDict(extra="ignore")


class FundReportSchema(_FundModel):
    """场外基金 LLM 解读输出。

    基金只有每日净值、没有盘中行情，因此这里不设任何股票概念字段
    （均线 / 成交量 / 涨跌停 / 资金流）。``operation_advice`` 是申赎倾向，
    不是买卖点。
    """

    # 对确定性层给出的持仓结构的解读，如「集中度较高，前十大占净值 68%」。
    holdings_concentration: Optional[str] = None
    # 综合解读：把净值表现、回撤、波动、持仓结构串成一个判断。
    analysis_summary: Optional[str] = None
    # 申赎倾向（申购 / 赎回 / 持有观望）及触发条件，不是买卖点。
    operation_advice: Optional[str] = None
    risk_warning: Optional[str] = None
    # 0-100，50 为中性；衡量风险收益吸引力，不是短期涨跌预期。
    sentiment_score: Optional[int] = Field(default=None, ge=0, le=100)

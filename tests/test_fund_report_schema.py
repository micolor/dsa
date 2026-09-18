# -*- coding: utf-8 -*-
"""场外基金 LLM 增强契约测试。

约束 ``FundReportSchema`` 是 LLM 输出的**唯一收口**：模型多吐的字段不得进入
载荷，越界的 sentiment_score 必须被拒，缺字段则按 None 放行（模型可能省略）。

同时守住**职责边界**：净值 / 收益 / 回撤 / 持仓这些事实由确定性层持有，
本契约只承载解读，不得出现与确定性层重复的事实字段。
"""

import pytest
from pydantic import ValidationError

from src.schemas.fund_report_schema import FundReportSchema


def _payload(**overrides):
    base = {
        "holdings_concentration": "前十大重仓占净值 68%，集中度较高",
        "analysis_summary": "近一年回撤明显但已收复大半，波动高于同类。",
        "operation_advice": "可考虑分批申购，回撤超 20% 时暂停。",
        "risk_warning": "重仓股集中在单一行业，行业回调时净值波动会放大。",
        "sentiment_score": 60,
    }
    base.update(overrides)
    return base


def test_accepts_a_complete_payload():
    schema = FundReportSchema(**_payload())

    assert schema.sentiment_score == 60
    assert schema.holdings_concentration == "前十大重仓占净值 68%，集中度较高"
    assert schema.operation_advice == "可考虑分批申购，回撤超 20% 时暂停。"


def test_all_fields_are_optional_because_the_model_may_omit_them():
    schema = FundReportSchema()

    assert schema.analysis_summary is None
    assert schema.sentiment_score is None


def test_unexpected_fields_from_the_model_do_not_leak_into_the_payload():
    """模型自造的字段不得进入载荷。

    否则 ``model_dump()`` 会把任意键一路带到前端展示层，
    等于把未经验证的模型输出当成契约的一部分。
    """
    schema = FundReportSchema(
        **_payload(moving_average="MA20 上穿", 涨跌停="涨停")
    )

    dumped = schema.model_dump()
    assert "moving_average" not in dumped
    assert "涨跌停" not in dumped
    assert set(dumped) == set(FundReportSchema.model_fields)


def test_fact_fields_owned_by_the_deterministic_layer_are_not_part_of_the_contract():
    """事实字段不属本契约。

    ``manager`` / ``scale`` / ``inception_date`` 没有数据源，收下就等于请模型
    凭记忆编造；``max_drawdown`` / ``top_holdings`` 确定性层已有，再收一次会在
    同一张卡片上产生两个可能互相矛盾的数据源。
    """
    for field in (
        "manager",
        "scale",
        "inception_date",
        "max_drawdown",
        "current_drawdown",
        "interval_return",
        "top_holdings",
        "fund_name",
        "fund_type",
    ):
        assert field not in FundReportSchema.model_fields, f"{field} 不该由 LLM 提供"


def test_sentiment_score_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        FundReportSchema(**_payload(sentiment_score=101))

    with pytest.raises(ValidationError):
        FundReportSchema(**_payload(sentiment_score=-1))


def test_sentiment_score_accepts_the_boundaries():
    assert FundReportSchema(**_payload(sentiment_score=0)).sentiment_score == 0
    assert FundReportSchema(**_payload(sentiment_score=100)).sentiment_score == 100

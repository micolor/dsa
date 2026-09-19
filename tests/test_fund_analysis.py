# tests/test_fund_analysis.py
import logging
from unittest.mock import MagicMock

from data_provider.fund_fetcher import NavRecord
from src.services.fund_analysis import (
    build_fund_llm_user_prompt,
    build_fund_report,
    enrich_fund_report_with_llm,
    map_fund_report_to_report_result,
)

def _profile():
    navs = []
    nv = 1.0
    for d in range(120):
        nv *= 1.001  # 平稳上行
        navs.append(NavRecord(date=str(d), unit_nav=round(nv,4), acc_nav=0.0, change_pct=0.1))
    from data_provider.fund_fetcher import FundProfile, compute_metrics
    m = compute_metrics(navs)
    return FundProfile(code="003095", name="中欧医疗健康混合A", nav_history=navs, **m)

def _profile_with_holdings():
    from data_provider.fund_fetcher import (
        FundAssetAllocation,
        FundHolding,
        FundProfile,
    )
    base = _profile()
    return FundProfile(
        code=base.code,
        name=base.name,
        fund_type="混合型",
        nav_history=base.nav_history,
        holdings=[
            FundHolding(
                rank=1,
                stock_code="600519",
                stock_name="贵州茅台",
                pct_of_nav=8.5,
                share_count=1000.0,
                market_value=12000.0,
            )
        ],
        asset_allocation=FundAssetAllocation(
            report_date="2026-06-30",
            stock_pct=88.0,
            bond_pct=0.0,
            cash_pct=6.5,
            net_asset=1234000000.0,
        ),
        return_1m=base.return_1m,
        return_3m=base.return_3m,
        return_6m=base.return_6m,
        return_1y=base.return_1y,
        max_drawdown=base.max_drawdown,
        annual_volatility=base.annual_volatility,
        sharpe=base.sharpe,
    )


def test_build_fund_report_shape():
    r = build_fund_report(_profile())
    assert r["report_type"] == "fund"
    assert "operation_advice" in r
    assert "风险" in r["summary"] or "净值" in r["summary"]


def test_insufficient_data_does_not_claim_a_low_risk_stable_trend():
    """净值序列不足时，结论三件套不得与 summary 的「风险等级:数据不足」互相矛盾。

    回撤/波动为 None 时 ``_risk_grade`` 给「数据不足」，但原来的分支把缺失值
    当成 0 参与判断：走势落到「震荡」（上行需要 ``return_1y > 0``）、建议落到
    兜底文案「风险较低,走势相对平稳」。这两句会一路进通知和 Web 报告，跟同一
    份报告里的「风险等级:数据不足」直接打架——既不对，也不诚实。
    """
    from data_provider.fund_fetcher import FundProfile, NavRecord as NR

    navs = [
        NR(date="2026-09-17", unit_nav=1.0, acc_nav=1.0, change_pct=0.0),
        NR(date="2026-09-18", unit_nav=1.0, acc_nav=1.0, change_pct=0.0),
    ]
    profile = FundProfile(code="003095", name="测试基金", nav_history=navs)

    r = build_fund_report(profile)

    assert "风险等级:数据不足" in r["summary"]
    assert r["trend_prediction"] == "数据不足"
    assert "风险较低" not in r["operation_advice"]
    assert "走势相对平稳" not in r["operation_advice"]
    assert "数据不足" in r["operation_advice"]


def test_latest_nav_comes_from_the_newest_record():
    """``latest_nav`` 必须取序列末尾（最新）那条，不是最旧那条。

    净值序列的顺序契约由 ``FundFetcher._fetch_nav`` 归一化为正序；这里守的是
    报告层确实按「末尾 = 最新」取数——取错会把一年前的净值当最新值展示。
    """
    from data_provider.fund_fetcher import FundProfile, NavRecord as NR

    navs = [
        NR(date="2025-01-02", unit_nav=1.0, acc_nav=1.0, change_pct=0.0),
        NR(date="2026-01-02", unit_nav=1.5, acc_nav=1.5, change_pct=0.0),
        NR(date="2026-09-18", unit_nav=1.9, acc_nav=1.9, change_pct=0.0),
    ]
    profile = FundProfile(code="003095", name="测试基金", nav_history=navs)

    assert build_fund_report(profile)["latest_nav"] == 1.9


def test_latest_nav_in_llm_prompt_comes_from_the_newest_record():
    """喂给模型的「最新净值」同样不得取自窗口内最旧的一条。"""
    from data_provider.fund_fetcher import FundProfile, NavRecord as NR

    navs = [
        NR(date="2025-01-02", unit_nav=1.0, acc_nav=1.0, change_pct=0.0),
        NR(date="2026-09-18", unit_nav=1.9, acc_nav=1.9, change_pct=0.0),
    ]
    prompt = build_fund_llm_user_prompt(
        FundProfile(code="003095", name="测试基金", nav_history=navs)
    )

    assert "最新净值(2026-09-18): 1.9" in prompt


# ------------------------------------------------------------------ 用户上下文


def test_llm_user_prompt_carries_the_facts_the_model_must_interpret():
    prompt = build_fund_llm_user_prompt(_profile_with_holdings())

    assert "003095" in prompt
    assert "中欧医疗健康混合A" in prompt
    assert "混合型" in prompt
    # 持仓与资产配置是解读集中度、行业暴露的依据，必须给到模型。
    assert "600519" in prompt and "贵州茅台" in prompt
    assert "8.5" in prompt
    assert "88.0" in prompt


def test_llm_user_prompt_omits_sections_the_profile_does_not_have():
    """没有持仓数据时不得渲染空表格，否则等于告诉模型「持仓为空」。"""
    prompt = build_fund_llm_user_prompt(_profile())

    assert "重仓" not in prompt
    assert "资产配置" not in prompt


def test_llm_user_prompt_does_not_leak_the_deterministic_conclusions():
    """确定性层的结论不喂给模型，避免它只做同义改写。"""
    prompt = build_fund_llm_user_prompt(_profile_with_holdings())

    assert "不构成投资建议" not in prompt


# ------------------------------------------------------------------ 增强层


def _fake_analyzer(payload):
    analyzer = MagicMock()
    analyzer.run_fund_analysis.return_value = payload
    return analyzer


def test_enrich_returns_the_sanitized_payload():
    analyzer = _fake_analyzer(
        {
            "holdings_concentration": "集中度较高",
            "analysis_summary": "综合解读",
            "operation_advice": "可考虑分批申购",
            "risk_warning": "行业暴露集中",
            "sentiment_score": 60,
        }
    )

    block = enrich_fund_report_with_llm(_profile_with_holdings(), analyzer=analyzer)

    assert block["sentiment_score"] == 60
    assert block["operation_advice"] == "可考虑分批申购"
    assert set(block) == {
        "holdings_concentration",
        "analysis_summary",
        "operation_advice",
        "risk_warning",
        "sentiment_score",
    }


def test_enrich_drops_fields_the_model_invented():
    """模型多吐的字段不得进入载荷（如误用股票概念）。"""
    analyzer = _fake_analyzer(
        {
            "analysis_summary": "综合解读",
            "moving_average": "MA20 上穿",
            "top_holdings": [{"code": "600519"}],
        }
    )

    block = enrich_fund_report_with_llm(_profile(), analyzer=analyzer)

    assert "moving_average" not in block
    assert "top_holdings" not in block


def test_enrich_passes_the_report_language_through():
    analyzer = _fake_analyzer({"analysis_summary": "x"})

    enrich_fund_report_with_llm(_profile(), analyzer=analyzer, report_language="en")

    assert analyzer.run_fund_analysis.call_args.kwargs["report_language"] == "en"


def test_enrich_returns_none_when_every_field_is_empty():
    """模型回了一坨 null 时不该在卡片上留一个空壳区块。"""
    analyzer = _fake_analyzer({"analysis_summary": None, "sentiment_score": None})

    assert enrich_fund_report_with_llm(_profile(), analyzer=analyzer) is None


def test_enrich_returns_none_and_logs_when_the_llm_call_fails(caplog):
    """LLM 是增强层：失败要有记录地降级，不能让整份确定性报告消失。

    这里断言的是「记录下来并降级」，不是静默吞掉——没有告警日志的 None
    会让线上分不清「没配 LLM」和「LLM 一直在挂」。
    """
    analyzer = MagicMock()
    analyzer.run_fund_analysis.side_effect = RuntimeError("all models failed")

    with caplog.at_level(logging.WARNING):
        block = enrich_fund_report_with_llm(_profile(), analyzer=analyzer)

    assert block is None
    assert "all models failed" in caplog.text


def test_enrich_returns_none_and_logs_when_the_payload_violates_the_contract(caplog):
    """越界的 sentiment_score 让整块不可信，按契约整块丢弃并记录原因。"""
    analyzer = _fake_analyzer({"analysis_summary": "x", "sentiment_score": 150})

    with caplog.at_level(logging.WARNING):
        block = enrich_fund_report_with_llm(_profile(), analyzer=analyzer)

    assert block is None
    assert "sentiment_score" in caplog.text or "校验" in caplog.text


# ------------------------------------------------------------------ 载荷映射


def test_dashboard_carries_the_llm_block():
    result = map_fund_report_to_report_result(
        build_fund_report(_profile()),
        fund_llm={"analysis_summary": "综合解读", "sentiment_score": 60},
    )

    assert result.dashboard["llm"] == {
        "analysis_summary": "综合解读",
        "sentiment_score": 60,
    }


def test_dashboard_llm_is_none_when_the_enhancement_did_not_run():
    """确定性报告不依赖 LLM：没有增强块时其余字段照常完整。"""
    result = map_fund_report_to_report_result(build_fund_report(_profile()))

    assert result.dashboard["llm"] is None
    assert result.dashboard["report_type"] == "fund"
    assert result.dashboard["metrics"]


def test_deterministic_score_is_not_overwritten_by_the_llm_score():
    """``AnalysisResult.sentiment_score`` 是下游排序依据，保持确定性取值。

    模型给出的分数随调用波动，用它排序会让同一条基金记录在不同运行里
    跳来跳去；LLM 分只作为解读展示在 ``dashboard["llm"]`` 内。
    """
    result = map_fund_report_to_report_result(
        build_fund_report(_profile()),
        fund_llm={"analysis_summary": "综合解读", "sentiment_score": 95},
    )

    assert result.sentiment_score == 50
    assert result.dashboard["llm"]["sentiment_score"] == 95

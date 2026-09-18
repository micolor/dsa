# -*- coding: utf-8 -*-
"""fund:<code> 分支的 LLM 增强接入测试（离线）。

覆盖的是真实接线：``StockAnalysisPipeline.analyze_stock`` 的基金分支在拿到
``FundProfile`` 后是否调用增强层、是否把它挂进 ``dashboard["llm"]``，以及
LLM 整条链路挂掉时确定性报告是否照常产出。

因此这里只桩掉两处**外部**依赖：数据源（``FundFetcher``，要联网）和
``GeminiAnalyzer`` 的 LLM 调用。管线的分支逻辑、确定性报告、载荷映射、
契约校验全部走真实实现。
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data_provider.fund_fetcher import FundProfile, NavRecord, compute_metrics
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


def _make_config() -> SimpleNamespace:
    return SimpleNamespace(
        max_workers=2,
        save_context_snapshot=False,
        bocha_api_keys=[],
        tavily_api_keys=[],
        brave_api_keys=[],
        serpapi_keys=[],
        minimax_api_keys=[],
        searxng_base_urls=[],
        searxng_public_instances_enabled=False,
        news_max_age_days=7,
        news_strategy_profile="short",
        enable_realtime_quote=False,
        realtime_source_priority=[],
        enable_chip_distribution=False,
        social_sentiment_api_key="",
        social_sentiment_api_url="https://example.invalid/social",
        report_language="zh",
    )


def _profile() -> FundProfile:
    navs = [
        NavRecord(date=str(i), unit_nav=round(1.0 + i * 0.001, 4), acc_nav=0.0, change_pct=0.1)
        for i in range(120)
    ]
    return FundProfile(
        code="003095",
        name="中欧医疗健康混合A",
        fund_type="混合型",
        nav_history=navs,
        **(compute_metrics(navs) or {}),
    )


def _analyze_fund(analyzer: MagicMock, caplog=None):
    """在桩掉数据源与 LLM 的前提下，跑一次 fund:<code> 分析。"""
    config = _make_config()
    with patch("src.core.pipeline.get_db", return_value=MagicMock()), patch(
        "src.core.pipeline.DataFetcherManager", return_value=MagicMock()
    ), patch("src.core.pipeline.StockTrendAnalyzer", return_value=MagicMock()), patch(
        "src.core.pipeline.NotificationService", return_value=MagicMock()
    ), patch(
        "src.core.pipeline.GeminiAnalyzer", return_value=analyzer
    ), patch(
        "data_provider.fund_fetcher.FundFetcher.get_profile",
        return_value=_profile(),
    ):
        pipeline = StockAnalysisPipeline(config=config)
        return pipeline.analyze_stock("fund:003095", ReportType.FUND, "q-test")


def test_fund_branch_attaches_the_llm_block_to_the_dashboard():
    analyzer = MagicMock()
    analyzer.run_fund_analysis.return_value = {
        "holdings_concentration": "集中度较高",
        "analysis_summary": "综合解读",
        "operation_advice": "可考虑分批申购",
        "risk_warning": "行业暴露集中",
        "sentiment_score": 60,
    }

    result = _analyze_fund(analyzer)

    assert result is not None
    assert result.dashboard["report_type"] == "fund"
    assert result.dashboard["llm"]["analysis_summary"] == "综合解读"
    assert result.dashboard["llm"]["sentiment_score"] == 60
    # 确定性事实仍然来自数据源，没有被 LLM 覆盖。
    assert result.dashboard["metrics"]


def test_fund_report_survives_a_total_llm_failure(caplog):
    """LLM 全挂时基金报告仍须产出，只是没有增强块。"""
    analyzer = MagicMock()
    analyzer.run_fund_analysis.side_effect = RuntimeError("all models failed")

    with caplog.at_level(logging.WARNING):
        result = _analyze_fund(analyzer)

    assert result is not None
    assert result.dashboard["report_type"] == "fund"
    assert result.dashboard["llm"] is None
    assert result.dashboard["metrics"]
    assert "all models failed" in caplog.text


def test_fund_report_survives_an_unconfigured_llm():
    """未配置 LLM 时不新增配置项、也不报错：增强层自然缺席。"""
    analyzer = MagicMock()
    analyzer.run_fund_analysis.return_value = {}

    result = _analyze_fund(analyzer)

    assert result is not None
    assert result.dashboard["llm"] is None
    assert result.dashboard["report_type"] == "fund"


# ------------------------------------------------------------ 外层 process_stock


def _process_single_stock_fetch_mock(code: str) -> MagicMock:
    """跑一次 ``process_single_stock``，返回被桩掉的 ``fetch_and_save_stock_data``。

    桩掉的是**外层编排之外**的东西（库、数据源管理器、通知、LLM），
    ``process_single_stock`` 自身的分支逻辑走真实实现。
    """
    config = _make_config()
    with patch("src.core.pipeline.get_db", return_value=MagicMock()), patch(
        "src.core.pipeline.DataFetcherManager", return_value=MagicMock()
    ), patch("src.core.pipeline.StockTrendAnalyzer", return_value=MagicMock()), patch(
        "src.core.pipeline.NotificationService", return_value=MagicMock()
    ), patch("src.core.pipeline.GeminiAnalyzer", return_value=MagicMock()):
        pipeline = StockAnalysisPipeline(config=config)
        pipeline._emit_progress = lambda *a, **k: None
        with patch.object(
            pipeline, "fetch_and_save_stock_data", return_value=(True, None)
        ) as fetch, patch.object(
            pipeline, "analyze_stock", return_value=MagicMock(success=False)
        ):
            pipeline.process_single_stock(code)
    return fetch


def test_process_stock_skips_the_stock_data_step_for_funds():
    """基金没有行情/K 线，不该走股票数据获取那一步。

    净值由 ``analyze_stock`` 的基金分支自己取；在这里先跑一遍股票数据管道
    只会依次打满各数据源的失败路径再落库——白等几十秒并刷一屏 ERROR 日志，
    对报告内容没有任何贡献。
    """
    assert _process_single_stock_fetch_mock("fund:003095").call_count == 0


def test_process_stock_still_fetches_stock_data_for_equities():
    """股票链路零行为变化：数据获取步骤照旧执行。"""
    assert _process_single_stock_fetch_mock("600519").call_count == 1

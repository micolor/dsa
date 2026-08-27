# tests/test_fund_report.py
from unittest import mock

from src.schemas.fund_report_schema import FundReportSchema
from src.services.fund_data_provider import FundDataProvider
from src.services.analysis_service import AnalysisService


def test_fund_report_schema_accepts_valid():
    r = FundReportSchema(fund_name="中欧医疗创新股票C", interval_return=0.2,
                         sentiment_score=65)
    assert r.fund_name == "中欧医疗创新股票C"


def test_fund_report_schema_extra_allow():
    r = FundReportSchema(**{"unknown_key": 1})
    assert "unknown_key" in r.model_dump()


def test_analyze_fund_dispatches():
    # 注意：analyze_stock 内部局部导入 is_fund_code，因此必须 patch 源模块属性。
    with mock.patch("src.services.fund_data_provider.is_fund_code", return_value=True), \
         mock.patch.object(AnalysisService, "_analyze_fund_stock",
                           return_value={"report": {"meta": {"report_type": "fund"}}}) as m:
        svc = AnalysisService()
        out = svc.analyze_stock("006229.FUND")
        m.assert_called_once()
        assert out["report"]["meta"]["report_type"] == "fund"


def test_analyze_stock_not_a_fund_keeps_stock_path():
    # 非基金代码（无 .FUND/.OTC 后缀）不应触发基金分流，走证券路径。
    # mock 掉真实 pipeline / diagnostic 上下文，避免测试依赖网络与数据库。
    from types import SimpleNamespace
    fake_pipeline = mock.Mock()
    fake_pipeline.process_single_stock.return_value = SimpleNamespace(success=True, code="600519")
    with mock.patch("src.services.fund_data_provider.is_fund_code", return_value=False), \
         mock.patch.object(AnalysisService, "_analyze_fund_stock") as m, \
         mock.patch("src.core.pipeline.StockAnalysisPipeline", return_value=fake_pipeline), \
         mock.patch("src.services.analysis_service.activate_run_diagnostic_context", return_value=None), \
         mock.patch("src.services.analysis_service.get_current_diagnostic_context", return_value=None), \
         mock.patch("src.services.analysis_service.reset_run_diagnostic_context"), \
         mock.patch.object(AnalysisService, "_build_analysis_response",
                           return_value={"report": {"meta": {"report_type": "detailed"}}}):
        svc = AnalysisService()
        out = svc.analyze_stock("600519", query_id="q1")
    m.assert_not_called()
    assert out["report"]["meta"]["report_type"] == "detailed"


def test_analyze_fund_structures_report_with_mocked_provider():
    # mock 掉网络数据源，验证「分流 + 编排 + 报告结构」不真正联网。
    fake_provider = mock.Mock(spec=FundDataProvider)
    fake_provider.get_nav_series.return_value = ([], {"provider": "test", "as_of": None})
    fake_provider.stats_from_nav.return_value = {
        "interval_return": None, "max_drawdown": None,
        "current_drawdown": None, "period_days": 0,
    }
    fake_provider.get_holdings.return_value = []

    llm_data = {"fund_name": "中欧医疗创新股票C", "sentiment_score": 60,
                "operation_advice": "持有", "interval_return": 0.2}
    with mock.patch("src.services.fund_data_provider.FundDataProvider",
                    return_value=fake_provider), \
         mock.patch("src.services.fund_data_provider.strip_fund_suffix",
                    side_effect=lambda c: c.split(".")[0]), \
         mock.patch.object(AnalysisService, "_run_fund_llm",
                           return_value=llm_data) as m:
        svc = AnalysisService()
        out = svc._analyze_fund_stock("006229.FUND", query_id="q1",
                                      report_language="zh")
        m.assert_called_once()
        assert out["report"]["meta"]["report_type"] == "fund"
        assert out["report"]["meta"]["stock_code"] == "006229.FUND"
        assert out["report"]["fund"]["fund_name"] == "中欧医疗创新股票C"
        # strip_fund_suffix 只用于 fund_code，报告响应保留原始股票代码
        assert out["report"]["fund"]["sentiment_score"] == 60


def test_analyze_fund_internal_llm_method_reuses_analyzer():
    # 验证 _run_fund_llm 复用 GeminiAnalyzer.run_fund_analysis（不复制整套 analyzer）。
    cnt = {"calls": 0}

    class _FakeAnalyzer:
        def run_fund_analysis(self, system_prompt, user_prompt, report_language=None):
            cnt["calls"] += 1
            assert "基金" in system_prompt or "场外" in system_prompt
            assert user_prompt
            return {"fund_name": "X"}

    with mock.patch("src.analyzer.GeminiAnalyzer", return_value=_FakeAnalyzer()), \
         mock.patch("src.analyzer._get_fund_system_prompt", return_value="你是基金分析师"):
        svc = AnalysisService()
        out = svc._run_fund_llm({"fund_code": "006229"}, "zh")
    assert cnt["calls"] == 1
    assert out == {"fund_name": "X"}

# 离线测试：fund:<code> 路由标记、ReportType.FUND、确定性映射契约。
from data_provider.fund_fetcher import is_fund_code
from src.enums import ReportType
from src.services.fund_analysis import build_fund_report, map_fund_report_to_report_result
from data_provider.fund_fetcher import NavRecord, FundProfile, compute_metrics


def test_report_type_has_fund():
    assert ReportType.from_str("fund") == ReportType.FUND


def test_route_marker():
    assert is_fund_code("fund:003095") is True
    assert is_fund_code("600519") is False


def test_map_report_to_result():
    navs = [NavRecord(date=str(i), unit_nav=round(1.0 + i * 0.001, 4), acc_nav=0.0, change_pct=0.1) for i in range(120)]
    prof = FundProfile(code="003095", name="中欧医疗健康混合A", nav_history=navs, **(compute_metrics(navs) or {}))
    report = build_fund_report(prof)
    got = map_fund_report_to_report_result(report, report_language="zh")
    assert got.code == "003095"
    assert got.sentiment_score == 50
    assert got.dashboard["report_type"] == "fund"
    assert got.dashboard["not_investment_advice"] is True
    assert "不构成投资建议" in got.analysis_summary

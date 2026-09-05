# tests/test_fund_analysis.py
from data_provider.fund_fetcher import NavRecord
from src.services.fund_analysis import build_fund_report

def _profile():
    navs = []
    nv = 1.0
    for d in range(120):
        nv *= 1.001  # 平稳上行
        navs.append(NavRecord(date=str(d), unit_nav=round(nv,4), acc_nav=0.0, change_pct=0.1))
    from data_provider.fund_fetcher import FundProfile, compute_metrics
    m = compute_metrics(navs)
    return FundProfile(code="003095", name="中欧医疗健康混合A", nav_history=navs, **m)

def test_build_fund_report_shape():
    r = build_fund_report(_profile())
    assert r["report_type"] == "fund"
    assert "operation_advice" in r
    assert "风险" in r["summary"] or "净值" in r["summary"]

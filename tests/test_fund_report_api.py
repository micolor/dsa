# -*- coding: utf-8 -*-
"""基金报告 API 层贯通测试。

断言 `_build_analysis_report` 把后端 report 块的 `fund` payload 原样装进
`report.details.fund`，使前端 `FundReportView` 能读到基金数据。
"""

from api.v1.endpoints.analysis import _build_analysis_report


def test_build_analysis_report_carries_fund_payload():
    # 手动构造后端报告输入，模拟 fund response 的 report 结构（Task 6 _build_fund_response）
    report_data = {
        "meta": {
            "query_id": "q1",
            "stock_code": "006229",
            "stock_name": "中欧医疗创新股票C",
            "report_type": "fund",
            "report_language": "zh",
        },
        "fund": {
            "fund_name": "中欧医疗创新股票C",
            "manager": "xxx",
            "sentiment_score": 60,
            "top_holdings": [
                {"code": "600519", "name": "贵州茅台", "ratio": 8.5,
                 "market_value": "1.2亿", "quarter": "2026Q1"},
            ],
        },
    }
    out = _build_analysis_report(report_data, "q1", "006229", "中欧医疗创新股票C")
    d = out.details
    assert d is not None
    assert d.fund is not None
    assert d.fund["fund_name"] == "中欧医疗创新股票C"
    assert d.fund["top_holdings"][0]["code"] == "600519"

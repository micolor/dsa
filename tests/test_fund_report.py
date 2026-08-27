# tests/test_fund_report.py
from src.schemas.fund_report_schema import FundReportSchema


def test_fund_report_schema_accepts_valid():
    r = FundReportSchema(fund_name="中欧医疗创新股票C", interval_return=0.2,
                         sentiment_score=65)
    assert r.fund_name == "中欧医疗创新股票C"


def test_fund_report_schema_extra_allow():
    r = FundReportSchema(**{"unknown_key": 1})
    assert "unknown_key" in r.model_dump()

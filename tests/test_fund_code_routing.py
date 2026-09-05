# tests/test_fund_code_routing.py
from data_provider.fund_fetcher import is_fund_code, strip_fund_prefix


def test_fund_prefix_recognized():
    assert is_fund_code("fund:003095") is True
    assert strip_fund_prefix("fund:003095") == "003095"


def test_bare_stock_code_not_fund():
    assert is_fund_code("003816") is False   # 003816 是 A 股,不得判为基金
    assert is_fund_code("600519") is False
    assert is_fund_code("") is False


def test_bare_marker_prefix_ok():
    # 裸前缀「fund:」(无代码) 仍被识别为基金标记;剥离后为空。
    # 数据层只按标记归类,空码的下游命中由调用方负责(fail-open)。
    assert is_fund_code("fund:") is True
    assert strip_fund_prefix("fund:") == ""

# tests/test_fund_code_routing.py
import pytest
from data_provider.fund_fetcher import is_fund_code, strip_fund_prefix


def test_fund_prefix_recognized():
    assert is_fund_code("fund:003095") is True
    assert is_fund_code("FUND:003095") is True  # 大小写不敏感：任务队列可能大写化前缀
    assert strip_fund_prefix("fund:003095") == "003095"
    assert strip_fund_prefix("FUND:003095") == "003095"


def test_bare_stock_code_not_fund():
    assert is_fund_code("003816") is False   # 003816 是 A 股,不得判为基金
    assert is_fund_code("600519") is False
    assert is_fund_code("") is False


def test_bare_marker_prefix_ok():
    # 裸前缀「fund:」(无代码) 仍被识别为基金标记;剥离后为空。
    # 数据层只按标记归类,空码的下游命中由调用方负责(fail-open)。
    assert is_fund_code("fund:") is True
    assert strip_fund_prefix("fund:") == ""


def test_analysis_input_resolves_fund_prefix():
    # Web / API 触发的分析请求须能透传 fund:<6位代码>,并归一化为小写前缀,
    # 以便 pipeline 依 is_fund_code 路由到基金校验链路(而非当作股票代码解析)。
    from api.v1.endpoints.analysis import _resolve_and_normalize_input

    assert _resolve_and_normalize_input("fund:006229") == "fund:006229"
    assert _resolve_and_normalize_input("FUND:006229") == "fund:006229"
    assert _resolve_and_normalize_input(" fund:003095 ") == "fund:003095"
    # 裸 6 位 A 股代码仍按股票解析(不判为基金)。
    assert _resolve_and_normalize_input("006229") != "fund:006229"
    # 非法基金标记(非 6 位数字)落到常规校验,抛 400。
    with pytest.raises(Exception):
        _resolve_and_normalize_input("fund:abc")

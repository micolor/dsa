from api.v1.endpoints.analysis import (
    _is_obviously_invalid_analysis_input,
    _resolve_and_normalize_input,
)


def test_fund_suffix_not_obviously_invalid():
    assert _is_obviously_invalid_analysis_input("006229.FUND") is False
    assert _is_obviously_invalid_analysis_input("006229.OTC") is False


def test_fund_suffix_resolves_without_400():
    out = _resolve_and_normalize_input("006229.FUND")
    assert out == "006229.FUND"          # 保留后缀，供 analyze_stock 二次识别

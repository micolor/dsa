from src.services.fund_data_provider import is_fund_code, strip_fund_suffix
from src.services.stock_list_parser import parse_analysis_target, ParseStatus

def test_fund_suffix_recognized():
    assert is_fund_code("006229.FUND") is True
    assert is_fund_code("006229.OTC") is True

def test_bare_code_is_not_fund():
    assert is_fund_code("006229") is False      # 裸码保持证券语义
    assert is_fund_code("600519.SH") is False   # 显式证券后缀
    assert is_fund_code("00878.TW") is False    # ETF 后缀

def test_strip_fund_suffix():
    assert strip_fund_suffix("006229.FUND") == "006229"

def test_fund_suffix_parsed_as_fund():
    t = parse_analysis_target("006229.FUND")
    assert t.asset_type == "fund"

def test_bare_code_stays_stock():
    t = parse_analysis_target("006229")
    assert t.asset_type == "stock"     # 裸码仍按个股契约，不被误判为基金

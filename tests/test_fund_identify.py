from src.services.fund_data_provider import is_fund_code, strip_fund_suffix

def test_fund_suffix_recognized():
    assert is_fund_code("006229.FUND") is True
    assert is_fund_code("006229.OTC") is True

def test_bare_code_is_not_fund():
    assert is_fund_code("006229") is False      # 裸码保持证券语义
    assert is_fund_code("600519.SH") is False   # 显式证券后缀
    assert is_fund_code("00878.TW") is False    # ETF 后缀

def test_strip_fund_suffix():
    assert strip_fund_suffix("006229.FUND") == "006229"

_FUND_SUFFIXES = ("FUND", "OTC")

def is_fund_code(code: str) -> bool:
    """显式后缀才判定为场外基金；裸码/证券后缀保持原语义，避免误判。
    场外基金 6 位代码与 A 股证券/ETF 存在天然歧义，不做纯数字推断。"""
    if not code:
        return False
    upper = code.strip().upper()
    for suffix in _FUND_SUFFIXES:
        if upper.endswith("." + suffix) or upper.endswith("_" + suffix):
            return True
    return False

def strip_fund_suffix(code: str) -> str:
    """返回去掉基金后缀后的 6 位数字代码。"""
    upper = code.strip().upper()
    for suffix in _FUND_SUFFIXES:
        if upper.endswith("." + suffix):
            return code.strip()[: -len("." + suffix)]
        if upper.endswith("_" + suffix):
            return code.strip()[: -len("_" + suffix)]
    return code.strip()

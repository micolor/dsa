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


import pandas as pd
from datetime import datetime


class FundDataProvider:
    """场外基金数据源：复用 akshare 净值/持仓接口。不进证券 failover 循环。"""

    @staticmethod
    def _normalize_nav_df(df: pd.DataFrame):
        from src.schemas.fund_report_schema import FundNavRow
        rows = []
        for _, r in df.iterrows():
            try:
                d = datetime.strptime(str(r["净值日期"]), "%Y-%m-%d").date()
            except Exception:
                continue
            rows.append(FundNavRow(
                date=d,
                unit_nav=float(r.get("单位净值") or r.get("单位净值估算") or 0) or None,
                daily_growth=float(r.get("日增长率", 0) or 0),
            ))
        rows.sort(key=lambda x: x.date)
        return rows

    def get_nav_series(self, code: str):
        """返回 (rows, data_quality)。data_quality 含 provider/as_of/missing_fields。"""
        import akshare as ak
        code6 = strip_fund_suffix(code)
        df = ak.fund_open_fund_info_em(symbol=code6, indicator="单位净值走势", period="成立来")
        rows = self._normalize_nav_df(df)
        q = {"provider": "akshare", "as_of": df.iloc[-1]["净值日期"] if len(df) else None,
             "missing_fields": []}
        return rows, q

    def get_holdings(self, code: str, year: str = "2025"):
        import akshare as ak
        code6 = strip_fund_suffix(code)
        df = ak.fund_portfolio_hold_em(symbol=code6, date=year)
        from src.schemas.fund_report_schema import FundHoldingRow
        rows = []
        for _, r in df.iterrows():
            rows.append(FundHoldingRow(
                code=str(r.get("股票代码") or ""),
                name=str(r.get("股票名称") or ""),
                ratio=float(r.get("占净值比例", 0) or 0),
                market_value=float(r.get("持仓市值", 0) or 0),
                quarter=str(r.get("季度") or ""),
            ))
        return rows

    @staticmethod
    def stats_from_nav(rows):
        if len(rows) < 2:
            return {"interval_return": None, "max_drawdown": None,
                    "current_drawdown": None, "period_days": len(rows)}
        first = rows[0].unit_nav
        last = rows[-1].unit_nav
        if not first or not last:
            return {"interval_return": None, "max_drawdown": None,
                    "current_drawdown": None, "period_days": len(rows)}
        interval_return = last / first - 1
        peak = -1e18
        max_dd = 0.0
        cur_peak = rows[-1].unit_nav
        cur_dd = 0.0
        for r in rows:
            if r.unit_nav is None:
                continue
            peak = max(peak, r.unit_nav)
            dd = (peak - r.unit_nav) / peak if peak else 0.0
            max_dd = max(max_dd, dd)
            cur_peak = max(cur_peak, r.unit_nav)
            if r.unit_nav:
                cur_dd = (cur_peak - r.unit_nav) / cur_peak
        return {"interval_return": interval_return, "max_drawdown": max_dd,
                "current_drawdown": cur_dd, "period_days": len(rows)}

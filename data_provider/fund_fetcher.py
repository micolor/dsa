# data_provider/fund_fetcher.py
from __future__ import annotations
import math, statistics
from dataclasses import dataclass, field
from typing import List, Optional
import requests

TRADING_DAYS = 252

@dataclass
class NavRecord:
    date: str
    unit_nav: float
    acc_nav: float
    change_pct: float

@dataclass
class FundProfile:
    code: str
    name: str
    fund_type: str = ""
    nav_history: List[NavRecord] = field(default_factory=list)
    holdings: List[dict] = field(default_factory=list)
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_1y: Optional[float] = None
    max_drawdown: Optional[float] = None
    annual_volatility: Optional[float] = None
    sharpe: Optional[float] = None

def is_fund_code(code: str) -> bool:
    return bool(code) and code.startswith("fund:")

def strip_fund_prefix(code: str) -> str:
    return code[5:] if is_fund_code(code) else code

def parse_lsjz(payload: dict) -> List[NavRecord]:
    rows = (payload.get("Data") or {}).get("LSJZList") or []
    recs = []
    for r in rows:
        try:
            recs.append(NavRecord(
                date=r.get("FSRQ", ""),
                unit_nav=float(r.get("DWJZ") or 0),
                acc_nav=float(r.get("LJJZ") or 0),
                change_pct=float(r.get("JZZZL") or 0),
            ))
        except (TypeError, ValueError):
            continue
    return recs

def parse_pingzhongdata(raw: str) -> str:
    name = ""
    if 'fS_name = ' in raw:
        name = raw.split('fS_name = "', 1)[1].split('"', 1)[0]
    return name

def compute_metrics(history: List[NavRecord], risk_free: float = 0.02) -> dict:
    if not history:
        return {}
    navs = [h.unit_nav for h in history]
    n = len(navs)

    def ret(days: int):
        if n <= days:
            return None
        start = navs[-days - 1]
        end = navs[-1]
        if start <= 0:
            return None
        return end / start - 1.0

    # max drawdown
    peak = -math.inf; mdd = 0.0
    for v in navs:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak else 0.0
        mdd = min(mdd, dd)
    # daily returns -> annualized volatility + sharpe
    daily = [(navs[i] / navs[i-1] - 1.0) for i in range(1, n) if navs[i-1]]
    vol = (statistics.pstdev(daily) * math.sqrt(TRADING_DAYS)) if len(daily) >= 2 else None
    annual_ret = ret(TRADING_DAYS)
    sharpe = None
    if vol and annual_ret is not None and vol > 0:
        sharpe = (annual_ret - risk_free) / vol
    return {
        "return_1m": ret(22), "return_3m": ret(66), "return_6m": ret(126),
        "return_1y": ret(TRADING_DAYS), "max_drawdown": mdd,
        "annual_volatility": vol, "sharpe": sharpe,
    }

_FUND_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
_FUND_INFO_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_HEADERS = {
    "Referer": "https://fundf10.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

class FundFetcher:
    def get_profile(self, code: str, history_len: int = 250) -> FundProfile:
        base = strip_fund_prefix(code)
        nav = self._fetch_nav(base, history_len)
        name = self._fetch_name(base)
        holdings = []
        metrics = compute_metrics(nav)
        return FundProfile(code=base, name=name, nav_history=nav,
                           holdings=holdings, **metrics)

    def _fetch_nav(self, code: str, limit: int) -> List[NavRecord]:
        recs: List[NavRecord] = []
        page_size = 60
        page = 1
        while len(recs) < limit:
            r = requests.get(_FUND_NAV_URL,
                params=dict(fundCode=code, pageIndex=page, pageSize=page_size),
                headers=_HEADERS, timeout=20)
            r.raise_for_status()
            batch = parse_lsjz(r.json())
            if not batch:          # only stop when exhausted
                break
            recs.extend(batch)
            page += 1
        return recs[:limit]

    def _fetch_name(self, code: str) -> str:
        try:
            r = requests.get(_FUND_INFO_URL.format(code=code), headers=_HEADERS, timeout=20)
            return parse_pingzhongdata(r.text)
        except Exception:
            return ""

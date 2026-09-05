# data_provider/fund_fetcher.py
from __future__ import annotations
import calendar, html, math, re, statistics
from dataclasses import dataclass, field
from datetime import date
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
class FundHolding:
    """单只重仓股：来自东财 F10 持仓明细（jjcc）最新季度。"""
    rank: int
    stock_code: str
    stock_name: str
    pct_of_nav: float       # 占净值比例（百分比值，如 10.39）
    share_count: float      # 持股数（万股）
    market_value: float     # 持仓市值（万元）

@dataclass
class FundAssetAllocation:
    """基金资产配置：来自东财 F10 资产配置页，最新报告期一行。"""
    report_date: str
    stock_pct: float        # 股票占净值比（%）
    bond_pct: float         # 债券占净值比（%）
    cash_pct: float         # 现金占净值比（%）
    net_asset: float        # 净资产（亿元）

@dataclass
class FundProfile:
    code: str
    name: str
    fund_type: str = ""
    nav_history: List[NavRecord] = field(default_factory=list)
    holdings: List[FundHolding] = field(default_factory=list)
    asset_allocation: Optional[FundAssetAllocation] = None
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_1y: Optional[float] = None
    max_drawdown: Optional[float] = None
    annual_volatility: Optional[float] = None
    sharpe: Optional[float] = None

def is_fund_code(code: str) -> bool:
    # 大小写不敏感：任务队列 / 调用方可能把 fund: 前缀大写化（如 FUND:006229），
    # 且 is_fund_code 是 pipeline 判别基金/股票的唯一开关，须对不同大小写一致识别。
    return bool(code) and code.lower().startswith("fund:")

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


def _clean_cell(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _parse_num(text: str) -> float:
    return float(str(text or "").replace(",", "").replace("%", "").strip())


def _parse_table_rows(raw: str) -> List[List[str]]:
    """抽取 HTML 中的表格数据行（<tr> 中的 <td>），清理成字符串列表。"""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw or "", re.S)
    out = []
    for row in rows:
        cells = [_clean_cell(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if cells:
            out.append(cells)
    return out


def _is_pct(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%", (text or "").strip()))


def _is_cjk(text: str) -> bool:
    return bool(re.match(r"[一-鿿]", text or ""))


def parse_jjcc(raw: str) -> List[FundHolding]:
    """解析东财 F10 持仓明细（jjcc）返回的最新季度十大重仓股。

    表格列（含表头）：序号 / 股票代码 / 股票名称 / 最新价 / 涨跌幅 /
    相关资讯 / 占净值比例 / 持股数(万股) / 持仓市值(万元)。

    - 「占净值比例」列不按固定索引定位：该列会出现偏移（最新价/涨跌幅有无），
      故取首个含 ``%`` 的单元格作为占净值比例，其后两列为持股数与市值。
    - 只保留序号 1..10；无股票持仓（纯债/货基）或解析失败 → 空列表。
    """
    if not raw:
        return []
    by_rank: dict = {}
    for cells in _parse_table_rows(raw):
        if not cells or not cells[0].isdigit():
            continue
        rank = int(cells[0])
        if rank < 1 or rank > 10:
            continue
        pct_i = next((i for i, c in enumerate(cells) if _is_pct(c)), None)
        if pct_i is None:
            continue

        def num(i: int) -> float:
            if 0 <= i < len(cells):
                try:
                    return _parse_num(cells[i])
                except (TypeError, ValueError):
                    return 0.0
            return 0.0

        tail = cells[1:6]
        by_rank[rank] = FundHolding(
            rank=rank,
            stock_code=cells[1] if len(cells) > 1 else "",
            stock_name=next((c for c in tail if _is_cjk(c)), ""),
            pct_of_nav=num(pct_i),
            share_count=num(pct_i + 1),
            market_value=num(pct_i + 2),
        )
    return [by_rank[r] for r in sorted(by_rank)][:10]


def parse_asset_allocation(raw: str) -> Optional[FundAssetAllocation]:
    """解析东财 F10 资产配置页（zcpz）最新报告期一行。

    表格列：报告期 / 股票占净值比 / 债券占净值比 / 现金占净值比 / 净资产(亿元)。
    只取首个满足「报告期为 YYYY-MM-DD」的行（页面按报告期倒序）。
    """
    if not raw:
        return None
    for cells in _parse_table_rows(raw):
        if len(cells) < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        try:
            return FundAssetAllocation(
                report_date=cells[0],
                stock_pct=_parse_num(cells[1]),
                bond_pct=_parse_num(cells[2]),
                cash_pct=_parse_num(cells[3]),
                net_asset=_parse_num(cells[4]),
            )
        except (TypeError, ValueError):
            continue
    return None

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
_FUND_HOLDING_URL = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
_FUND_ASSET_ALLOC_URL = "http://fundf10.eastmoney.com/zcpz_{code}.html"

# 定期报告按季度披露；jjcc 需显式季度参数且当前季度存在发布滞后，故从最近结束的季度扫描。
_QUARTER_END_MONTHS = (3, 6, 9, 12)


def _recent_quarter_ends(count: int = 4, now: Optional[date] = None) -> List[tuple]:
    """从「最近一个已披露季度的季度末」往前倒退 count 个 (year, month)。

    季度末日期 <= 今天才视为已结束（否则本季尚未披露，回退一季）。
    """
    today = now or date.today()
    y, m = today.year, today.month
    qe_m = ((m - 1) // 3 + 1) * 3
    yy, mm = y, qe_m
    while (yy, mm, calendar.monthrange(yy, mm)[1]) > (today.year, today.month, today.day):
        yy, mm = (yy - 1, 12) if mm == 3 else (yy, mm - 3)
    out: List[tuple] = []
    for _ in range(count):
        out.append((yy, mm))
        yy, mm = (yy - 1, 12) if mm == 3 else (yy, mm - 3)
    return out
_HEADERS = {
    "Referer": "https://fundf10.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

class FundFetcher:
    def get_profile(self, code: str, history_len: int = 250) -> FundProfile:
        base = strip_fund_prefix(code)
        nav = self._fetch_nav(base, history_len)
        name = self._fetch_name(base)
        holdings = self._fetch_holdings(base)
        asset_alloc = self._fetch_asset_alloc(base)
        metrics = compute_metrics(nav)
        return FundProfile(code=base, name=name, nav_history=nav,
                           holdings=holdings, asset_allocation=asset_alloc,
                           **metrics)

    def _fetch_holdings(self, code: str) -> List[FundHolding]:
        """取最新定期报告的十大重仓股；无持仓（纯债/货基）或失败 → 空列表。"""
        for year, month in _recent_quarter_ends():
            holdings = self._fetch_jjcc_quarter(code, year, month)
            if holdings:
                return holdings
        return []

    def _fetch_jjcc_quarter(self, code: str, year: int, month: int) -> List[FundHolding]:
        try:
            r = requests.get(
                _FUND_HOLDING_URL,
                params=dict(type="jjcc", code=code, year=year, month=month, topline=10),
                headers=_HEADERS, timeout=20,
            )
            r.raise_for_status()
            return parse_jjcc(r.text)
        except Exception:
            return []

    def _fetch_asset_alloc(self, code: str) -> Optional[FundAssetAllocation]:
        try:
            r = requests.get(_FUND_ASSET_ALLOC_URL.format(code=code), headers=_HEADERS, timeout=20)
            r.raise_for_status()
            return parse_asset_allocation(r.text)
        except Exception:
            return None

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

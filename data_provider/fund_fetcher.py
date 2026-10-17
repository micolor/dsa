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
class FundDividendEvent:
    """基金分红：除息日与每份现金分红。"""

    date: str
    cash_per_share: float


@dataclass
class FundSplitEvent:
    """基金拆分/份额折算：折算日与份额乘数。"""

    date: str
    ratio: float


@dataclass
class FundDistributionEvents:
    """基金分红送配明细；``skipped_rows`` 记录无法解析的行数。"""

    dividends: List[FundDividendEvent] = field(default_factory=list)
    splits: List[FundSplitEvent] = field(default_factory=list)
    skipped_rows: int = 0


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

def _extract_table_html(raw: str, class_name: str) -> str:
    match = re.search(
        rf"<table[^>]*class=['\"][^'\"]*{re.escape(class_name)}[^'\"]*['\"][^>]*>(.*?)</table>",
        raw or "",
        re.S | re.I,
    )
    return match.group(1) if match else ""


def parse_fhsp(raw: str) -> FundDistributionEvents:
    """解析东财 F10 分红送配页（fhsp）。

    - ``cfxq`` 表：年份 / 权益登记日 / 除息日 / 每10份分红 / 分红发放日；
      分红金额换算为每份现金（除以 10），日期优先取除息日。
    - ``fhxq`` 表：年份 / 拆分折算日 / 拆分类型 / 拆分折算比例；
      ``1:1.4982`` 的右值作为份额乘数。

    空表或「暂无…信息!」返回空列表；无法解析的数据行计入 ``skipped_rows``，
    不做推断，避免把错误口径带进复盘指标。
    """
    events = FundDistributionEvents()
    for cells in _parse_table_rows(_extract_table_html(raw, "cfxq")):
        if len(cells) < 4:
            continue
        event_date = cells[2] or cells[1]
        amount = _DIVIDEND_AMOUNT_RE.search(cells[3])
        try:
            if not event_date or amount is None:
                raise ValueError("invalid dividend row")
            cash = float(amount.group(1)) / 10.0
            if cash <= 0:
                raise ValueError("non-positive dividend")
            events.dividends.append(FundDividendEvent(event_date, round(cash, 8)))
        except (TypeError, ValueError):
            events.skipped_rows += 1
    for cells in _parse_table_rows(_extract_table_html(raw, "fhxq")):
        if len(cells) < 4:
            continue
        ratio_match = _SPLIT_RATIO_RE.match(cells[3] or "")
        try:
            if not cells[1] or ratio_match is None:
                raise ValueError("invalid split row")
            ratio = float(ratio_match.group(1))
            if ratio <= 0:
                raise ValueError("non-positive split ratio")
            events.splits.append(FundSplitEvent(cells[1], ratio))
        except (TypeError, ValueError):
            events.skipped_rows += 1
    events.dividends.sort(key=lambda item: item.date)
    events.splits.sort(key=lambda item: item.date)
    return events


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
_FUND_FHSP_URL = "https://fundf10.eastmoney.com/fhsp_{code}.html"

_DIVIDEND_AMOUNT_RE = re.compile(r"每10份.*?([\d.]+)元")
_SPLIT_RATIO_RE = re.compile(r"^\s*1\s*:\s*([\d.]+)\s*$")

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

_FUND_NAV_PROVIDER = "eastmoney"

def _parse_nav_date(value: str) -> Optional[date]:
    """把东财净值日期字符串（YYYY-MM-DD）解析为 date；非法返回 None。"""
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except (TypeError, ValueError):
        return None

class FundFetcher:
    def get_latest_nav(self, code: str) -> Optional[tuple]:
        """按代码取最新单位净值，返回 (nav: float, date: date)；失败返回 None。

        只拉净值页一页，按日期取最大（不依赖接口返回顺序，兼容新旧两种排法），
        不取持仓/资产配置，供组合持仓按净值估值做轻量调用。
        """
        base = strip_fund_prefix(code)
        try:
            nav = self._fetch_nav(base, 60)
        except Exception:
            return None
        if not nav:
            return None
        latest = max(nav, key=lambda r: r.date)  # YYYY-MM-DD 字符串按时间序可比较
        if latest.unit_nav is None or latest.unit_nav <= 0:
            return None
        nav_date = _parse_nav_date(latest.date)
        return (float(latest.unit_nav), nav_date) if nav_date else None

    def get_profile(self, code: str, history_len: int = TRADING_DAYS + 1) -> FundProfile:
        """取基金档案。``history_len`` 默认比 ``TRADING_DAYS`` 多一条。

        区间收益按「末尾是最新、往前数 days 条」计算，因此要算满一年（252 个
        交易日）至少需要 253 条；默认值与 ``TRADING_DAYS`` 对齐，避免「近 1 年」
        因为窗口差一条而恒为空。
        """
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

    def get_nav_range(
        self,
        code: str,
        start_date: date,
        end_date: date,
        *,
        page_size: int = 60,
        max_pages: int = 100,
    ) -> List[NavRecord]:
        """取 ``[start_date, end_date]`` 区间内的净值，按日期正序返回。

        东财 lsjz 支持 ``startDate`` / ``endDate``，但分页行为仍需自己处理：
        接口按日期倒序返回，且可能夹带区间外记录。这里统一过滤并按日期去重，
        让评估服务可以稳定地用「锚点日期 + 第 N 条」定位窗口。

        区间无数据返回空列表；请求/解析异常向上抛，由调用方决定重试语义。
        """
        if start_date > end_date:
            return []
        base = strip_fund_prefix(code)
        start_key = start_date.isoformat()
        end_key = end_date.isoformat()
        by_date: dict = {}
        page = 1
        while page <= max_pages:
            batch = self._request_nav_page(
                base,
                page,
                page_size,
                start_date=start_date,
                end_date=end_date,
            )
            if not batch:  # 只在真正取空时停止：服务端可能把 pageSize 截到更小值
                break
            for record in batch:
                day = (record.date or "")[:10]
                if start_key <= day <= end_key:
                    by_date[day] = record
            page += 1
        return [by_date[day] for day in sorted(by_date)]

    def get_distribution_events(self, code: str) -> FundDistributionEvents:
        """取基金分红送配明细；网络或页面异常向上抛，由调用方降级。"""
        base = strip_fund_prefix(code)
        r = requests.get(_FUND_FHSP_URL.format(code=base), headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return parse_fhsp(r.text)

    def _request_nav_page(
        self,
        code: str,
        page: int,
        page_size: int,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[NavRecord]:
        params = dict(fundCode=code, pageIndex=page, pageSize=page_size)
        if start_date is not None:
            params["startDate"] = start_date.isoformat()
        if end_date is not None:
            params["endDate"] = end_date.isoformat()
        r = requests.get(_FUND_NAV_URL, params=params, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return parse_lsjz(r.json())

    def _fetch_nav(self, code: str, limit: int) -> List[NavRecord]:
        """取最近 ``limit`` 条净值，返回**按净值日期正序（旧 → 新）**的序列。

        东财 lsjz 按日期倒序返回，且接口历史上的排法不保证稳定（见
        :meth:`get_latest_nav`）。顺序在这里归一化，消费方才能约定「末尾是最新」：
        :func:`compute_metrics` 的区间收益与最大回撤、``build_fund_report`` 的
        ``latest_nav``、以及基金 LLM prompt 的「最新净值」都依赖这个约定。
        """
        recs: List[NavRecord] = []
        page_size = 60
        page = 1
        while len(recs) < limit:
            batch = self._request_nav_page(code, page, page_size)
            if not batch:          # only stop when exhausted
                break
            recs.extend(batch)
            page += 1
        # 先排序再截断：分页取多了要留下**最近**的 limit 条，而不是最旧的。
        return sorted(recs, key=lambda r: r.date)[-limit:]

    def _fetch_name(self, code: str) -> str:
        try:
            r = requests.get(_FUND_INFO_URL.format(code=code), headers=_HEADERS, timeout=20)
            return parse_pingzhongdata(r.text)
        except Exception:
            return ""

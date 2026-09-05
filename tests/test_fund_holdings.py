# 离线测试：基金十大重仓股 + 资产配置的解析与报告透传。
# fixture 结构来自真实东财响应（006229）——
#   jjcc：最新价/涨跌幅为空串，首个 `%` 稳定落在「占净值比例」列；
#   zcpz：报告期 / 股票占比 / 债券占比 / 现金占比 / 净资产(亿元)，表头行无日期会被跳过。
import pytest
from data_provider.fund_fetcher import (
    FundHolding,
    FundAssetAllocation,
    FundProfile,
    NavRecord,
    parse_jjcc,
    parse_asset_allocation,
    compute_metrics,
)
from src.services.fund_analysis import (
    build_fund_report,
    map_fund_report_to_report_result,
)

JJCC_HTML = """
<table>
<tr><th>序号</th><th>股票代码</th><th>股票名称</th><th>最新价</th><th>涨跌幅</th><th>相关资讯</th><th>占净值比例</th><th>持股数(万股)</th><th>持仓市值(万元)</th></tr>
<tr><td>1</td><td>603259</td><td>药明康德</td><td></td><td></td><td>变动详情股吧行情</td><td>10.39%</td><td>554.42</td><td>69,031.27</td></tr>
<tr><td>2</td><td>688506</td><td>百利天恒</td><td></td><td></td><td>变动详情股吧行情</td><td>9.62%</td><td>217.36</td><td>63,910.89</td></tr>
</table>
"""

ZCPZ_HTML = """
<table>
<tr><th>报告期</th><th>股票占净值比</th><th>债券占净值比</th><th>现金占净值比</th><th>净资产(亿元)</th></tr>
<tr><td>2026-06-30</td><td>94.84%</td><td>1.80%</td><td>5.95%</td><td>66.43</td></tr>
<tr><td>2026-03-31</td><td>94.93%</td><td>0.27%</td><td>5.72%</td><td>69.28</td></tr>
</table>
"""


def test_parse_jjcc_extracts_top_holdings():
    rows = parse_jjcc(JJCC_HTML)
    assert len(rows) == 2
    first = rows[0]
    assert isinstance(first, FundHolding)
    assert first.rank == 1
    assert first.stock_code == "603259"
    assert first.stock_name == "药明康德"
    assert first.pct_of_nav == 10.39
    assert first.share_count == 554.42
    assert first.market_value == 69031.27
    assert rows[1].pct_of_nav == 9.62


def test_parse_jjcc_empty_and_short_rows():
    assert parse_jjcc("") == []
    # 无 `%` 列（如货基无股票持仓）→ 空列表，而非报错。
    assert parse_jjcc("<tr><td>1</td><td>600000</td><td>某债基</td><td></td></tr>") == []


def test_parse_asset_allocation_takes_latest_report_date():
    a = parse_asset_allocation(ZCPZ_HTML)
    assert isinstance(a, FundAssetAllocation)
    assert a.report_date == "2026-06-30"
    assert a.stock_pct == 94.84
    assert a.bond_pct == 1.80
    assert a.cash_pct == 5.95
    assert a.net_asset == 66.43


def test_parse_asset_allocation_no_date_row():
    # 表头行（无 `YYYY-MM-DD`）被跳过；全表无日期 → None。
    assert parse_asset_allocation("<table><tr><th>报告期</th></tr></table>") is None
    assert parse_asset_allocation("") is None


def _fund_profile_with_data():
    navs = [NavRecord(date=str(i), unit_nav=round(1.0 + i * 0.001, 4), acc_nav=0.0, change_pct=0.1)
            for i in range(120)]
    holdings = [
        FundHolding(rank=1, stock_code="603259", stock_name="药明康德",
                    pct_of_nav=10.39, share_count=554.42, market_value=69031.27),
    ]
    alloc = FundAssetAllocation(report_date="2026-06-30", stock_pct=94.84,
                                bond_pct=1.80, cash_pct=5.95, net_asset=66.43)
    return FundProfile(code="006229", name="工银医药健康", nav_history=navs,
                       holdings=holdings, asset_allocation=alloc,
                       **(compute_metrics(navs) or {}))


def test_build_fund_report_includes_holdings_and_allocation():
    report = build_fund_report(_fund_profile_with_data())
    assert report["report_type"] == "fund"
    assert report["holdings"] == [{
        "rank": 1, "stock_code": "603259", "stock_name": "药明康德",
        "pct_of_nav": 10.39, "share_count": 554.42, "market_value": 69031.27,
    }]
    assert report["asset_allocation"] == {
        "report_date": "2026-06-30", "stock_pct": 94.84,
        "bond_pct": 1.80, "cash_pct": 5.95, "net_asset": 66.43,
    }
    assert report["not_investment_advice"] is True


def test_map_report_to_result_passes_dashboard_holdings():
    report = build_fund_report(_fund_profile_with_data())
    got = map_fund_report_to_report_result(report, report_language="zh")
    assert got.dashboard["holdings"][0]["stock_name"] == "药明康德"
    assert got.dashboard["asset_allocation"]["stock_pct"] == 94.84


def test_fund_report_has_no_trade_advice():
    report = build_fund_report(_fund_profile_with_data())
    text = (str(report["summary"]) + str(report["operation_advice"]) + str(report["trend_prediction"]))
    for banned in ("买入", "卖出", "建仓", "加仓", "减仓", "清仓", "止盈", "止损", "持仓"):
        assert banned not in text
    assert "不构成投资建议" in report["summary"]
    assert report["not_investment_advice"] is True

# tests/test_fund_fetcher.py
import math
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from data_provider.fund_fetcher import (
    TRADING_DAYS,
    FundFetcher,
    parse_lsjz, compute_metrics, parse_pingzhongdata,
    is_fund_code, strip_fund_prefix, NavRecord,
)


def _mk(dates, navs):
    recs = []
    for d, n in zip(dates, navs):
        acc = n * 1.5
        recs.append(NavRecord(date=d, unit_nav=n, acc_nav=acc, change_pct=0.0))
    return recs


def test_parse_lsjz_maps_fields():
    payload = {"Data": {"LSJZList": [
        {"FSRQ": "2026-09-04", "DWJZ": "1.9360", "LJJZ": "2.1740", "JZZZL": "-1.07"},
    ]}}
    recs = parse_lsjz(payload)
    assert len(recs) == 1
    assert recs[0].date == "2026-09-04"
    assert abs(recs[0].unit_nav - 1.936) < 1e-9
    assert abs(recs[0].acc_nav - 2.174) < 1e-9
    assert abs(recs[0].change_pct - -1.07) < 1e-9


def test_parse_lsjz_skips_bad_rows():
    payload = {"Data": {"LSJZList": [
        {"FSRQ": "2026-09-04", "DWJZ": "bad", "LJJZ": "2.17", "JZZZL": "-1.0"},
        {"FSRQ": "2026-09-03", "DWJZ": "1.91", "LJJZ": "2.14", "JZZZL": "0.5"},
    ]}}
    recs = parse_lsjz(payload)
    assert len(recs) == 1
    assert recs[0].date == "2026-09-03"


def test_parse_lsjz_empty():
    assert parse_lsjz({}) == []
    assert parse_lsjz({"Data": {}}) == []
    assert parse_lsjz({"Data": {"LSJZList": []}}) == []


def test_parse_pingzhongdata_extracts_name():
    raw = 'var fS_name = "广发稳健增长";var fS_code = "003095";'
    name = parse_pingzhongdata(raw)
    assert name == "广发稳健增长"


def test_parse_pingzhongdata_no_name():
    assert parse_pingzhongdata("var a = 1;") == ""


def test_is_fund_code():
    assert is_fund_code("fund:003095") is True
    assert is_fund_code("003095") is False
    assert is_fund_code("") is False
    assert is_fund_code("fund:") is True


def test_strip_fund_prefix():
    assert strip_fund_prefix("fund:003095") == "003095"
    assert strip_fund_prefix("003095") == "003095"


def test_compute_metrics_empty():
    assert compute_metrics([]) == {}


def test_compute_metrics_return_1m_and_drawdown():
    # 120 trading rows: unit_nav starts at 1.0, rises to 1.5, then falls to 1.2
    navs = list(range(1, 61)) + list(range(60, 30, -1)) + [30]
    navs = [v / 40.0 for v in navs]   # ~0.025 .. ~1.5, then down to 0.75
    recs = []
    for i, v in enumerate(navs):
        recs.append(NavRecord(date=str(i), unit_nav=round(v, 6), acc_nav=round(v * 1.5, 6), change_pct=0.0))
    m = compute_metrics(recs)
    assert m["return_1m"] is not None
    assert m["max_drawdown"] < 0      # 序列存在峰值回落
    assert m["return_1y"] is None     # 120 行不足以算 1 年(252 日)


def test_compute_metrics_year():
    # 253 rows -> return_1y computable
    navs = [1.0 + i * 0.001 for i in range(253)]
    recs = [NavRecord(date=str(i), unit_nav=v, acc_nav=v * 1.5, change_pct=0.0)
            for i, v in enumerate(navs)]
    m = compute_metrics(recs)
    assert m["return_1y"] is not None
    assert m["return_1m"] is not None
    assert m["max_drawdown"] == 0.0
    assert m["annual_volatility"] is not None


# --- 净值序列顺序契约 ---------------------------------------------------------
#
# 东财 lsjz 接口按净值日期倒序返回，而 compute_metrics / build_fund_report /
# build_fund_llm_user_prompt 都按「列表末尾是最新一条」取数。顺序必须在数据源
# 边界归一化，否则：
#   - latest_nav 会取到窗口内最旧的一条（净值显示为一年前）；
#   - 区间收益 / 最大回撤会在时间倒序的序列上计算，结论与事实相反。
# 下面这组用例守的就是这个边界。


def _lsjz_page(rows):
    """按东财 lsjz 的响应结构包装一页净值。"""
    return {"Data": {"LSJZList": [
        {"FSRQ": d, "DWJZ": str(v), "LJJZ": str(round(v * 1.5, 4)), "JZZZL": "0.00"}
        for d, v in rows
    ]}}


def _nav_rows(n, newest_first=False):
    """生成 n 个连续交易日的 (date, nav)，nav 逐日递增，便于断言方向。"""
    start = date(2025, 1, 1)
    rows = [((start + timedelta(days=i)).isoformat(), 1.0 + i * 0.001) for i in range(n)]
    return list(reversed(rows)) if newest_first else rows


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fetch_nav_with_pages(fetcher, pages, limit):
    """让 requests.get 按页返回给定 payload，最后一页之后返回空页。"""
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        idx = calls["n"]
        calls["n"] += 1
        if idx < len(pages):
            return _FakeResponse(pages[idx])
        return _FakeResponse({"Data": {"LSJZList": []}})

    with patch("data_provider.fund_fetcher.requests.get", side_effect=fake_get):
        return fetcher._fetch_nav("003095", limit)


def test_fetch_nav_normalizes_to_chronological_order():
    """接口倒序返回时，_fetch_nav 也必须给出「旧 → 新」的正序序列。"""
    rows = _nav_rows(80, newest_first=True)
    pages = [_lsjz_page(rows[:60]), _lsjz_page(rows[60:])]

    recs = _fetch_nav_with_pages(FundFetcher(), pages, limit=80)

    dates = [r.date for r in recs]
    assert dates == sorted(dates), "nav_history 必须按日期正序，末尾为最新"
    assert recs[-1].unit_nav == pytest.approx(1.079)
    assert recs[0].unit_nav == pytest.approx(1.0)


def test_fetch_nav_keeps_the_most_recent_records():
    """分页超出 limit 时，截断必须保留最近的 limit 条，而不是最旧的。"""
    rows = _nav_rows(120, newest_first=True)
    pages = [_lsjz_page(rows[:60]), _lsjz_page(rows[60:])]

    recs = _fetch_nav_with_pages(FundFetcher(), pages, limit=100)

    assert len(recs) == 100
    # 最新一条 = 全部 120 条里的最后一条
    assert recs[-1].unit_nav == pytest.approx(1.0 + 119 * 0.001)
    # 最旧一条 = 120 条里倒数第 100 条
    assert recs[0].unit_nav == pytest.approx(1.0 + 20 * 0.001)


def test_fetch_nav_tolerates_ascending_payload():
    """接口若改为正序返回，归一化逻辑不得把序列倒过来。"""
    rows = _nav_rows(80, newest_first=False)
    pages = [_lsjz_page(rows[:60]), _lsjz_page(rows[60:])]

    recs = _fetch_nav_with_pages(FundFetcher(), pages, limit=80)

    assert [r.date for r in recs] == sorted(r.date for r in recs)
    assert recs[-1].unit_nav == pytest.approx(1.079)


def test_default_history_window_can_compute_one_year_return():
    """默认窗口必须大于 TRADING_DAYS，否则「近 1 年」永远是空值。"""
    import inspect

    default_len = inspect.signature(FundFetcher.get_profile).parameters["history_len"].default
    assert default_len > TRADING_DAYS

    recs = _mk([(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(default_len)],
               [1.0 + i * 0.001 for i in range(default_len)])
    assert compute_metrics(recs)["return_1y"] is not None

# tests/test_fund_fetcher.py
import math
from data_provider.fund_fetcher import (
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

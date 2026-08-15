# -*- coding: utf-8 -*-
"""
Tests for BaostockFundamentalAdapter (A-share fundamentals fallback).

These tests mock the Baostock session (login/logout + query result sets) so no
network call happens; they verify code conversion, period-backoff selection,
field mapping and status semantics.
"""

import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.baostock_fundamental_adapter import BaostockFundamentalAdapter


class _FakeRs:
    def __init__(self, error_code="0", error_msg="", fields=None, rows=None):
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = fields or []
        self._rows = rows or []
        self._idx = -1

    def next(self):
        self._idx += 1
        return self._idx < len(self._rows)

    def get_row_data(self):
        return self._rows[self._idx]


def _profit_row(report_date="2026-03-31", pub_date="2026-04-30"):
    return ["600519", pub_date, report_date, "10.57", "52.22", "89.76", "281.5", "1.01", "28153.0", "1000.0", "1000.0"]


def _growth_row(report_date="2026-03-31", pub_date="2026-04-30"):
    return ["600519", pub_date, report_date, "5.0", "3.0", "1.37", "1.2", "1.47"]


class _FakeFetcher:
    """Minimal BaostockFetcher stand-in exercising the adapter's contract."""

    def __init__(self, profit_rows, growth_rows):
        self._profit_rows = profit_rows
        self._growth_rows = growth_rows
        self._calls = []

    def _convert_stock_code(self, code):
        # mirror BaostockFetcher conversion: sh.600519 / sz.000001
        code = code.strip()
        if code.startswith("6") or code.startswith("9"):
            return f"sh.{code}"
        return f"sz.{code}"

    @contextmanager
    def _baostock_session(self):
        class _FakeBS:
            def query_profit_data(self, code, year, quarter):
                self._calls.append(("profit", code, year, quarter))
                rows = [r for r in self._profit_rows if r[2] == f"{year}-03-31"]
                return _FakeRs(fields=["code", "pubDate", "statDate", "roeAvg", "npMargin", "gpMargin", "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"], rows=rows)

            def query_growth_data(self, code, year, quarter):
                self._calls.append(("growth", code, year, quarter))
                rows = [r for r in self._growth_rows if r[2] == f"{year}-03-31"]
                return _FakeRs(fields=["code", "pubDate", "statDate", "YOYEquity", "YOYAsset", "YOYNI", "YOYEPSBasic", "YOYPNI"], rows=rows)

        bs = _FakeBS()
        bs._calls = self._calls
        bs._profit_rows = self._profit_rows
        bs._growth_rows = self._growth_rows
        yield bs


class TestBaostockFundamentalAdapter(unittest.TestCase):
    def test_maps_profit_and_growth_fields(self) -> None:
        profit = _profit_row()
        growth = _growth_row()
        adapter = BaostockFundamentalAdapter(fetcher=_FakeFetcher([profit], [growth]))
        result = adapter.get_fundamental_bundle("600519")

        self.assertEqual(result["status"], "partial")
        self.assertAlmostEqual(result["growth"]["roe"], 10.57, places=6)
        self.assertAlmostEqual(result["growth"]["gross_margin"], 89.76, places=6)
        self.assertAlmostEqual(result["growth"]["net_profit_yoy"], 1.37, places=6)
        self.assertAlmostEqual(result["growth"]["net_profit_parent_yoy"], 1.47, places=6)

        fr = result["earnings"]["financial_report"]
        self.assertEqual(fr["report_date"], "2026-03-31")
        self.assertAlmostEqual(fr["roe"], 10.57, places=6)
        self.assertAlmostEqual(fr["net_margin"], 52.22, places=6)
        self.assertIn("growth:baostock_profit", result["source_chain"])
        self.assertIn("growth:baostock_growth", result["source_chain"])
        self.assertEqual(result["errors"], [])

    def test_not_supported_when_no_disclosed_report(self) -> None:
        # profit row is far in the future relative to today (not yet disclosed)
        future = "2999-12-31"
        profit = _profit_row(report_date=future, pub_date=future)
        adapter = BaostockFundamentalAdapter(fetcher=_FakeFetcher([profit], []))
        result = adapter.get_fundamental_bundle("600519")
        self.assertEqual(result["status"], "not_supported")
        self.assertEqual(result["growth"], {})
        self.assertEqual(result["earnings"], {})

    def test_convert_error_is_recorded(self) -> None:
        class _BadFetcher:
            def _convert_stock_code(self, _code):
                raise ValueError("bad code")

        adapter = BaostockFundamentalAdapter(fetcher=_BadFetcher())
        result = adapter.get_fundamental_bundle("600519")
        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("convert" in e for e in result["errors"]))

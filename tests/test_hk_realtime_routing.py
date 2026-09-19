# -*- coding: utf-8 -*-
"""
Regression tests for Hong Kong realtime quote routing.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.base import DataFetcherManager


class _DummyFetcher:
    def __init__(self, name: str, priority: int, result=None):
        self.name = name
        self.priority = priority
        self.result = result
        self.calls = []

    def get_realtime_quote(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class TestHKRealtimeRouting(unittest.TestCase):
    """Ensure HK realtime lookup does not fan out into A-share sources."""

    @patch("src.config.get_config")
    def test_manager_routes_hk_suffix_only_to_akshare_once(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority="tencent,akshare_sina,efinance,akshare_em,tushare",
        )

        efinance = _DummyFetcher("EfinanceFetcher", 0, result={"should": "not be called"})
        akshare = _DummyFetcher("AkshareFetcher", 1, result=None)
        tushare = _DummyFetcher("TushareFetcher", 2, result={"should": "not be called"})

        manager = DataFetcherManager(fetchers=[efinance, akshare, tushare])
        quote = manager.get_realtime_quote("1810.HK")

        self.assertIsNone(quote)
        self.assertEqual(akshare.calls, [(("HK01810",), {"source": "hk"})])
        self.assertEqual(efinance.calls, [])
        self.assertEqual(tushare.calls, [])


class TestHKSinaFallbackProvenance(unittest.TestCase):
    """备用链路（新浪）返回的行情必须把来源标成新浪而不是东财。"""

    def setUp(self):
        import data_provider.akshare_fetcher as akf

        akf._hk_realtime_cache["data"] = None
        akf._hk_realtime_cache["timestamp"] = 0
        akf._hk_realtime_cache["last_result"] = None
        # 熔断器是进程级单例，上一个用例失败过会污染这一次
        from data_provider.realtime_types import get_realtime_circuit_breaker

        get_realtime_circuit_breaker().reset()

    def test_sina_fallback_reports_akshare_sina(self):
        import pandas as pd

        from data_provider.akshare_fetcher import AkshareFetcher
        from data_provider.realtime_types import RealtimeSource

        sina_df = pd.DataFrame(
            [
                {
                    "代码": "00700",
                    "名称": "腾讯控股",
                    "最新价": 384.0,
                    "涨跌幅": 1.32,
                    "成交量": 12345678,
                }
            ]
        )
        fake_ak = SimpleNamespace(
            stock_hk_spot_em=lambda: (_ for _ in ()).throw(RuntimeError("em down")),
            stock_hk_spot=lambda: sina_df,
        )
        fetcher = AkshareFetcher()
        with patch.dict(sys.modules, {"akshare": fake_ak}), patch.object(
            fetcher, "_set_random_user_agent", return_value=None
        ), patch.object(fetcher, "_enforce_rate_limit", return_value=None):
            quote = fetcher._get_hk_realtime_quote("hk00700")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.source, RealtimeSource.AKSHARE_SINA)
        self.assertNotEqual(quote.source, RealtimeSource.AKSHARE_EM)


if __name__ == "__main__":
    unittest.main()

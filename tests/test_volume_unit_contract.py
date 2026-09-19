# -*- coding: utf-8 -*-
"""成交量单位契约：东方财富「手」→ 统一口径「股」。

东方财富（push2 快照 / push2his K 线，即 efinance 与 akshare 的 ``*_em`` 接口）
返回的成交量以「手」为单位，而本项目的统一口径是「股」：

- ``stock_daily.volume``（``src/storage.py``）注释为「成交量（股）」
- ``UnifiedRealtimeQuote.volume``（``data_provider/realtime_types.py``）注释为
  「成交量（股，与历史日线口径一致）」

修复前，日线入库的是「手」而实时行情是「股」，两者在
``pipeline._enhance_context`` 与 ``_augment_historical_with_realtime`` 里直接相除，
``volume_change_ratio`` / ``volume_ratio_5d`` 会稳定放大 100 倍（实测
``volume_ratio_5d = 100.00 → 放量上涨``，LLM 提示里出现「成交量较昨日变化约 100.00 倍」）。

单位依据来自 efinance 自带文档字符串中的真实样本
（``efinance/stock/getter.py``，贵州茅台 600519）：

    2021-07-29  成交量 63864  成交额 1.129957e10  换手率 0.51%

按「手」解读：63864 × 100 = 6,386,400 股，流通股约 12.56 亿 → 换手率 0.51% ✓
按「股」解读：63864 股 → 换手率 0.0051%，与接口自报值相差 100 倍。
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider import akshare_fetcher, efinance_fetcher
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.efinance_fetcher import EfinanceFetcher
from data_provider.realtime_types import EM_VOLUME_LOT_SIZE, em_lots_to_shares

# efinance 文档字符串里的真实样本（手 / 元 / %）
LOTS = 63864
AMOUNT = 1.129957e10
TURNOVER_PCT = 0.51
FLOAT_SHARES = 1.256e9
OHLC = (1810.01, 1749.79, 1823.00, 1734.34)


def _em_daily_frame() -> pd.DataFrame:
    """东方财富 K 线原始返回：成交量列以「手」计。"""
    return pd.DataFrame(
        {
            "股票名称": ["贵州茅台"],
            "股票代码": ["600519"],
            "日期": ["2021-07-29"],
            "开盘": [OHLC[0]],
            "收盘": [OHLC[1]],
            "最高": [OHLC[2]],
            "最低": [OHLC[3]],
            "成交量": [LOTS],
            "成交额": [AMOUNT],
            "涨跌幅": [-2.06],
        }
    )


class TestLotSizeHelper(unittest.TestCase):
    def test_lot_size_matches_the_documented_conversion(self):
        self.assertEqual(EM_VOLUME_LOT_SIZE, 100)
        self.assertEqual(em_lots_to_shares(LOTS), LOTS * 100)
        self.assertEqual(em_lots_to_shares("63864"), LOTS * 100)
        self.assertEqual(em_lots_to_shares("63864.0"), LOTS * 100)

    def test_unparseable_values_fall_back_to_the_default(self):
        self.assertIsNone(em_lots_to_shares(None))
        self.assertIsNone(em_lots_to_shares(""))
        self.assertIsNone(em_lots_to_shares("--"))
        self.assertEqual(em_lots_to_shares(None, 0), 0)


class TestEfinanceVolumeUnit(unittest.TestCase):
    def setUp(self):
        # 模块级实时行情缓存必须清掉，否则会串到别的用例
        efinance_fetcher._realtime_cache["data"] = None
        efinance_fetcher._realtime_cache["timestamp"] = 0

    def test_daily_volume_is_stored_in_shares(self):
        out = EfinanceFetcher()._normalize_data(_em_daily_frame(), "600519")
        self.assertEqual(float(out["volume"].iloc[0]), LOTS * 100)

    def test_daily_volume_cross_checks_against_amount_and_turnover(self):
        """成交额/均价、换手率两条独立算式都应指向「股」而不是「手」。"""
        out = EfinanceFetcher()._normalize_data(_em_daily_frame(), "600519")
        stored = float(out["volume"].iloc[0])

        avg_price = sum(OHLC) / 4
        shares_from_amount = AMOUNT / avg_price
        self.assertLess(abs(stored - shares_from_amount) / shares_from_amount, 0.01)

        turnover_from_stored = stored / FLOAT_SHARES * 100
        self.assertAlmostEqual(turnover_from_stored, TURNOVER_PCT, delta=0.02)

    def test_realtime_volume_is_in_shares(self):
        fake_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "股票名称": ["贵州茅台"],
                "最新价": [1707.0],
                "涨跌幅": [-4.13],
                "涨跌额": [-73.5],
                "成交量": [49156],
                "成交额": [8487507456.0],
                "换手率": [0.39],
                "最高": [1768.0],
                "最低": [1703.8],
                "开盘": [1760.2],
            }
        )
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_realtime_quotes=lambda *a, **kw: fake_df)
        )
        fetcher = EfinanceFetcher()
        with patch.dict(sys.modules, {"efinance": fake_efinance}), patch.object(
            fetcher, "_set_random_user_agent", return_value=None
        ), patch.object(fetcher, "_enforce_rate_limit", return_value=None):
            quote = fetcher.get_realtime_quote("600519")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.volume, 49156 * 100)


class TestAkshareEastmoneyVolumeUnit(unittest.TestCase):
    def setUp(self):
        akshare_fetcher._realtime_cache["data"] = None
        akshare_fetcher._realtime_cache["timestamp"] = 0

    def _em_history_frame(self) -> pd.DataFrame:
        # ak.stock_zh_a_hist 返回的列名（中文），成交量同样来自东财 f5
        return pd.DataFrame(
            {
                "日期": ["2021-07-29"],
                "开盘": [OHLC[0]],
                "收盘": [OHLC[1]],
                "最高": [OHLC[2]],
                "最低": [OHLC[3]],
                "成交量": [LOTS],
                "成交额": [AMOUNT],
                "涨跌幅": [-2.06],
            }
        )

    def test_daily_volume_is_stored_in_shares(self):
        fake_akshare = types.SimpleNamespace(
            stock_zh_a_hist=lambda *a, **kw: self._em_history_frame()
        )
        fetcher = AkshareFetcher()
        with patch.dict(sys.modules, {"akshare": fake_akshare}), patch.object(
            fetcher, "_set_random_user_agent", return_value=None
        ), patch.object(fetcher, "_enforce_rate_limit", return_value=None):
            df = fetcher._fetch_stock_data_em("600519", "2021-07-01", "2021-07-31")

        self.assertEqual(float(df["成交量"].iloc[0]), LOTS * 100)

    def test_shared_normalize_does_not_rescale_volume(self):
        """回归护栏：换算必须在东财专用入口，不能落进共享的 ``_normalize_data``。

        新浪/腾讯历史接口的成交量本来就是「股」（akshare 文档：
        ``stock_zh_a_hist_tx`` 的 volume 统一为股），若把 ×100 放进共享的
        ``_normalize_data``，这两条链路会各自被放大 100 倍。
        """
        sina_like = pd.DataFrame(
            {
                "日期": ["2021-07-29"],
                "开盘": [OHLC[0]],
                "收盘": [OHLC[1]],
                "最高": [OHLC[2]],
                "最低": [OHLC[3]],
                "成交量": [LOTS * 100],  # 已经是「股」
                "成交额": [AMOUNT],
                "涨跌幅": [-2.06],
            }
        )
        out = AkshareFetcher()._normalize_data(sina_like, "600519")
        self.assertEqual(float(out["volume"].iloc[0]), float(LOTS * 100))

    def test_realtime_volume_is_in_shares(self):
        fake_df = pd.DataFrame(
            {
                "代码": ["600519"],
                "名称": ["贵州茅台"],
                "最新价": [1707.0],
                "涨跌幅": [-4.13],
                "涨跌额": [-73.5],
                "成交量": [49156],
                "成交额": [8487507456.0],
                "换手率": [0.39],
            }
        )
        fake_akshare = types.SimpleNamespace(stock_zh_a_spot_em=lambda *a, **kw: fake_df)
        fetcher = AkshareFetcher()
        with patch.dict(sys.modules, {"akshare": fake_akshare}), patch.object(
            fetcher, "_set_random_user_agent", return_value=None
        ), patch.object(fetcher, "_enforce_rate_limit", return_value=None):
            quote = fetcher._get_stock_realtime_quote_em("600519")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.volume, 49156 * 100)


class TestTushareLegacyRealtimeVolumeUnit(unittest.TestCase):
    """Tushare 旧版实时接口（``ts.get_realtime_quotes``）的成交量本就是「股」。

    该接口直连新浪 hq 行情源，vendored tushare 自己的字段说明写着
    ``8：volumn，成交量 maybe you need do volumn/100``——也就是原始值是「股」，
    除以 100 才得到「手」。此前代码写的是 ``// 100``，于是这条降级分支吐出的
    成交量比契约小 100 倍，与同文件的日线口径（``vol`` 手 → ×100 → 股）也互相矛盾。
    """

    @staticmethod
    def _fetcher():
        """构造一个只走旧版降级分支的 fetcher（Pro 接口按积分不足失败）。"""
        from unittest.mock import MagicMock

        from data_provider.tushare_fetcher import TushareFetcher

        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher._api.quotation.side_effect = Exception("quota")
        return fetcher

    def test_legacy_realtime_volume_is_not_divided(self):
        fake_df = pd.DataFrame(
            [
                {
                    "name": "平安银行",
                    "price": "10.94",
                    "pre_close": "10.88",
                    "volume": "100000",
                    "amount": "2000",
                    "high": "11.00",
                    "low": "10.80",
                    "open": "10.90",
                }
            ]
        )
        fake_tushare = types.SimpleNamespace(
            get_realtime_quotes=lambda symbols: fake_df
        )
        fetcher = self._fetcher()
        with patch.dict(sys.modules, {"tushare": fake_tushare}):
            quote = fetcher.get_realtime_quote("SZ000001")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.volume, 100000)
        self.assertNotEqual(quote.volume, 1000)


class TestDailyAndRealtimeShareOneUnit(unittest.TestCase):
    """契约本身：同一天的日线与实时行情必须是同一个单位，比值才有意义。

    这两条链路在真实配置下来自不同数据源（日线 efinance 东财，实时默认腾讯），
    因此用 efinance 日线 × 腾讯实时来断言，而不是同源自比 —— 同源同错会互相抵消。
    """

    def setUp(self):
        efinance_fetcher._realtime_cache["data"] = None
        efinance_fetcher._realtime_cache["timestamp"] = 0

    @staticmethod
    def _tencent_fields(shares: int) -> list:
        """构造腾讯/新浪实时行情字段列表（字段 6 为「股」口径成交量）。"""
        fields = [""] * 50
        fields[3] = "1749.79"    # 最新价
        fields[6] = str(shares)  # 成交量（股）
        fields[38] = "0.51"      # 换手率(%)
        fields[44] = "21980"     # 流通市值(亿) → 1.256e9 股 @1749.79
        return fields

    def test_daily_eastmoney_and_realtime_tencent_volumes_divide_to_one(self):
        from data_provider.akshare_fetcher import _normalize_tencent_volume

        daily_shares = float(
            EfinanceFetcher()._normalize_data(_em_daily_frame(), "600519")["volume"].iloc[0]
        )
        # 同一个交易日的真实成交量：6,386,400 股
        realtime_shares = _normalize_tencent_volume(self._tencent_fields(int(LOTS * 100)))

        self.assertEqual(realtime_shares, LOTS * 100)
        self.assertAlmostEqual(realtime_shares / daily_shares, 1.0, places=6)

    def test_volume_ratio_5d_is_not_inflated_by_the_realtime_overlay(self):
        from data_provider.akshare_fetcher import _normalize_tencent_volume
        from src.stock_analyzer import StockTrendAnalyzer

        daily_shares = float(
            EfinanceFetcher()._normalize_data(_em_daily_frame(), "600519")["volume"].iloc[0]
        )
        overlay_shares = float(_normalize_tencent_volume(self._tencent_fields(int(LOTS * 100))))

        n = 25
        df = pd.DataFrame(
            {
                "date": pd.date_range("2021-06-01", periods=n, freq="D"),
                "open": [1749.79] * n,
                "high": [1823.00] * n,
                "low": [1734.34] * n,
                "close": [1749.79] * (n - 1) + [1768.90],
                "volume": [daily_shares] * (n - 1) + [overlay_shares],
                "amount": [AMOUNT] * n,
            }
        )
        result = StockTrendAnalyzer().analyze(df, "600519")
        self.assertAlmostEqual(result.volume_ratio_5d, 1.0, places=6)
        self.assertNotAlmostEqual(result.volume_ratio_5d, 100.0, places=1)


if __name__ == "__main__":
    unittest.main()
